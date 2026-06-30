"""
timestamps.py

Parses GitHub API ISO 8601 timestamps into structured dicts for commit analysis.

All timestamps from the GitHub API are UTC. This module keeps them as UTC
throughout — never silently converts to local time, which would make
hour-of-day heatmaps wrong for any non-local contributor.

If you need local time for a specific contributor, pass their IANA timezone
name as the `tz` argument (e.g. "America/New_York", "Europe/Berlin").
The stored `datetime` object is always UTC regardless.

Standalone — no dependency on _core.py, github_fetch.py, or github_profile.py.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


_DAYS = [
    "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday",
]
_MONTHS = [
    "",           
    "January", "February", "March",     "April",   "May",      "June",
    "July",    "August",   "September", "October", "November", "December",
]


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_github_timestamp(ts: str, tz: str = "UTC") -> dict:
    """
    Parse a single GitHub API timestamp string into a structured dict.

    GitHub returns timestamps in two equivalent formats:
        "2024-06-10T15:30:45Z"          ← commits, events
        "2024-06-10T15:30:45+00:00"     ← user/repo metadata fields

    Both are handled identically.

    Args:
        ts: ISO 8601 timestamp string from the GitHub API.
        tz: IANA timezone name for human-readable fields (hour, day_of_week,
            month). Defaults to "UTC". The stored `datetime` is always UTC.

    Returns:
        {
            "datetime":        datetime  — tz-aware, always UTC
            "hour":            int       — 0–23, in the requested tz
            "hour_label":      str       — "03:00", "15:00" (chart axis labels)
            "day_of_week":     str       — "Monday" … "Sunday"
            "day_of_week_num": int       — 0=Monday … 6=Sunday (sortable)
            "is_weekend":      bool      — True for Saturday or Sunday
            "month":           str       — "January" … "December"
            "month_num":       int       — 1–12 (sortable)
            "year":            int       — e.g. 2024
            "quarter":         int       — 1–4
            "date":            str       — "2024-06-10" (daily grouping key)
            "iso_week":        int       — ISO 8601 week number, 1–53
            "timestamp_utc":   str       — normalised to "+00:00" suffix
        }

    Raises:
        ValueError: ts is None, empty, or not a parseable ISO 8601 string.
    """
    if not ts:
        raise ValueError("Timestamp string is empty or None.")

    normalised = ts.rstrip("Z").replace(" ", "T")
    if "+" not in normalised and normalised.count("-") <= 2:
        normalised += "+00:00"

    try:
        dt_utc = datetime.fromisoformat(normalised).astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Cannot parse timestamp '{ts}': {exc}") from exc

    # Convert to the requested display timezone for human-readable fields only
    dt_local = dt_utc.astimezone(ZoneInfo(tz))

    dow_num = dt_local.weekday()        # 0 = Monday, 6 = Sunday
    month   = dt_local.month
    quarter = (month - 1) // 3 + 1

    return {
        # Always UTC, always tz-aware — safe for arithmetic and comparisons
        "datetime":        dt_utc,

        # Time-of-day in the requested tz (matters for heatmaps)
        "hour":            dt_local.hour,
        "hour_label":      dt_local.strftime("%H:00"),

        # Day of week
        "day_of_week":     _DAYS[dow_num],
        "day_of_week_num": dow_num,
        "is_weekend":      dow_num >= 5,

        # Month / year / quarter
        "month":           _MONTHS[month],
        "month_num":       month,
        "year":            dt_local.year,
        "quarter":         quarter,

        # Grouping keys — usable directly in groupby / pivot_table
        "date":            dt_local.strftime("%Y-%m-%d"),
        "iso_week":        dt_local.isocalendar().week,

        # Preserved for debugging and round-trip checks
        "timestamp_utc":   dt_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }


# ── Batch helpers ─────────────────────────────────────────────────────────────

def parse_timestamps_to_columns(
    timestamps: list[str],
    tz:         str = "UTC",
) -> dict[str, list]:
    """
    Parse a list of timestamp strings and return a column-oriented dict.
    Pass the result directly to pd.DataFrame() or df.assign(**cols).

    Args:
        timestamps: list of ISO 8601 strings (e.g. a DataFrame column as list).
        tz:         display timezone for hour/day/month fields.

    Returns:
        Dict mapping field name → list of values, same length as input.

    Example:
        cols = parse_timestamps_to_columns(df["date"].tolist(), tz="UTC")
        df   = df.assign(**cols)
    """
    if not timestamps:
        return {}

    parsed = [parse_github_timestamp(ts, tz) for ts in timestamps]

    # Transpose: list of dicts → dict of lists
    return {key: [row[key] for row in parsed] for key in parsed[0]}


def enrich_commits(
    commits: list[dict],
    tz:      str = "UTC",
) -> list[dict]:
    """
    Merge parsed time fields into each commit dict in the list.
    Mutates the list in place and returns it for chaining.

    The original "date" key (ISO string) is overwritten by the "date" field
    from parse_github_timestamp (formatted "YYYY-MM-DD"). The full UTC
    datetime object is stored under "datetime", and the original ISO string
    is preserved under "timestamp_utc".

    Args:
        commits: list of commit dicts from fetch_github_profile or run().
                 Each dict must have a "date" key.
        tz:      display timezone for hour/day/month fields.

    Returns:
        The same list, each dict now containing all parsed time fields.

    Example:
        commits = enrich_commits(data["commits"], tz="Europe/London")
        df = pd.DataFrame(commits)
    """
    for commit in commits:
        commit.update(parse_github_timestamp(commit["date"], tz))
    return commits


def enrich_dataframe(df, date_col: str = "date", tz: str = "UTC"):
    """
    Add all parsed time columns to a pandas DataFrame.
    Returns a new DataFrame — does not mutate the original.

    Args:
        df:       pandas DataFrame with a timestamp column.
        date_col: name of the column holding ISO 8601 strings.
        tz:       display timezone for hour/day/month fields.

    Returns:
        New DataFrame with original columns plus all parsed time fields.

    Example:
        df = pd.DataFrame(data["commits"])
        df = enrich_dataframe(df, tz="America/New_York")
        print(df[["repo", "hour", "day_of_week", "month"]].head())
    """
    try:
        import pandas  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "pandas is required for enrich_dataframe(). "
            "Install it with: pip install pandas"
        ) from exc

    cols = parse_timestamps_to_columns(df[date_col].tolist(), tz=tz)
    return df.assign(**cols)
