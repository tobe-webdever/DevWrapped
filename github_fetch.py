"""
github_fetch.py
---------------
Async GitHub data pipeline — bulk analysis orientation.

Fetches all public repos for a user, then all commits for every repo
(up to 100 per repo), and returns the result as a single pandas DataFrame
with repo metadata joined in.

Use this when you want to analyse commit patterns across an entire account
(heatmaps, language breakdowns, contributor stats).

Use github_profile.py instead when you want a structured dict snapshot
(profile + repos + recent commits) for display or storage.

Requires: aiohttp, pandas
    pip install aiohttp pandas

Usage:
    import asyncio
    from github_fetch import run

    df = asyncio.run(run("torvalds", token="ghp_..."))
    print(df.head())
    print(df.dtypes)
"""

import asyncio
import math

import aiohttp
import pandas as pd

from _core import (
    BASE_URL,
    MAX_CONCURRENT,
    REPOS_PER_PAGE,
    RateLimitTracker,
    GitHubUserNotFoundError,
    get,
)

COMMITS_PER_REPO = 100   # commits fetched per repo


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
        username=username,
    )
    return [
        {
            "name":        repo["name"],
            "description": repo.get("description"),
            "stars":       repo["stargazers_count"],
            "forks":       repo["forks_count"],
            "language":    repo.get("language"),
            "topics":      repo.get("topics", []),
            "archived":    repo["archived"],
            "updated_at":  repo["updated_at"],
        }
        for repo in data
    ]


async def _fetch_all_repos(
    session:  aiohttp.ClientSession,
    username: str,
    tracker:  RateLimitTracker,
) -> list[dict]:
    """
    Determine total page count from the user endpoint, then fire all
    repo pages concurrently.
    """
    user_data = await get(
        session, f"{BASE_URL}/users/{username}", tracker, username=username
    )
    total_repos  = user_data["public_repos"]
    total_pages  = math.ceil(total_repos / REPOS_PER_PAGE)

    print(f"  {username}: {total_repos} public repos → {total_pages} page(s)")

    pages = await asyncio.gather(*[
        _fetch_repos_page(session, username, p, tracker)
        for p in range(1, total_pages + 1)
    ])
    return [repo for page in pages for repo in page]


# ── Commits ───────────────────────────────────────────────────────────────────

async def _fetch_commits_single(
    session:   aiohttp.ClientSession,
    owner:     str,
    repo_name: str,
    tracker:   RateLimitTracker,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """
    Fetch the last COMMITS_PER_REPO commits for one repo.
    The semaphore keeps at most MAX_CONCURRENT requests open at a time.
    """
    async with semaphore:
        data = await get(
            session,
            f"{BASE_URL}/repos/{owner}/{repo_name}/commits",
            tracker,
            params={"per_page": COMMITS_PER_REPO},
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


async def _fetch_all_commits(
    session:  aiohttp.ClientSession,
    owner:    str,
    repos:    list[dict],
    tracker:  RateLimitTracker,
) -> list[dict]:
    """
    Fetch commits for all repos concurrently, bounded by MAX_CONCURRENT.
    Prints a live progress line per repo as each completes.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_and_report(repo):
        commits = await _fetch_commits_single(
            session, owner, repo["name"], tracker, semaphore
        )
        print(
            f"  ✓  {repo['name']:40s}  {len(commits):>3} commits  "
            f"({tracker.remaining} API calls left)"
        )
        return commits

    results = await asyncio.gather(*[fetch_and_report(r) for r in repos])
    return [c for repo_commits in results for c in repo_commits]


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(username: str, token: str = None) -> pd.DataFrame:
    """
    Full pipeline: repos → commits → pandas DataFrame.

    Args:
        username: GitHub username to analyse.
        token:    Personal access token. Strongly recommended —
                  raises rate limit from 60 → 5000 req/hr.
                  Generate at: github.com → Settings → Developer settings
                  → Fine-grained tokens (no scopes needed for public data).

    Returns:
        pd.DataFrame with columns:
            repo, description, stars, forks, language, topics, archived,
            updated_at, sha, date (ISO string), message, author

        The "date" column contains raw ISO 8601 strings. Pass the DataFrame
        to timestamps.enrich_dataframe() to add hour, day_of_week, month, etc.

    Raises:
        GitHubUserNotFoundError: username does not exist.
        GitHubRateLimitError:    rate limit hit mid-run.
        GitHubNetworkError:      connection or HTTP failure.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tracker = RateLimitTracker()

    async with aiohttp.ClientSession(headers=headers) as session:
        print(f"\n── Fetching repos for '{username}' ──")
        repos = await _fetch_all_repos(session, username, tracker)
        print(f"  Found {len(repos)} repos\n")

        print(f"── Fetching commits ({COMMITS_PER_REPO}/repo) ──")
        commits = await _fetch_all_commits(session, username, repos, tracker)
        print(f"\n  Done — {len(commits):,} total commits fetched\n")

    if not commits:
        return pd.DataFrame()

    df_commits = pd.DataFrame(commits)
    df_repos   = pd.DataFrame(repos).rename(columns={"name": "repo"})
    df         = df_commits.merge(df_repos, on="repo", how="left")

    # Reorder columns: repo metadata first, then per-commit fields
    df = df[[
        "repo", "description", "stars", "forks", "language", "topics",
        "archived", "updated_at", "sha", "date", "author", "message",
    ]]
    return df.sort_values(["repo", "date"], ascending=[True, False]).reset_index(drop=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python github_fetch.py <username> [token]")
        sys.exit(1)

    df = asyncio.run(run(sys.argv[1], token=sys.argv[2] if len(sys.argv) > 2 else None))
    print(df.to_string(max_rows=20))
    print(f"\nShape:      {df.shape}")
    print(f"Date range: {df['date'].min()} → {df['date'].max()}")
