"""
_core.py
--------
Shared infrastructure for github_fetch.py and github_profile.py.
Not intended to be imported by user code directly.

Contains:
  - Typed exception hierarchy
  - Shared constants
  - RateLimitTracker  — async-safe rate limit state shared across coroutines
  - _get()            — single HTTP GET with consistent error handling
"""

import asyncio
import time
from datetime import datetime, timezone

import aiohttp


# ── Exceptions ────────────────────────────────────────────────────────────────

class GitHubError(Exception):
    """Base class for all errors raised by this package."""

class GitHubUserNotFoundError(GitHubError):
    """Raised when the requested username does not exist on GitHub."""

class GitHubRateLimitError(GitHubError):
    """
    Raised when the API rate limit is exceeded.
    .reset_at holds the Unix timestamp of when the window resets.
    """
    def __init__(self, reset_at: int):
        self.reset_at = reset_at
        wait = max(reset_at - time.time(), 0)
        reset_str = datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat()
        super().__init__(
            f"Rate limit exceeded. Resets in {wait:.0f}s (at {reset_str})."
        )

class GitHubNetworkError(GitHubError):
    """Raised on connection failures or unexpected HTTP status codes."""


# ── Constants ─────────────────────────────────────────────────────────────────

BASE_URL          = "https://api.github.com"
REPOS_PER_PAGE    = 100   # GitHub's maximum page size for repos
MAX_CONCURRENT    = 10    # simultaneous open connections to the GitHub API
RATE_LIMIT_BUFFER = 50    # pause when fewer than this many calls remain


# ── Rate limit tracker ────────────────────────────────────────────────────────

class RateLimitTracker:
    """
    Async-safe shared state for GitHub rate limit headers.

    Every response passes its headers to .update().
    Every outgoing request calls await .check() first, which suspends
    the coroutine if the remaining quota is critically low.
    """

    def __init__(self):
        self.remaining = 5000
        self.reset_at  = 0
        self._lock     = asyncio.Lock()

    def update(self, headers: "aiohttp.CIMultiDictProxy"):
        if (r := headers.get("X-RateLimit-Remaining")) is not None:
            self.remaining = int(r)
        if (t := headers.get("X-RateLimit-Reset")) is not None:
            self.reset_at = int(t)

    async def check(self):
        """Suspend until the rate limit window resets if quota is nearly gone."""
        async with self._lock:
            if self.remaining <= RATE_LIMIT_BUFFER:
                wait = max(self.reset_at - time.time(), 0) + 2  # +2s safety margin
                if wait > 0:
                    print(
                        f"\n  ⚠  Rate limit low ({self.remaining} remaining). "
                        f"Sleeping {wait:.0f}s until reset..."
                    )
                    await asyncio.sleep(wait)


# ── HTTP helper ───────────────────────────────────────────────────────────────

async def get(
    session:  aiohttp.ClientSession,
    url:      str,
    tracker:  RateLimitTracker,
    params:   dict = None,
    username: str  = None,
) -> dict | list:
    """
    Perform a single authenticated GET request with consistent error handling.

    Args:
        session:  active aiohttp session (headers already set by caller).
        url:      fully-qualified GitHub API URL.
        tracker:  shared RateLimitTracker; updated from every response.
        params:   optional query-string dict.
        username: if provided, used to produce a readable 404 message.

    Returns:
        Parsed JSON body as a dict or list.

    Raises:
        GitHubUserNotFoundError: HTTP 404.
        GitHubRateLimitError:    HTTP 403 or 429.
        GitHubNetworkError:      connection failure or other non-OK status.
    """
    await tracker.check()

    try:
        async with session.get(url, params=params) as resp:
            tracker.update(resp.headers)

            if resp.status == 404:
                label = f"'{username}'" if username else url
                raise GitHubUserNotFoundError(f"GitHub user {label} not found.")

            if resp.status in (403, 429):
                raise GitHubRateLimitError(tracker.reset_at)

            if resp.status == 409:
                # Empty repository — GitHub returns 409 Conflict for repos
                # that exist but have no commits yet. Not an error; return [].
                return []

            if not resp.ok:
                raise GitHubNetworkError(
                    f"Unexpected HTTP {resp.status} from {url}"
                )

            return await resp.json()

    except aiohttp.ClientError as exc:
        raise GitHubNetworkError(f"Network error fetching {url}: {exc}") from exc
