"""
github_profile.py
-----------------
Fetches a complete GitHub user snapshot in a single async call and returns
everything as one structured dict:

    {
        "profile": { username, name, bio, followers, public_repos, ... },
        "repos":   [ { name, description, stars, language, topics, ... }, ... ],
        "commits": [ { repo, sha, date, message, author }, ... ],  # top 100
        "meta":    { total_repos, total_commits, languages, fetch_duration, ... }
    }

Use this when you want a complete snapshot for display, storage, or a
one-off analysis. Use github_fetch.py instead when you want a pandas
DataFrame covering all commits across all repos.

Requires: aiohttp
    pip install aiohttp

Usage (async):
    from github_profile import fetch_github_profile
    data = await fetch_github_profile("torvalds", token="ghp_...")

Usage (sync / scripts):
    from github_profile import fetch_github_profile_sync
    data = fetch_github_profile_sync("torvalds", token="ghp_...")
"""

import asyncio
import math
import time
from datetime import datetime, timezone

import aiohttp

from _core import (
    BASE_URL,
    MAX_CONCURRENT,
    REPOS_PER_PAGE,
    RateLimitTracker,
    GitHubError,             # noqa: F401  (re-exported for callers)
    GitHubUserNotFoundError, # noqa: F401
    GitHubRateLimitError,    # noqa: F401
    GitHubNetworkError,      # noqa: F401
    get,
)

_TARGET_COMMITS = 100   # how many recent commits to return in total


# ── Profile ───────────────────────────────────────────────────────────────────

async def _fetch_profile(
    session:  aiohttp.ClientSession,
    username: str,
    tracker:  RateLimitTracker,
) -> dict:
    """
    Fetch user profile and validate the username in a single request.
    Called first so that a 404 fails fast before any other work starts.
    """
    data = await get(
        session, f"{BASE_URL}/users/{username}", tracker, username=username
    )
    return {
        "username":     data["login"],
        "name":         data.get("name"),
        "bio":          data.get("bio"),
        "location":     data.get("location"),
        "company":      data.get("company"),
        "blog":         data.get("blog") or None,
        "avatar_url":   data["avatar_url"],
        "followers":    data["followers"],
        "following":    data["following"],
        "public_repos": data["public_repos"],
        "created_at":   data["created_at"],
        "updated_at":   data["updated_at"],
    }


# ── Repos ─────────────────────────────────────────────────────────────────────

async def _fetch_repos_page(
    session:  aiohttp.ClientSession,
    username: str,
    page:     int,
    tracker:  RateLimitTracker,
) -> list[dict]:
    data = await get(
        session,
        f"{BASE_URL}/users/{username}/repos",
        tracker,
        params={
            "per_page": REPOS_PER_PAGE,
            "page":     page,
            "type":     "public",
            "sort":     "updated",
        },
    )
    return [
        {
            "name":        repo["name"],
            "description": repo.get("description"),
            "url":         repo["html_url"],
            "stars":       repo["stargazers_count"],
            "forks":       repo["forks_count"],
            "language":    repo.get("language"),
            "topics":      repo.get("topics", []),
            "archived":    repo["archived"],
            "created_at":  repo["created_at"],
            "updated_at":  repo["updated_at"],
        }
        for repo in data
    ]


async def _fetch_all_repos(
    session:     aiohttp.ClientSession,
    username:    str,
    tracker:     RateLimitTracker,
    total_repos: int,
) -> list[dict]:
    """Fire all repo pages concurrently now that we know the total count."""
    total_pages = math.ceil(total_repos / REPOS_PER_PAGE)
    pages = await asyncio.gather(*[
        _fetch_repos_page(session, username, p, tracker)
        for p in range(1, total_pages + 1)
    ])
    repos = [repo for page in pages for repo in page]
    repos.sort(key=lambda r: r["stars"], reverse=True)
    return repos


# ── Commits ───────────────────────────────────────────────────────────────────

async def _fetch_commits_single(
    session:          aiohttp.ClientSession,
    username:         str,
    repo_name:        str,
    commits_per_repo: int,
    tracker:          RateLimitTracker,
    semaphore:        asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        data = await get(
            session,
            f"{BASE_URL}/repos/{username}/{repo_name}/commits",
            tracker,
            params={"per_page": commits_per_repo},
        )

    return [
        {
            "repo":    repo_name,
            "sha":     c["sha"][:7],
            "date":    c["commit"]["author"]["date"],           # ISO 8601 string
            "message": c["commit"]["message"].splitlines()[0],  # subject line only
            "author":  c["commit"]["author"]["name"],
        }
        for c in data
    ]


async def _fetch_recent_commits(
    session:  aiohttp.ClientSession,
    username: str,
    repos:    list[dict],
    tracker:  RateLimitTracker,
) -> list[dict]:
    """
    Fetch enough commits per repo to guarantee we can surface the
    _TARGET_COMMITS most recent commits globally after sorting.

    Strategy: oversample by 2× so empty/stale repos don't leave us short.
    Fetches between 5 and 100 commits per repo.

    Examples:
        1  repo  → 100 per repo (full allowance)
        10 repos → 20  per repo → 200 candidates → slice top 100
        50 repos → 10  per repo → 500 candidates → slice top 100
       100 repos → 5   per repo → 500 candidates → slice top 100
    """
    n = max(len(repos), 1)
    commits_per_repo = min(100, max(5, math.ceil(_TARGET_COMMITS / n) * 2))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = await asyncio.gather(*[
        _fetch_commits_single(
            session, username, repo["name"],
            commits_per_repo, tracker, semaphore,
        )
        for repo in repos
    ])

    all_commits = [c for repo_commits in results for c in repo_commits]
    all_commits.sort(key=lambda c: c["date"], reverse=True)
    return all_commits[:_TARGET_COMMITS]


# ── Languages (bytes per repo) ────────────────────────────────────────────────
# Separate from the main pipeline because it costs one extra API call per
# repo — call this only when you need byte-level language data (e.g. for
# analytics.top_languages()), not on every fetch_github_profile() run.

async def _fetch_repo_languages(
    session:   aiohttp.ClientSession,
    username:  str,
    repo_name: str,
    tracker:   RateLimitTracker,
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict[str, int]]:
    """
    Fetch the language byte breakdown for one repo.

    GitHub's /languages endpoint returns bytes of code per language,
    e.g. {"Python": 48213, "HTML": 1022} — a much finer-grained signal
    than the repo object's single "language" field, which only reports
    the one most-used language and ignores everything else.
    """
    async with semaphore:
        data = await get(
            session,
            f"{BASE_URL}/repos/{username}/{repo_name}/languages",
            tracker,
        )
    return repo_name, (data or {})


async def fetch_repo_languages_async(
    username:   str,
    repo_names: list[str],
    token:      str = None,
) -> dict[str, dict[str, int]]:
    """
    Fetch language byte breakdowns for multiple repos concurrently.

    Args:
        username:   GitHub username (repo owner).
        repo_names: list of repo names to fetch language data for.
        token:      Personal access token. Strongly recommended — this
                    issues one additional API request per repo on top
                    of whatever has already been fetched.

    Returns:
        Dict mapping repo name -> {language: byte_count}.
        Repos with no detected code (e.g. empty repos) map to {}.

    Raises:
        GitHubRateLimitError, GitHubNetworkError: as with other fetch functions.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tracker   = RateLimitTracker()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(*[
            _fetch_repo_languages(session, username, name, tracker, semaphore)
            for name in repo_names
        ])

    return dict(results)


def fetch_repo_languages_sync(
    username:   str,
    repo_names: list[str],
    token:      str = None,
) -> dict[str, dict[str, int]]:
    """Blocking wrapper around fetch_repo_languages_async() for scripts."""
    return asyncio.run(fetch_repo_languages_async(username, repo_names, token))


# ── Entry point ───────────────────────────────────────────────────────────────

async def fetch_github_profile(username: str, token: str = None) -> dict:
    """
    Fetch a complete GitHub user snapshot and return it as a structured dict.

    The profile is fetched first and validates the username; if it raises,
    no further requests are made.

    Args:
        username: GitHub username to look up.
        token:    Personal access token. Strongly recommended —
                  raises rate limit from 60 → 5000 req/hr.
                  Generate at: github.com → Settings → Developer settings
                  → Fine-grained tokens (no scopes needed for public data).

    Returns:
        {
            "profile": {
                "username", "name", "bio", "location", "company", "blog",
                "avatar_url", "followers", "following",
                "public_repos", "created_at", "updated_at"
            },
            "repos": [                              # all public repos
                {
                    "name", "description", "url",
                    "stars", "forks", "language",
                    "topics", "archived",
                    "created_at", "updated_at"
                },
                ...                                 # sorted by stars desc
            ],
            "commits": [                            # top 100 most recent
                { "repo", "sha", "date", "message", "author" },
                ...
            ],
            "meta": {
                "total_repos":         int,
                "total_commits":       int,
                "repos_with_commits":  int,
                "languages":           list[str],   # unique, sorted
                "fetch_duration_secs": float,
                "fetched_at":          str,          # ISO 8601 UTC
                "api_calls_remaining": int,
            }
        }

        The "date" field in each commit is a raw ISO 8601 string.
        Pass the commits list to timestamps.enrich_commits() to add
        hour, day_of_week, month, and other analysis fields.

    Raises:
        GitHubUserNotFoundError: username does not exist on GitHub.
        GitHubRateLimitError:    rate limit hit; .reset_at is Unix timestamp.
        GitHubNetworkError:      connection failure or unexpected HTTP status.
    """
    started_at = time.monotonic()

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tracker = RateLimitTracker()

    async with aiohttp.ClientSession(headers=headers) as session:

        # Profile first — validates username and gives us public_repos count
        profile = await _fetch_profile(session, username, tracker)

        # Repos and commits fetched after profile; commits depend on repos list
        repos = await _fetch_all_repos(
            session, username, tracker, profile["public_repos"]
        )
        commits = await _fetch_recent_commits(session, username, repos, tracker)

    duration = time.monotonic() - started_at

    return {
        "profile": profile,
        "repos":   repos,
        "commits": commits,
        "meta": {
            "total_repos":         len(repos),
            "total_commits":       len(commits),
            "repos_with_commits":  len({c["repo"] for c in commits}),
            "languages":           sorted(
                                       r["language"] for r in repos
                                       if r["language"] is not None
                                   ),
            "fetch_duration_secs": round(duration, 2),
            "fetched_at":          datetime.now(timezone.utc).isoformat(),
            "api_calls_remaining": tracker.remaining,
        },
    }


# ── Sync wrapper ──────────────────────────────────────────────────────────────

def fetch_github_profile_sync(username: str, token: str = None) -> dict:
    """
    Blocking wrapper around fetch_github_profile for scripts and plain REPLs.

    Do NOT call this inside an already-running event loop (FastAPI routes,
    Jupyter notebooks, async test suites). In those contexts, use:

        data = await fetch_github_profile(username, token)
    """
    return asyncio.run(fetch_github_profile(username, token))


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python github_profile.py <username> [token]")
        sys.exit(1)

    try:
        data = fetch_github_profile_sync(
            sys.argv[1],
            token=sys.argv[2] if len(sys.argv) > 2 else None,
        )
    except (GitHubUserNotFoundError, GitHubRateLimitError, GitHubNetworkError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(data, indent=2, default=str))
