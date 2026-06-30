"""
insights.py
-----------
Turns the numeric stats from analytics.py into human-readable "fun facts",
Spotify-Wrapped style — e.g. "Your code peaks at 3 AM (you're a night owl)".

Why this is its own file rather than added to analytics.py:
  - analytics.py produces numbers (counts, percentages, DataFrames) meant to
    be machine-consumed (charts, further aggregation, Plotly). This file
    produces sentences meant to be read by a human. Mixing "compute the
    stat" with "phrase it nicely" in the same function would make both
    harder to test — analytics.py's outputs are already covered by exact
    numeric assertions; this file's outputs are covered by string-shape
    assertions instead (does it mention a percentage, is it non-empty).
  - Wording, thresholds, and tone here ("night owl", "Momentum!") are the
    kind of thing you'll want to tweak repeatedly as the DevWrapped UI
    takes shape. Isolating that churn keeps it from touching the stats
    pipeline that other code already depends on.

Requires: pandas (via analytics.py)
"""

import pandas as pd

from analytics import coding_consistency, top_languages


# ── Phrasing helpers (private — tone/wording lives here, tweak freely) ────────

def _describe_peak_hour(hour: int) -> str:
    """Turn an hour (0-23) into a "peaks at X (vibe)" sentence."""
    label = f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"

    if hour < 5 or hour >= 22:
        vibe = "night owl"
    elif hour < 9:
        vibe = "early riser"
    elif hour < 18:
        vibe = "daytime grinder"
    else:
        vibe = "evening coder"

    return f"Your code peaks at {label} (you're a {vibe})."


def _describe_weekday_split(commits: list[dict], tz: str) -> str:
    """Compute % of commits on weekdays and phrase it with a tone descriptor."""
    dt_utc   = pd.to_datetime([c["date"] for c in commits], utc=True)
    dt_local = dt_utc.tz_convert(tz)

    is_weekend  = dt_local.dayofweek >= 5   # pandas: Mon=0 ... Sun=6
    weekday_pct = round((~is_weekend).sum() / len(commits) * 100, 1)

    if weekday_pct >= 80:
        tone = "disciplined 9-to-5 energy"
    elif weekday_pct >= 60:
        tone = "mostly a weekday coder"
    elif weekday_pct >= 40:
        tone = "a healthy weekday/weekend mix"
    else:
        tone = "weekends are basically your office"

    return f"You commit {weekday_pct}% on weekdays — {tone}."


def _describe_pace(avg_per_day: float) -> str:
    """Phrase the average-commits-per-day figure with a tone descriptor."""
    if avg_per_day >= 5:
        tone = "prolific"
    elif avg_per_day >= 1:
        tone = "steady"
    else:
        tone = "sporadic"
    return f"You average {avg_per_day} commits a day — {tone} contributor energy."


def _years_on_github(created_at: str) -> int:
    """Whole years between an ISO 8601 account-creation date and now."""
    created = pd.to_datetime(created_at, utc=True)
    now     = pd.Timestamp.now(tz="UTC")
    return int((now - created).days // 365)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_fun_facts(
    data:           dict,
    language_bytes: dict[str, dict[str, int]] | None = None,
    tz:             str = "UTC",
) -> list[str]:
    """
    Generate human-readable "fun facts" about a GitHub user.

    Calls coding_consistency() and top_languages() internally — pass in the
    same dict you got from fetch_user_profile(), no need to pre-compute and
    hand over every individual stat yourself.

    Args:
        data:           dict as returned by fetch_user_profile() /
                         fetch_github_profile(), with "profile", "repos",
                         "commits", "meta" keys.
        language_bytes: optional dict from data_fetcher.fetch_language_bytes().
                         Makes the top-language fact byte-accurate instead of
                         falling back to a repo count. Omit if you don't have it.
        tz:             IANA timezone name for hour/weekday calculations
                         (e.g. "America/New_York"). Defaults to "UTC".

    Returns:
        list[str] of human-readable sentences, typically 5-8 depending on
        what's computable from the given data. Facts that don't apply (e.g.
        top-language when no repo has a detected language) are silently
        skipped rather than padded with a placeholder. If `data["commits"]`
        is empty, only the non-commit facts (repo/follower/account-age)
        are returned, so the list may be shorter than 5 in that case.

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from insights import generate_fun_facts
        >>> data = fetch_user_profile("torvalds")
        >>> for fact in generate_fun_facts(data):
        ...     print("•", fact)
        • Your code peaks at 3 PM (you're a daytime grinder).
        • You commit 81.4% on weekdays — disciplined 9-to-5 energy.
        • Your longest streak was 12 days in a row. Momentum!
        • C is your top language at 68.2% of your repos.
        • You've shipped 100 commits across 45 public repos.
        • 192,847 people follow your work on GitHub.
        • You've been coding on GitHub for 18 years.
        • You average 3.45 commits a day — steady contributor energy.
    """
    facts: list[str] = []

    profile = data.get("profile") or {}
    repos   = data.get("repos") or []
    commits = data.get("commits") or []
    meta    = data.get("meta") or {}

    consistency = coding_consistency(commits, tz=tz) if commits else None

    # --- Peak coding hour ---
    if consistency and consistency["busiest_hour"] is not None:
        facts.append(_describe_peak_hour(consistency["busiest_hour"]))

    # --- Weekday vs weekend split ---
    if commits:
        facts.append(_describe_weekday_split(commits, tz))

    # --- Longest streak ---
    if consistency and consistency["longest_streak_days"] > 0:
        days = consistency["longest_streak_days"]
        day_word = "day" if days == 1 else "days"
        facts.append(f"Your longest streak was {days} {day_word} in a row. Momentum!")

    # --- Top language ---
    top5, top_pct = top_languages(repos, language_bytes=language_bytes)
    if top5:
        top_lang = top5[0][0]
        basis = "of your code" if language_bytes else "of your repos"
        facts.append(f"{top_lang} is your top language at {top_pct}% {basis}.")

    # --- Commit / repo overview ---
    total_repos = meta.get("total_repos", len(repos))
    if total_repos:
        total_commits = meta.get("total_commits", len(commits))
        facts.append(
            f"You've shipped {total_commits} commits across {total_repos} public repos."
        )

    # --- Followers ---
    followers = profile.get("followers")
    if followers is not None:
        if followers >= 1000:
            facts.append(f"{followers:,} people follow your work on GitHub.")
        else:
            facts.append(f"You have {followers} followers cheering on your commits.")

    # --- Years on GitHub ---
    created_at = profile.get("created_at")
    if created_at:
        years = _years_on_github(created_at)
        if years >= 1:
            year_word = "year" if years == 1 else "years"
            facts.append(f"You've been coding on GitHub for {years} {year_word}.")

    # --- Average pace ---
    if consistency:
        facts.append(_describe_pace(consistency["average_commits_per_day"]))

    return facts[:8]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from data_fetcher import fetch_user_profile

    if len(sys.argv) < 2:
        print("Usage: python insights.py <username>")
        sys.exit(1)

    data = fetch_user_profile(sys.argv[1])

    print(f"\nFun facts about @{sys.argv[1]}:\n")
    for fact in generate_fun_facts(data):
        print(f"  • {fact}")
