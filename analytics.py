"""
analytics.py
------------
Turns the raw dict returned by fetch_user_profile() / fetch_github_profile()
into flat, analysis-ready pandas structures.

Responsibilities, kept separate:
  1. commits_to_dataframe()  — reshape: dict → one-row-per-commit DataFrame
  2. commits_per_language()  — aggregate: DataFrame → commit counts by language
  3. coding_consistency()    — aggregate: commits → streaks, averages, etc.
  4. top_languages()         — aggregate: repos → top-N languages by bytes/count
  5. build_commit_heatmap()  — aggregate: commit times → 7×24 day×hour grid

Why this is its own file rather than added to timestamps.py or data_fetcher.py:
  - timestamps.py only knows about parsing single timestamp strings; it has
    no concept of "repo", "language", or the nested dict shape.
  - data_fetcher.py is the fetch layer (network calls); this is the transform
    layer (pure functions, no I/O). Keeping fetch and transform separate
    means these functions are trivially testable with a fixture dict —
    no network or mocking required.

Requires: pandas
"""

import pandas as pd

from timestamps import enrich_commits


# ── Reshape: dict → DataFrame ──────────────────────────────────────────────────

def commits_to_dataframe(data: dict, tz: str = "UTC") -> pd.DataFrame:
    """
    Convert the raw dict from fetch_user_profile() into a flat DataFrame
    with one row per commit.

    The source dict's "commits" list (repo, sha, date, message, author) does
    not carry the repo's language — that lives in the separate "repos" list.
    This function joins the two on repo name so each commit row ends up with
    its repo's language attached.

    Does not mutate the input dict: commit dicts are shallow-copied before
    being enriched, so calling this twice on the same `data` is always safe.

    Args:
        data: dict as returned by fetch_user_profile() / fetch_github_profile(),
              i.e. with top-level "repos" and "commits" keys.
        tz:   IANA timezone name used to compute "hour" and "day_of_week"
              (e.g. "America/New_York"). Defaults to "UTC". See timestamps.py
              for why UTC is the safe default for cross-contributor analysis.

    Returns:
        pd.DataFrame with exactly these columns:
            "date"        : str   "YYYY-MM-DD"
            "hour"        : int   0-23, in the given tz
            "day_of_week" : str   "Monday" … "Sunday"
            "repo_name"   : str   repository name
            "language"    : str | None   primary language of that repo

        Returns an empty DataFrame with the same columns if `data["commits"]`
        is empty or missing.

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from analytics import commits_to_dataframe
        >>> data = fetch_user_profile("torvalds")
        >>> df = commits_to_dataframe(data)
        >>> df.head()
                 date  hour day_of_week repo_name language
        0  2024-06-10    15      Monday     linux        C
        1  2024-06-09    09     Sunday      linux        C
    """
    columns = ["date", "hour", "day_of_week", "repo_name", "language"]

    commits = data.get("commits") or []
    if not commits:
        return pd.DataFrame(columns=columns)

    # Shallow-copy each commit dict so enrich_commits() doesn't mutate the
    # caller's original data["commits"] list as a side effect.
    commits = [dict(c) for c in commits]
    enrich_commits(commits, tz=tz)   # adds "hour", "day_of_week"; overwrites "date"

    df = pd.DataFrame(commits).rename(columns={"repo": "repo_name"})

    # Join in language from the repos list — commits don't carry it themselves
    lang_by_repo = {r["name"]: r.get("language") for r in data.get("repos", [])}
    df["language"] = df["repo_name"].map(lang_by_repo)

    return df[columns]


# ── Aggregate: DataFrame → per-language commit counts ─────────────────────────

def commits_per_language(df: pd.DataFrame) -> pd.DataFrame:
    """
    Total number of commits per language, sorted descending.

    Commits are a count, not a quantity to sum — there's no numeric field on
    a commit to add up. "Total commits per language" here means counting how
    many commit rows fall under each language.

    Repos with no detected language (GitHub returns null for empty repos,
    documentation-only repos, etc.) are grouped under "Unknown" rather than
    silently dropped, so the totals still add up to len(df).

    Args:
        df: DataFrame as returned by commits_to_dataframe(), or any DataFrame
            with at least a "language" column.

    Returns:
        pd.DataFrame with columns:
            "language"      : str
            "commit_count"  : int
        Sorted by commit_count descending. Empty DataFrame in → empty out.

    Example:
        >>> from analytics import commits_to_dataframe, commits_per_language
        >>> df = commits_to_dataframe(data)
        >>> commits_per_language(df)
          language  commit_count
        0        C            74
        1      C++            18
        2  Unknown             8
    """
    if df.empty:
        return pd.DataFrame(columns=["language", "commit_count"])

    result = (
        df["language"]
        .fillna("Unknown")
        .value_counts()
        .rename_axis("language")
        .reset_index(name="commit_count")
    )
    return result


# ── Coding consistency metrics ─────────────────────────────────────────────────

def coding_consistency(commits: list[dict], tz: str = "UTC") -> dict:
    """
    Calculate coding consistency metrics from a list of commits.

    Accepts either raw commits (each with a "date" ISO 8601 string, the shape
    returned by fetch_user_profile()["commits"]) or already-enriched commits
    (each with a "datetime" key, the shape after timestamps.enrich_commits()
    has run). If both keys are present, "datetime" is preferred since it's
    already parsed and unambiguous.

    All date-range logic (streaks, span, hour, weekday) is done with pandas
    vectorised operations rather than Python loops — important since this
    is meant to run on hundreds of commits without being a bottleneck.

    Args:
        commits: list of dicts, each with a "date" (ISO 8601 string) or
                 "datetime" (tz-aware datetime) key. Other keys are ignored.
        tz:      IANA timezone name for hour-of-day and weekend boundaries
                 (e.g. "America/New_York"). Defaults to "UTC". Only applies
                 when reading from "date"; if "datetime" is present it is
                 assumed to already be in the desired timezone.

    Returns:
        {
            "longest_streak_days":     int,    longest run of consecutive
                                                calendar days with >=1 commit
            "average_commits_per_day": float,  total commits / days spanned
                                                from first to last commit
                                                (inclusive) — gaps pull this
                                                number down, which is the
                                                point of a *consistency* metric
            "busiest_hour":            int | None,  0-23, hour with the most
                                                commits; None if no commits
            "codes_on_weekends":       bool,   True if any commit falls on
                                                Saturday or Sunday
        }

        All-zero/None/False values if `commits` is empty.

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from analytics import coding_consistency
        >>> data = fetch_user_profile("torvalds")
        >>> coding_consistency(data["commits"])
        {'longest_streak_days': 12, 'average_commits_per_day': 3.45,
         'busiest_hour': 14, 'codes_on_weekends': True}
    """
    empty_result = {
        "longest_streak_days": 0,
        "average_commits_per_day": 0.0,
        "busiest_hour": None,
        "codes_on_weekends": False,
    }

    if not commits:
        return empty_result

    # Prefer an already-parsed "datetime" if enrich_commits() has run;
    # otherwise parse the raw ISO "date" string ourselves.
    if "datetime" in commits[0]:
        dt_utc = pd.to_datetime([c["datetime"] for c in commits], utc=True)
    else:
        dt_utc = pd.to_datetime([c["date"] for c in commits], utc=True)

    dt_local = dt_utc.tz_convert(tz)

    # --- Busiest hour: the hour value (0-23) appearing most often ---
    busiest_hour = int(pd.Series(dt_local.hour).value_counts().idxmax())

    # --- Weekend check: pandas dayofweek is Monday=0 ... Sunday=6 ---
    codes_on_weekends = bool((dt_local.dayofweek >= 5).any())

    # --- Longest streak of consecutive calendar days with a commit ---
    # Standard pandas pattern: take unique calendar days, sort them, then
    # find where the gap to the previous day isn't exactly 1 — each such
    # gap starts a new streak. cumsum() turns those breakpoints into group
    # IDs, and the largest group size is the longest streak.
    unique_days = pd.Series(dt_local.normalize().unique()).sort_values().reset_index(drop=True)
    day_gaps    = unique_days.diff().dt.days        # NaN for the first row
    streak_id   = (day_gaps != 1).cumsum()           # new streak each non-1 gap
    longest_streak = int(unique_days.groupby(streak_id).size().max())

    # --- Average commits per day, across the FULL span (not just active days) ---
    # Using the full span (including days with zero commits) means gaps in
    # activity pull this number down — which is exactly what a consistency
    # metric should do. Averaging only over active days would hide gaps.
    span_days = (unique_days.max() - unique_days.min()).days + 1
    average_commits_per_day = round(len(commits) / span_days, 2)

    return {
        "longest_streak_days":     longest_streak,
        "average_commits_per_day": average_commits_per_day,
        "busiest_hour":            busiest_hour,
        "codes_on_weekends":       codes_on_weekends,
    }


# ── Top languages ranking ──────────────────────────────────────────────────────

def top_languages(
    repos:          list[dict],
    language_bytes: dict[str, dict[str, int]] | None = None,
    top_n:          int = 5,
) -> tuple[list[tuple[str, float]], float]:
    """
    Rank the user's top languages by total bytes committed, falling back to
    a count of repos per language if byte data isn't available.

    Byte data isn't included in the standard repo listing — GitHub's repos
    endpoint only reports one primary "language" per repo, not a full
    breakdown. Fetching the breakdown costs one extra API call per repo
    (see data_fetcher.fetch_language_bytes()), so it's optional here: pass
    it in when you have it, and this function degrades gracefully to
    repo-count ranking when you don't.

    Args:
        repos:          list of repo dicts, each with at least a "language"
                         key (e.g. data["repos"] from fetch_user_profile()).
                         Used for the repo-count fallback, and as the
                         language vocabulary when no byte data is given.
        language_bytes: optional dict mapping repo name -> {language: bytes},
                         e.g. from data_fetcher.fetch_language_bytes(). If
                         omitted or empty, ranking falls back to counting
                         repos by their primary "language" field instead.
        top_n:          how many languages to return. Defaults to 5.

    Returns:
        A tuple of (top_languages, top_language_percentage):

        top_languages : list[tuple[str, float]]
            Up to `top_n` (language, percentage) pairs, sorted descending
            by total bytes (or repo count if falling back). Percentages
            are of the language-attributed total — repos with no detected
            language are excluded from the denominator, since "no language
            detected" isn't itself a language worth ranking.

        top_language_percentage : float
            The percentage share of the single most-used language, i.e.
            top_languages[0][1]. 0.0 if there's no language data at all.

    Example:
        >>> from data_fetcher import fetch_user_profile, fetch_language_bytes
        >>> from analytics import top_languages
        >>> data = fetch_user_profile("torvalds")
        >>> lang_bytes = fetch_language_bytes("torvalds", repos=data["repos"])
        >>> top5, top_pct = top_languages(data["repos"], language_bytes=lang_bytes)
        >>> top5
        [('C', 68.2), ('Python', 14.1), ('Shell', 9.7), ('C++', 5.0), ('Makefile', 3.0)]
        >>> top_pct
        68.2

        # Without byte data — falls back to counting repos per language:
        >>> top5, top_pct = top_languages(data["repos"])
        >>> top5
        [('Python', 40.0), ('JavaScript', 25.0), ('Go', 15.0), ('C++', 12.0), ('Rust', 8.0)]
    """
    totals: dict[str, int] = {}

    if language_bytes:
        for lang_map in language_bytes.values():
            for lang, byte_count in lang_map.items():
                totals[lang] = totals.get(lang, 0) + byte_count
    else:
        # Fallback: count repos per primary language; repos with no
        # detected language are skipped rather than counted as "Unknown",
        # since we're ranking actual languages here, not data completeness.
        for repo in repos:
            lang = repo.get("language")
            if lang:
                totals[lang] = totals.get(lang, 0) + 1

    unit_total = sum(totals.values())
    if not totals or unit_total == 0:
        return [], 0.0

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    ranked_pct = [(lang, round(count / unit_total * 100, 1)) for lang, count in ranked]

    top_language_percentage = ranked_pct[0][1] if ranked_pct else 0.0
    return ranked_pct, top_language_percentage


# ── 7×24 commit heatmap ─────────────────────────────────────────────────────────

_DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday",
]   # Mon-first, matching ISO weekday convention used throughout this project


def build_commit_heatmap(commit_times: list, tz: str = "UTC") -> pd.DataFrame:
    """
    Build a 7×24 grid of commit counts: one row per day of week, one column
    per hour of day. This is the exact shape Plotly's go.Heatmap expects.

    Every cell in the grid exists even where the count is zero — a heatmap
    needs a complete, regularly-shaped grid, not a sparse table that only
    lists combinations that actually occurred.

    Args:
        commit_times: list of commit timestamps. Each item may be an ISO
                       8601 string (e.g. "2024-06-10T15:30:45Z") or a
                       datetime object (naive or tz-aware) — pd.to_datetime()
                       parses either. Pass commit dicts' "date" values
                       (or "datetime" values, if enrich_commits() has run)
                       directly: [c["date"] for c in data["commits"]].
        tz:            IANA timezone name used to compute which day/hour
                        bucket each commit falls into (e.g. "America/New_York").
                        Defaults to "UTC".

    Returns:
        pd.DataFrame, shape (7, 24):
            index   : day names, "Monday" … "Sunday"
            columns : hours, 0–23
            values  : int commit counts (0 where no commits occurred)

        Use .values for a plain nested array/list, or pass the DataFrame
        straight to Plotly:

            heatmap_df = build_commit_heatmap(commit_times)

            import plotly.graph_objects as go
            fig = go.Figure(go.Heatmap(
                z=heatmap_df.values,
                x=heatmap_df.columns,
                y=heatmap_df.index,
            ))
            fig.show()

    Example:
        >>> times = [
        ...     "2024-06-10T15:00:00Z",  # Monday, 15:00
        ...     "2024-06-10T15:30:00Z",  # Monday, 15:00 (same bucket)
        ...     "2024-06-08T09:00:00Z",  # Saturday, 09:00
        ... ]
        >>> build_commit_heatmap(times).loc[:, [9, 15]]
                     9   15
        Monday        0    2
        Tuesday       0    0
        Wednesday     0    0
        Thursday      0    0
        Friday        0    0
        Saturday      1    0
        Sunday        0    0
    """
    if not commit_times:
        # All-zero grid rather than an empty/malformed frame — keeps the
        # shape consistent for callers that always expect a 7×24 DataFrame.
        return pd.DataFrame(0, index=_DAY_NAMES, columns=range(24), dtype=int)

    dt_utc   = pd.to_datetime(list(commit_times), utc=True)
    dt_local = dt_utc.tz_convert(tz)

    buckets = pd.DataFrame({
        "day_of_week": pd.Categorical(
            [_DAY_NAMES[d] for d in dt_local.dayofweek],
            categories=_DAY_NAMES,
            ordered=True,
        ),
        "hour": dt_local.hour,
    })

    heatmap = (
        buckets.groupby(["day_of_week", "hour"])
               .size()
               .unstack(fill_value=0)
               # Explicit reindex guarantees all 7 days and all 24 hours are
               # present even if some bucket had zero commits in the data —
               # groupby/unstack alone only returns combinations that occurred.
               .reindex(index=_DAY_NAMES, columns=range(24), fill_value=0)
               .astype(int)
    )

    return heatmap


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from data_fetcher import fetch_user_profile

    if len(sys.argv) < 2:
        print("Usage: python analytics.py <username>")
        sys.exit(1)

    data = fetch_user_profile(sys.argv[1])
    df = commits_to_dataframe(data)

    print(df.head(10).to_string(index=False))
    print(f"\nShape: {df.shape}")

    print("\nCommits per language:")
    print(commits_per_language(df).to_string(index=False))

    print("\nCoding consistency:")
    for key, value in coding_consistency(data["commits"]).items():
        print(f"  {key}: {value}")

    print("\nTop languages (by repo count — pass language_bytes for byte ranking):")
    top5, top_pct = top_languages(data["repos"])
    for lang, pct in top5:
        print(f"  {lang:15s} {pct:>5.1f}%")
    print(f"\n  Top language share: {top_pct}%")

    print("\n7×24 commit heatmap (first 8 hours shown):")
    heatmap = build_commit_heatmap([c["date"] for c in data["commits"]])
    print(heatmap.iloc[:, :8].to_string())
