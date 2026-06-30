"""
app.py
------
DevWrapped — Streamlit front end.

Current sections:
    1. A text input for the GitHub username
    2. A "Generate Report" button
    3. A loading spinner while data is fetched
    4. Friendly error messages for invalid/not-found usernames, rate
       limits, and network failures
    5. A profile header (avatar, name, bio, followers, public repos)
    6. Four charts in a 2-column grid: language donut, commit heatmap,
       top repos bar chart, and cumulative contribution growth
    7. A Fun Facts section — 5-8 generated insights as info cards
    8. A PDF export button — generates a downloadable report with the
       profile, all four charts, and the fun facts

Still to come: theme toggle.

Run with:
    pip install streamlit aiohttp pandas python-dotenv plotly reportlab "kaleido==0.2.1" requests
    streamlit run app.py

(See pdf_report.py's module docstring for why kaleido is pinned to 0.2.1
rather than the newer Chrome-dependent 1.0+ line.)
"""

import re

import streamlit as st

from github_analyzer import GitHubAnalyzer
from analytics import commits_per_language
from charts import (
    create_language_donut_chart,
    create_commit_heatmap_chart,
    create_top_repos_bar_chart,
    create_contribution_growth_chart,
)
from pdf_report import generate_pdf_report, PDFGenerationError
from data_fetcher import (
    GitHubError,
    GitHubUserNotFoundError,
    GitHubRateLimitError,
    GitHubNetworkError,
)

# Single point of control for chart theming — flip this to "light" (or wire
# it up to a toggle later) to re-theme every chart in the app at once.
_CHART_THEME = "dark"


# ── Username format validation ────────────────────────────────────────────────
# GitHub usernames: letters, digits, single hyphens, can't start/end with a
# hyphen, max 39 characters. Checking this client-side catches obviously
# malformed input (spaces, symbols, "-foo") without spending an API call on
# something that could never be a valid username in the first place.
_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$")


def _is_valid_username_format(username: str) -> bool:
    """True if `username` matches GitHub's username format rules."""
    return bool(_USERNAME_PATTERN.match(username))


# ── Page setup ─────────────────────────────────────────────────────────────────

st.set_page_config(page_title="DevWrapped", page_icon="🐙", layout="wide")

st.title("🐙 DevWrapped")
st.caption("Your GitHub year in review — enter a username to get started.")

# Session state holds the fetched analyzer across reruns, so later additions
# (chart tabs, etc.) can read st.session_state.analyzer without re-fetching.
st.session_state.setdefault("analyzer", None)
st.session_state.setdefault("report_username", None)
st.session_state.setdefault("pdf_bytes", None)


# ── Input flow ─────────────────────────────────────────────────────────────────

username_input = st.text_input(
    "GitHub username",
    placeholder="e.g. torvalds",
    help="Enter any public GitHub username to generate their DevWrapped report.",
)

generate_clicked = st.button("Generate Report", type="primary")

if generate_clicked:
    username = username_input.strip()

    if not username:
        st.error("⚠️ Please enter a GitHub username.")

    elif not _is_valid_username_format(username):
        st.error(
            f"⚠️ '{username}' doesn't look like a valid GitHub username "
            "(letters, digits, and single hyphens only)."
        )

    else:
        with st.spinner(f"Fetching GitHub data for @{username}..."):
            try:
                analyzer = GitHubAnalyzer(username)
                analyzer.get_stats()   # triggers the actual fetch; validates the username

            except GitHubUserNotFoundError:
                st.session_state.analyzer = None
                st.error(
                    f"❌ GitHub user '@{username}' wasn't found. "
                    "Double-check the spelling and try again."
                )

            except GitHubRateLimitError:
                st.session_state.analyzer = None
                st.error(
                    "❌ GitHub's API rate limit was reached. Try again in a few "
                    "minutes, or add a GITHUB_TOKEN to your .env file for a "
                    "much higher limit (60 → 5,000 requests/hour)."
                )

            except GitHubNetworkError as e:
                st.session_state.analyzer = None
                st.error(f"❌ Network error while contacting GitHub: {e}")

            except GitHubError as e:
                # Catch-all for any other GitHub-related error not handled above
                st.session_state.analyzer = None
                st.error(f"❌ Something went wrong: {e}")

            else:
                st.session_state.analyzer = analyzer
                st.session_state.report_username = username
                st.session_state.pdf_bytes = None   # clear any previous user's cached PDF


# ── Result: profile display ───────────────────────────────────────────────────
# Persists across reruns (not just right after the button click) so this
# survives other widget interactions once more sections exist below it.

if st.session_state.analyzer is not None:
    profile = st.session_state.analyzer.get_profile()

    st.success(f"✅ Data fetched for @{st.session_state.report_username}!")

    avatar_col, info_col = st.columns([1, 3])

    with avatar_col:
        st.image(profile["avatar_url"], width=150)

    with info_col:
        st.subheader(profile["name"] or profile["username"])
        st.caption(f"@{profile['username']}")
        if profile["bio"]:
            st.write(profile["bio"])

        followers_col, repos_col = st.columns(2)
        with followers_col:
            st.metric("Followers", f"{profile['followers']:,}")
        with repos_col:
            st.metric("Public Repos", profile["public_repos"])

    # ── Charts: 2 per row ────────────────────────────────────────────────────
    # All four pull from the same cached fetch (get_stats() above already
    # triggered and cached it) — none of these trigger a new network call.

    st.divider()
    st.subheader("📊 Your DevWrapped Stats")

    commits_df  = st.session_state.analyzer.get_commits_dataframe()
    lang_counts = commits_per_language(commits_df)
    heatmap_data = st.session_state.analyzer.get_heatmap_data()
    repos        = st.session_state.analyzer.get_repos()

    row1_left, row1_right = st.columns(2)
    with row1_left:
        donut_fig = create_language_donut_chart(lang_counts, theme=_CHART_THEME)
        st.plotly_chart(donut_fig, use_container_width=True)
    with row1_right:
        heatmap_fig = create_commit_heatmap_chart(heatmap_data, theme=_CHART_THEME)
        st.plotly_chart(heatmap_fig, use_container_width=True)

    row2_left, row2_right = st.columns(2)
    with row2_left:
        repos_fig = create_top_repos_bar_chart(repos, theme=_CHART_THEME)
        st.plotly_chart(repos_fig, use_container_width=True)
    with row2_right:
        growth_fig = create_contribution_growth_chart(commits_df, theme=_CHART_THEME)
        st.plotly_chart(growth_fig, use_container_width=True)

    # ── Fun Facts ────────────────────────────────────────────────────────────
    # generate_fun_facts() (called inside get_fun_facts()) already returns
    # complete, flavorful sentences — st.info() boxes give each one a
    # distinct colored card without needing to re-derive labels/values for
    # st.metric(), which is built for raw numbers rather than full sentences.

    st.divider()
    st.subheader("✨ Fun Facts")

    fun_facts = st.session_state.analyzer.get_fun_facts()

    if not fun_facts:
        st.write("Not enough commit history yet to generate fun facts.")
    else:
        # 2 per row, same rhythm as the chart grid above — fills left, right,
        # left, right, so 5-8 facts land in 3-4 tidy rows.
        fact_columns = st.columns(2)
        for i, fact in enumerate(fun_facts):
            with fact_columns[i % 2]:
                st.info(fact)

    # ── PDF Export ───────────────────────────────────────────────────────────
    # Reuses donut_fig/heatmap_fig/repos_fig/growth_fig from the chart grid
    # above rather than rebuilding them — same Python script execution, so
    # those variables are still in scope here. This guarantees the PDF shows
    # the exact same charts as the screen, and avoids paying the chart-build
    # cost twice.

    st.divider()
    st.subheader("📄 Export Report")

    if st.button("Generate PDF Report"):
        with st.spinner("Building your PDF report..."):
            try:
                figures = {
                    "Language Breakdown":  donut_fig,
                    "Commit Activity":     heatmap_fig,
                    "Top Repositories":    repos_fig,
                    "Contribution Growth": growth_fig,
                }
                st.session_state.pdf_bytes = generate_pdf_report(profile, figures, fun_facts)
            except PDFGenerationError as e:
                st.session_state.pdf_bytes = None
                st.error(f"❌ {e}")

    if st.session_state.pdf_bytes:
        st.download_button(
            label="⬇️ Download PDF Report",
            data=st.session_state.pdf_bytes,
            file_name=f"{st.session_state.report_username}_devwrapped.pdf",
            mime="application/pdf",
        )
