"""
example.py
----------
End-to-end usage examples for the full GitHub analysis pipeline.

Files in this project:
    _core.py           Shared exceptions, constants, RateLimitTracker, HTTP helper
    timestamps.py      ISO 8601 parser → analysis fields (hour, day_of_week, etc.)
    github_profile.py  Full snapshot → structured dict  (profile + repos + commits)
    github_fetch.py    Bulk pipeline  → pandas DataFrame (all commits, all repos)

Install dependencies:
    pip install aiohttp pandas

Set your token (optional but strongly recommended):
    export GITHUB_TOKEN=ghp_...
"""

import asyncio
import os

import pandas as pd

TOKEN    = os.getenv("GITHUB_TOKEN")   # None → unauthenticated (60 req/hr limit)
USERNAME = "torvalds"                  # replace with any GitHub username


# ─────────────────────────────────────────────────────────────────────────────
# Path A: structured dict snapshot  (github_profile.py + timestamps.py)
# Best for: display, storage, one-off lookups, building APIs
# ─────────────────────────────────────────────────────────────────────────────

def example_profile_snapshot():
    from github_profile import fetch_github_profile_sync, GitHubUserNotFoundError
    from timestamps import enrich_commits

    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    try:
        data = fetch_github_profile_sync(USERNAME, token=TOKEN)
    except GitHubUserNotFoundError:
        print(f"User '{USERNAME}' not found.")
        return

    # ── 2. Inspect top-level keys ─────────────────────────────────────────────
    print("=== Profile ===")
    p = data["profile"]
    print(f"  {p['name']} (@{p['username']})")
    print(f"  {p['followers']:,} followers  ·  {p['public_repos']} public repos")
    print(f"  Bio: {p['bio']}")

    print("\n=== Top 5 repos by stars ===")
    for repo in data["repos"][:5]:
        print(f"  {repo['name']:30s}  ⭐ {repo['stars']:>7,}  [{repo['language']}]")

    print("\n=== Fetch metadata ===")
    m = data["meta"]
    print(f"  {m['total_repos']} repos  ·  {m['total_commits']} commits fetched")
    print(f"  Languages: {', '.join(m['languages'][:8])}")
    print(f"  Duration:  {m['fetch_duration_secs']}s")
    print(f"  API calls remaining: {m['api_calls_remaining']}")

    # ── 3. Enrich commits with time fields ────────────────────────────────────
    # enrich_commits() mutates data["commits"] in place.
    # "date" (ISO string) → overwritten by "YYYY-MM-DD"
    # "datetime" (tz-aware UTC datetime object) → new key
    # "hour", "day_of_week", "month", "is_weekend", etc. → new keys
    enrich_commits(data["commits"], tz="UTC")

    print("\n=== Most recent 3 commits (enriched) ===")
    for c in data["commits"][:3]:
        print(
            f"  [{c['repo']}]  {c['date']}  {c['day_of_week'][:3]}  "
            f"{c['hour_label']}  {c['author'][:20]:20s}  {c['message'][:60]}"
        )

    # ── 4. Convert to DataFrame ───────────────────────────────────────────────
    df = pd.DataFrame(data["commits"])
    print(f"\nDataFrame shape: {df.shape}")
    print(df[["repo", "date", "hour", "day_of_week", "month", "author"]].head())

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Path B: bulk DataFrame pipeline  (github_fetch.py + timestamps.py)
# Best for: commit heatmaps, language stats, pandas-heavy analysis
# ─────────────────────────────────────────────────────────────────────────────

async def example_bulk_dataframe():
    from github_fetch import run
    from timestamps import enrich_dataframe

    # ── 1. Fetch all repos + 100 commits per repo ─────────────────────────────
    df = await run(USERNAME, token=TOKEN)

    # ── 2. Enrich with time analysis columns ──────────────────────────────────
    # enrich_dataframe() returns a NEW DataFrame (non-mutating).
    # Pass tz="America/New_York" etc. to shift hour/day fields to local time.
    df = enrich_dataframe(df, date_col="date", tz="UTC")

    print(f"\nColumns: {list(df.columns)}")
    print(f"Shape:   {df.shape}")

    # ── 3. Sample analyses ────────────────────────────────────────────────────

    print("\n=== Commits by hour of day (top 5) ===")
    print(
        df.groupby("hour_label")["sha"]
          .count()
          .sort_values(ascending=False)
          .head(5)
          .to_string()
    )

    print("\n=== Commits by day of week ===")
    print(
        df.groupby(["day_of_week_num", "day_of_week"])["sha"]
          .count()
          .reset_index()
          .sort_values("day_of_week_num")
          .set_index("day_of_week")["sha"]
          .to_string()
    )

    print("\n=== Weekend vs weekday split ===")
    split = df.groupby("is_weekend")["sha"].count()
    print(f"  Weekday: {split.get(False, 0):>5}")
    print(f"  Weekend: {split.get(True,  0):>5}")

    print("\n=== Commits by language (top 5) ===")
    print(
        df.groupby("language")["sha"]
          .count()
          .sort_values(ascending=False)
          .head(5)
          .to_string()
    )

    print("\n=== Commit heatmap grid (day × hour, first 6 hours shown) ===")
    heatmap = df.pivot_table(
        index="day_of_week_num",
        columns="hour",
        values="sha",
        aggfunc="count",
        fill_value=0,
    )
    heatmap.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print(heatmap.iloc[:, :6].to_string())   # first 6 hours as a preview

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Path C: error handling
# ─────────────────────────────────────────────────────────────────────────────

def example_error_handling():
    from github_profile import fetch_github_profile_sync
    from _core import (
        GitHubUserNotFoundError,
        GitHubRateLimitError,
        GitHubNetworkError,
        GitHubError,
    )

    try:
        data = fetch_github_profile_sync("this_user_does_not_exist_xyz", token=TOKEN)

    except GitHubUserNotFoundError as e:
        # Safe to retry with a corrected username
        print(f"Not found: {e}")

    except GitHubRateLimitError as e:
        # e.reset_at is a Unix timestamp — wait until then
        import time
        wait = max(e.reset_at - time.time(), 0)
        print(f"Rate limited. Sleep {wait:.0f}s then retry.")

    except GitHubNetworkError as e:
        # Connection drop, DNS failure, unexpected HTTP status
        print(f"Network error: {e}")

    except GitHubError as e:
        # Catch-all for any other GitHub-related error
        print(f"GitHub error: {e}")



if __name__ == "__main__":
    print("━" * 60)
    print("PATH A — structured dict snapshot")
    print("━" * 60)
    example_profile_snapshot()

    print("\n" + "━" * 60)
    print("PATH B — bulk DataFrame pipeline")
    print("━" * 60)
    asyncio.run(example_bulk_dataframe())

    print("\n" + "━" * 60)
    print("PATH C — error handling")
    print("━" * 60)
    example_error_handling()
