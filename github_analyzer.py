"""
github_analyzer.py
------------------
Object-oriented façade over the entire GitHub analysis pipeline.

GitHubAnalyzer wraps data_fetcher, analytics, and insights behind a single
class: construct it once with a username, then call whichever get_*()
method you need. The underlying GitHub snapshot (profile + repos + top 100
commits) is fetched once — lazily, on first method call — and cached on
the instance, so calling several methods back to back never re-fetches
from the GitHub API.

Why a class instead of calling the standalone functions directly:
    Every function built so far (commits_to_dataframe, coding_consistency,
    top_languages, build_commit_heatmap, generate_fun_facts) takes the
    *same* underlying data dict as input. Calling them independently means
    either re-fetching that dict repeatedly or manually threading a `data`
    variable through every call site yourself. This class does that
    threading once, internally, via lazy caching, and exposes only the
    four outputs a caller actually wants — one method per output.

Usage:
    from github_analyzer import GitHubAnalyzer

    analyzer = GitHubAnalyzer("torvalds")

    breakdown = analyzer.get_language_breakdown()   # [('C', 68.2), ...]
    stats     = analyzer.get_stats()                # {"longest_streak_days": 12, ...}
    heatmap   = analyzer.get_heatmap_data()          # 7×24 DataFrame
    facts     = analyzer.get_fun_facts()             # ["Your code peaks at...", ...]
"""

import pandas as pd

from data_fetcher import fetch_user_profile, fetch_language_bytes
from github_profile import fetch_github_profile_sync
from analytics import (
    commits_to_dataframe,
    coding_consistency,
    top_languages,
    build_commit_heatmap,
)
from insights import generate_fun_facts


class GitHubAnalyzer:
    """
    Unified façade over the GitHub analysis pipeline for a single user.

    All four get_*() methods share one underlying data fetch: the first
    method called triggers the network request (via data_fetcher), and
    every method after that — on this same instance — reuses the cached
    result instead of fetching again.

    Attributes:
        username (str): the GitHub username this instance analyzes.

    Example:
        >>> from github_analyzer import GitHubAnalyzer
        >>> analyzer = GitHubAnalyzer("torvalds")
        >>> analyzer.get_stats()["longest_streak_days"]
        12
        >>> analyzer.get_language_breakdown()
        [('C', 68.2), ('C++', 14.1), ('Shell', 9.7), ('Python', 5.0), ('Makefile', 3.0)]
        >>> analyzer.get_fun_facts()[0]
        "Your code peaks at 3 PM (you're a daytime grinder)."
    """

    def __init__(
        self,
        username:           str,
        token:              str | None = None,
        tz:                 str = "UTC",
        use_language_bytes: bool = False,
    ) -> None:
        """
        Args:
            username: GitHub username to analyze.
            token:    Optional explicit API token, overriding the .env
                      default. If omitted (the normal case), the token is
                      loaded automatically from GITHUB_TOKEN in .env — same
                      behavior as calling data_fetcher.fetch_user_profile()
                      directly. Only pass this if you need a different
                      token than the one configured in .env (e.g. testing
                      with multiple tokens in the same process).
            tz:       IANA timezone name applied to every hour/weekday
                      calculation across all four methods (e.g.
                      "America/New_York"). Defaults to "UTC".
            use_language_bytes: if True, get_language_breakdown() and
                      get_fun_facts() fetch byte-accurate language data
                      (data_fetcher.fetch_language_bytes()) instead of
                      falling back to a repo-count ranking. This costs one
                      extra API call per repo, so it defaults to False.

        Note:
            Constructing an instance never makes a network call by itself —
            all fetching is deferred to the first get_*() call (see
            "Lazy caches" below).
        """
        self.username = username
        self._token = token
        self._tz = tz
        self._use_language_bytes = use_language_bytes

        # Lazy caches — None until first computed, then reused. Each is
        # populated by its own _ensure_*()/get_*() method, never eagerly,
        # so creating a GitHubAnalyzer is always a cheap, network-free call.
        self._data:           dict | None           = None
        self._language_bytes: dict | None           = None
        self._stats:          dict | None           = None
        self._heatmap:        pd.DataFrame | None   = None
        self._fun_facts:      list[str] | None      = None
        self._commits_df:     pd.DataFrame | None   = None

    # ── Internal lazy loaders ────────────────────────────────────────────────

    def _ensure_data(self) -> dict:
        """
        Fetch the full profile snapshot (profile + repos + top 100 commits)
        on first call; every subsequent call reuses the cached dict.
        """
        if self._data is None:
            if self._token:
                # Explicit token override — bypass data_fetcher's .env loading
                self._data = fetch_github_profile_sync(self.username, token=self._token)
            else:
                # Normal path — data_fetcher loads GITHUB_TOKEN from .env itself
                self._data = fetch_user_profile(self.username)
        return self._data

    def _ensure_language_bytes(self) -> dict[str, dict[str, int]] | None:
        """
        Fetch per-repo language byte breakdowns on first call, only if
        use_language_bytes=True was set at construction. Returns None
        otherwise, which signals downstream functions to use their
        repo-count fallback instead.
        """
        if not self._use_language_bytes:
            return None
        if self._language_bytes is None:
            data = self._ensure_data()
            self._language_bytes = fetch_language_bytes(self.username, repos=data["repos"])
        return self._language_bytes

    # ── Public methods ───────────────────────────────────────────────────────

    def get_profile(self) -> dict:
        """
        Basic profile information for this user.

        Returns:
            dict with keys:
                "username", "name", "bio", "location", "company", "blog",
                "avatar_url", "followers", "following", "public_repos",
                "created_at", "updated_at"
            See github_profile._fetch_profile() for exact field definitions.
        """
        data = self._ensure_data()
        return data["profile"]

    def get_repos(self) -> list[dict]:
        """
        All public repositories for this user, sorted by stars descending.

        Unlike get_language_breakdown() (which returns a ranked summary),
        this returns the raw per-repo records — needed for anything that
        wants fields get_language_breakdown() doesn't carry, like stars,
        description, or topics (e.g. charts.create_top_repos_bar_chart()).

        Returns:
            list[dict], each with keys:
                "name", "description", "url", "stars", "forks",
                "language", "topics", "archived", "created_at", "updated_at"
        """
        data = self._ensure_data()
        return data["repos"]

    def get_language_breakdown(self, top_n: int = 5) -> list[tuple[str, float]]:
        """
        Rank this user's top languages by bytes committed, or by repo count
        if use_language_bytes=False (the default).

        Args:
            top_n: how many languages to return. Defaults to 5.

        Returns:
            list[tuple[str, float]]: up to `top_n` (language, percentage)
            pairs, sorted descending by share. Empty list if no repo has a
            detected language. See analytics.top_languages() for the exact
            ranking and fallback rules.
        """
        data = self._ensure_data()
        lang_bytes = self._ensure_language_bytes()
        ranked, _ = top_languages(data["repos"], language_bytes=lang_bytes, top_n=top_n)
        return ranked

    def get_stats(self) -> dict:
        """
        Calculate coding consistency stats from this user's recent commits.

        Returns:
            dict with keys:
                "longest_streak_days"     : int
                "average_commits_per_day" : float
                "busiest_hour"            : int | None
                "codes_on_weekends"       : bool
            See analytics.coding_consistency() for exact definitions.
            All-zero/None/False values if the user has no commits.
        """
        if self._stats is None:
            data = self._ensure_data()
            self._stats = coding_consistency(data["commits"], tz=self._tz)
        return self._stats

    def get_heatmap_data(self) -> pd.DataFrame:
        """
        Build the 7×24 day-of-week × hour-of-day commit count grid.

        Returns:
            pd.DataFrame, shape (7, 24): index is day names ("Monday" …
            "Sunday"), columns are hours 0–23, values are commit counts.
            Ready to pass straight to Plotly. See
            analytics.build_commit_heatmap() for the exact structure.
        """
        if self._heatmap is None:
            data = self._ensure_data()
            commit_times = [c["date"] for c in data["commits"]]
            self._heatmap = build_commit_heatmap(commit_times, tz=self._tz)
        return self._heatmap

    def get_fun_facts(self) -> list[str]:
        """
        Generate Spotify-Wrapped-style fun facts about this user.

        Returns:
            list[str], typically 5–8 human-readable sentences (fewer if the
            user has little or no commit history). See
            insights.generate_fun_facts() for the exact generation rules.
        """
        if self._fun_facts is None:
            data = self._ensure_data()
            lang_bytes = self._ensure_language_bytes()
            self._fun_facts = generate_fun_facts(data, language_bytes=lang_bytes, tz=self._tz)
        return self._fun_facts

    # ── Bonus method ─────────────────────────────────────────────────────────
    # Not in the original four, but "process it into DataFrames" implied a
    # flat per-commit table should be reachable too — every other method
    # already builds on it internally (coding_consistency, the heatmap, and
    # the fun facts all derive from the same commit list), so exposing it
    # directly costs nothing extra and is useful for ad-hoc exploration.

    def get_commits_dataframe(self) -> pd.DataFrame:
        """
        One-row-per-commit DataFrame: date, hour, day_of_week, repo_name,
        language. See analytics.commits_to_dataframe() for exact columns.

        Returns:
            pd.DataFrame with columns: "date", "hour", "day_of_week",
            "repo_name", "language". Empty DataFrame if no commits.
        """
        if self._commits_df is None:
            data = self._ensure_data()
            self._commits_df = commits_to_dataframe(data, tz=self._tz)
        return self._commits_df

    def __repr__(self) -> str:
        cached = self._data is not None
        return f"GitHubAnalyzer(username={self.username!r}, data_fetched={cached})"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python github_analyzer.py <username>")
        sys.exit(1)

    analyzer = GitHubAnalyzer(sys.argv[1])

    print(f"\n=== {analyzer!r} ===\n")

    print("Language breakdown:")
    for lang, pct in analyzer.get_language_breakdown():
        print(f"  {lang:15s} {pct:>5.1f}%")

    print("\nStats:")
    for key, value in analyzer.get_stats().items():
        print(f"  {key}: {value}")

    print("\nHeatmap shape:", analyzer.get_heatmap_data().shape)

    print("\nFun facts:")
    for fact in analyzer.get_fun_facts():
        print(f"  • {fact}")
