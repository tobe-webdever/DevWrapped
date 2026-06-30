"""
data_fetcher.py
---------------
Public-facing entry point for the GitHub analysis pipeline.

Wraps github_profile.fetch_github_profile_sync() and github_fetch.run()
behind two clean, simply-typed functions that load the API token from a
.env file automatically — no token wiring required in calling code.

Setup:
    1. pip install python-dotenv aiohttp pandas
    2. Copy .env.example to .env and fill in your token (see .env.example)
    3. Import and call:

        from data_fetcher import fetch_user_profile, fetch_commits

        profile = fetch_user_profile("torvalds")
        df      = fetch_commits("torvalds")

Environment variables (in .env):
    GITHUB_TOKEN   Personal access token. Optional but strongly recommended —
                   raises the rate limit from 60 → 5000 requests/hour.
                   Generate one at:
                   github.com → Settings → Developer settings
                   → Fine-grained tokens → Generate new token
                   (no scopes required for public data)
"""

import asyncio
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from _core import GitHubError, GitHubUserNotFoundError, GitHubRateLimitError, GitHubNetworkError
from github_profile import fetch_github_profile_sync, fetch_repo_languages_sync
from github_fetch import run as _run_bulk


# ── Token loading ─────────────────────────────────────────────────────────────

# Walk up from this file's directory to find the nearest .env file.
# This means data_fetcher.py works whether it lives in the project root
# or a subdirectory (e.g. src/), without hardcoding any path.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

def _get_token() -> str | None:
    """
    Return the GitHub API token from the environment.

    load_dotenv() at import time populates os.environ from .env, so
    os.getenv() here will find the value whether it was set in the shell
    or only in the .env file.

    Returns:
        Token string if GITHUB_TOKEN is set and non-empty, else None.
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return token if token else None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_user_profile(username: str) -> dict:
    """
    Fetch a complete GitHub user snapshot for `username`.

    Retrieves the user's profile, all public repositories, and the 100 most
    recent commits across those repositories in a single async run. The API
    token is loaded automatically from the .env file.

    Args:
        username: GitHub username to look up (e.g. "torvalds").

    Returns:
        A dict with four top-level keys:

        "profile" : dict
            Basic user information.
                "username"     : str
                "name"         : str | None
                "bio"          : str | None
                "location"     : str | None
                "company"      : str | None
                "blog"         : str | None
                "avatar_url"   : str
                "followers"    : int
                "following"    : int
                "public_repos" : int
                "created_at"   : str   ISO 8601 UTC
                "updated_at"   : str   ISO 8601 UTC

        "repos" : list[dict]
            All public repositories, sorted by stars descending.
            Each dict contains:
                "name", "description", "url", "stars", "forks",
                "language", "topics", "archived", "created_at", "updated_at"

        "commits" : list[dict]
            The 100 most recent commits across all repos, sorted by date.
            Each dict contains:
                "repo", "sha", "date" (ISO 8601), "message", "author"
            Pass this list to timestamps.enrich_commits() to add
            hour, day_of_week, month, and other analysis fields.

        "meta" : dict
            Fetch statistics.
                "total_repos"         : int
                "total_commits"       : int
                "repos_with_commits"  : int
                "languages"           : list[str]
                "fetch_duration_secs" : float
                "fetched_at"          : str   ISO 8601 UTC
                "api_calls_remaining" : int

    Raises:
        GitHubUserNotFoundError : `username` does not exist on GitHub.
        GitHubRateLimitError    : API rate limit hit; .reset_at is Unix ts.
        GitHubNetworkError      : Connection failure or unexpected HTTP status.

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> data = fetch_user_profile("torvalds")
        >>> data["profile"]["followers"]
        192847
        >>> data["repos"][0]["name"]
        'linux'
        >>> data["meta"]["fetch_duration_secs"]
        1.83
    """
    return fetch_github_profile_sync(username, token=_get_token())


def fetch_commits(username: str) -> pd.DataFrame:
    """
    Fetch all public repos and up to 100 commits per repo for `username`.

    Runs the full async bulk pipeline and returns a pandas DataFrame with
    per-commit rows and repo metadata joined in. The API token is loaded
    automatically from the .env file.

    Args:
        username: GitHub username to analyse (e.g. "torvalds").

    Returns:
        pd.DataFrame with one row per commit and the following columns:

        From the repository:
            "repo"        : str    repository name
            "description" : str    repository description (may be None)
            "stars"       : int    stargazer count
            "forks"       : int    fork count
            "language"    : str    primary language (may be None)
            "topics"      : list   repository topic tags
            "archived"    : bool   whether the repo is archived
            "updated_at"  : str    ISO 8601 last-updated timestamp

        Per commit:
            "sha"         : str    short (7-char) commit hash
            "date"        : str    ISO 8601 UTC commit timestamp
            "author"      : str    commit author name
            "message"     : str    first line of the commit message

        Sorted by (repo asc, date desc). The "date" column contains raw
        ISO 8601 strings. Pass the DataFrame to timestamps.enrich_dataframe()
        to add hour, day_of_week, month, and other analysis columns.

        Returns an empty DataFrame (0 rows, 0 columns) if the user has no
        public repositories or no commits.

    Raises:
        GitHubUserNotFoundError : `username` does not exist on GitHub.
        GitHubRateLimitError    : API rate limit hit; .reset_at is Unix ts.
        GitHubNetworkError      : Connection failure or unexpected HTTP status.

    Example:
        >>> from data_fetcher import fetch_commits
        >>> df = fetch_commits("torvalds")
        >>> df.shape
        (800, 12)
        >>> df.columns.tolist()
        ['repo', 'description', 'stars', 'forks', 'language', 'topics',
         'archived', 'updated_at', 'sha', 'date', 'author', 'message']
        >>> df[df["language"] == "C"].shape
        (432, 12)
    """
    return asyncio.run(_run_bulk(username, token=_get_token()))


def fetch_language_bytes(username: str, repos: list[dict] | None = None) -> dict[str, dict[str, int]]:
    """
    Fetch per-repo language byte breakdowns for `username`.

    Issues one extra API request per repo (GitHub's /languages endpoint) on
    top of whatever has already been fetched. Call this only when byte-level
    language data is actually needed — e.g. for analytics.top_languages() —
    rather than on every run, since it doubles the request count for
    accounts with many repos.

    Args:
        username: GitHub username.
        repos:    Optional pre-fetched repo list (e.g. data["repos"] from
                  fetch_user_profile(), or the repo columns from
                  fetch_commits()) to avoid re-fetching the repo list.
                  If omitted, repos are fetched fresh via fetch_user_profile().

    Returns:
        Dict mapping repo name -> {language: byte_count}.
        Repos with no detected code (e.g. empty repos) map to {}.

    Raises:
        GitHubUserNotFoundError : `username` does not exist on GitHub.
        GitHubRateLimitError    : API rate limit hit; .reset_at is Unix ts.
        GitHubNetworkError      : Connection failure or unexpected HTTP status.

    Example:
        >>> from data_fetcher import fetch_user_profile, fetch_language_bytes
        >>> data = fetch_user_profile("torvalds")
        >>> lang_bytes = fetch_language_bytes("torvalds", repos=data["repos"])
        >>> lang_bytes["linux"]
        {'C': 23145102, 'Assembly': 421532, 'Makefile': 88210}
    """
    if repos is None:
        data = fetch_user_profile(username)
        repos = data["repos"]

    repo_names = [r["name"] for r in repos]
    return fetch_repo_languages_sync(username, repo_names, token=_get_token())


# ── Module-level re-exports ───────────────────────────────────────────────────
# Re-export the exception types so callers only need one import for both
# the functions and the exceptions they need to catch.

__all__ = [
    "fetch_user_profile",
    "fetch_commits",
    "fetch_language_bytes",
    "GitHubError",
    "GitHubUserNotFoundError",
    "GitHubRateLimitError",
    "GitHubNetworkError",
]
