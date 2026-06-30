"""
charts.py
---------
Plotly visualizations for the DevWrapped pipeline.

Built on top of analytics.py's outputs — pass it a DataFrame already
computed by that layer, get back a styled go.Figure. Kept separate from
analytics.py for the same reason insights.py is separate: analytics
produces numbers for any consumer (a chart, a table, further computation);
this file produces presentation — colors, layout, theme — specifically for
Plotly, which will need tweaking repeatedly as the DevWrapped UI takes shape.

Requires: plotly, pandas
    pip install plotly pandas

Responsiveness — three contexts, three approaches:
    1. Streamlit:        st.plotly_chart(fig, use_container_width=True)
                          Streamlit handles resizing itself; no figure
                          changes needed beyond what's already done below.
    2. Raw HTML page:     use save_responsive_html() (below). It sets
                          config={"responsive": True} so the chart redraws
                          on window resize, and writes the chart into a
                          div sized at 100% width/height of its container.
    3. Notebook / fig.show(): works as-is — autosize=True (set in every
                          chart's layout here) means no fixed pixel size
                          fights the notebook's own sizing.

    The common thread: every chart in this file is built WITHOUT a fixed
    width/height in its layout. A fixed width is what actually breaks
    responsiveness — once you set width=800, the figure can't shrink below
    that on a mobile screen no matter what config you wrap it in.
"""

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px


# Custom palette — loosely terminal/matrix-inspired (greens + cool accents)
# rather than Plotly's defaults. Not making every slice a shade of green:
# a donut with 5+ near-identical greens would be unreadable, so the palette
# leads with green/teal/mint and uses clearly distinct accents after that.
_PALETTE = [
    "#00FF88",  # neon green   — primary accent
    "#00C2A8",  # teal
    "#3DDC97",  # mint green
    "#5BC0EB",  # sky blue
    "#9D4EDD",  # violet
    "#FFB627",  # amber
    "#FF6B6B",  # coral
    "#6C757D",  # slate gray   — reserved for the "Other" slice
]

_THEMES = {
    "dark": {
        "paper_bgcolor": "#0D1117",   # GitHub-dark-mode background
        "plot_bgcolor":  "#0D1117",
        "font_color":    "#E6EDF3",
        "line_color":    "#0D1117",   # slice borders match background — clean separation
    },
    "light": {
        "paper_bgcolor": "#FFFFFF",
        "plot_bgcolor":  "#FFFFFF",
        "font_color":    "#1A1A1A",
        "line_color":    "#FFFFFF",
    },
}

# Heatmap-specific colorscales — low values blend into the background, high
# values pop in neon green, keeping the same matrix-inspired identity as
# the donut chart's palette without needing 24×7 individually colored cells.
_HEATMAP_COLORSCALES = {
    "dark": [
        [0.00, "#0D1117"],   # no commits — same as background, fades away
        [0.25, "#0B3D2E"],
        [0.50, "#137752"],
        [0.75, "#00C2A8"],
        [1.00, "#00FF88"],   # peak activity
    ],
    "light": [
        [0.00, "#FFFFFF"],
        [0.25, "#D8F3E3"],
        [0.50, "#7FE8B9"],
        [0.75, "#1FBE85"],
        [1.00, "#00875A"],
    ],
}

# Line + fill colors for the contribution-growth chart. Dark theme keeps
# the same neon green as the rest of the file; light theme uses a deeper
# green so the line has enough contrast against a white background.
_GROWTH_COLORS = {
    "dark":  {"line": "#00FF88", "fill": "rgba(0, 255, 136, 0.18)"},
    "light": {"line": "#00875A", "fill": "rgba(0, 135, 90, 0.15)"},
}


def _base_layout(colors: dict) -> dict:
    """
    Layout kwargs shared by every chart in this file: theme colors plus
    the settings that make responsiveness work (see module docstring).
    """
    return dict(
        paper_bgcolor=colors["paper_bgcolor"],
        plot_bgcolor=colors["plot_bgcolor"],
        font=dict(color=colors["font_color"]),
        autosize=True,                      # fills its container, no fixed size
        margin=dict(l=20, r=20, t=70, b=20),
    )


def create_language_donut_chart(
    language_counts: pd.DataFrame,
    title: str = "Your Languages",
    theme: str = "dark",
    top_n: int = 7,
) -> go.Figure:
    """
    Build a donut chart of commit count by language.

    Args:
        language_counts: DataFrame with "language" and "commit_count"
                          columns — the exact shape returned by
                          analytics.commits_per_language().
        title:            chart title. Defaults to "Your Languages".
        theme:            "dark" or "light" — controls background, font,
                           and slice-border colors. Defaults to "dark".
        top_n:            how many languages to show individually before
                           grouping the remainder into "Other". Defaults
                           to 7 — a donut with 15 thin slices is unreadable,
                           so smaller languages get folded into one slice.

    Returns:
        go.Figure — a Plotly donut chart with no fixed width/height set
        (see module docstring for what that means for responsiveness).
        If `language_counts` is empty, returns a themed-but-empty figure
        rather than raising, so a dashboard doesn't need to special-case
        "user has no commits yet" before calling this.

    Raises:
        ValueError: `theme` is not "dark" or "light".

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from analytics import commits_to_dataframe, commits_per_language
        >>> from charts import create_language_donut_chart
        >>> data = fetch_user_profile("torvalds")
        >>> df = commits_to_dataframe(data)
        >>> lang_counts = commits_per_language(df)
        >>> fig = create_language_donut_chart(lang_counts, theme="dark")
        >>> fig.show()
    """
    if theme not in _THEMES:
        raise ValueError(f"theme must be 'dark' or 'light', got {theme!r}")
    colors = _THEMES[theme]

    if language_counts.empty:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor="center"),
            **_base_layout(colors),
        )
        return fig

    df = (
        language_counts
        .sort_values("commit_count", ascending=False)
        .reset_index(drop=True)
    )

    # Fold anything beyond top_n - 1 into a single "Other" slice so the
    # chart stays readable regardless of how many languages the user has.
    if len(df) > top_n:
        head = df.iloc[: top_n - 1]
        other_total = df.iloc[top_n - 1 :]["commit_count"].sum()
        other_row = pd.DataFrame([{"language": "Other", "commit_count": other_total}])
        df = pd.concat([head, other_row], ignore_index=True)

    slice_colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(df))]
    if "Other" in df["language"].values:
        # Always render "Other" in slate gray regardless of where it landed
        other_idx = df.index[df["language"] == "Other"][0]
        slice_colors[other_idx] = _PALETTE[-1]

    fig = go.Figure(data=[go.Pie(
        labels=df["language"],
        values=df["commit_count"],
        hole=0.55,
        sort=False,                          # keep our own descending order
        marker=dict(
            colors=slice_colors,
            line=dict(color=colors["line_color"], width=2),
        ),
        textinfo="label+percent",
        textfont=dict(color=colors["font_color"], size=13),
        hovertemplate="<b>%{label}</b><br>%{value} commits (%{percent})<extra></extra>",
    )])

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=22)),
        showlegend=True,
        legend=dict(
            orientation="v",
            x=1.02, xanchor="left",
            y=0.5,  yanchor="middle",
            font=dict(color=colors["font_color"]),
        ),
        **_base_layout(colors),
    )

    return fig


def create_commit_heatmap_chart(
    heatmap_data: pd.DataFrame,
    title: str = "Commit Activity",
    theme: str = "dark",
) -> go.Figure:
    """
    Build an interactive heatmap of commit counts by hour of day (x-axis)
    and day of week (y-axis), with color intensity showing commit count.

    Args:
        heatmap_data: DataFrame shaped (7, 24) — the exact output of
                      analytics.build_commit_heatmap(): index is day names
                      ("Monday" … "Sunday"), columns are hours (0-23),
                      values are commit counts.
        title:        chart title. Defaults to "Commit Activity".
        theme:        "dark" or "light" — controls background/font colors
                      and the heatmap's color scale. Defaults to "dark".

    Returns:
        go.Figure — Plotly heatmap with a colorbar and hover tooltips, no
        fixed width/height set (see module docstring for what that means
        for responsiveness).

    Raises:
        ValueError: `theme` is not "dark" or "light".

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from analytics import build_commit_heatmap
        >>> from charts import create_commit_heatmap_chart
        >>> data = fetch_user_profile("torvalds")
        >>> heatmap_data = build_commit_heatmap([c["date"] for c in data["commits"]])
        >>> fig = create_commit_heatmap_chart(heatmap_data)
        >>> fig.show()
    """
    if theme not in _THEMES:
        raise ValueError(f"theme must be 'dark' or 'light', got {theme!r}")
    colors     = _THEMES[theme]
    colorscale = _HEATMAP_COLORSCALES[theme]

    fig = go.Figure(data=go.Heatmap(
        z=heatmap_data.values,
        x=[f"{h:02d}:00" for h in heatmap_data.columns],
        y=list(heatmap_data.index),
        colorscale=colorscale,
        colorbar=dict(
            title=dict(text="Commits", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            outlinewidth=0,
        ),
        xgap=2,   # thin gaps between cells — reads as a clean grid, not a blur
        ygap=2,
        hovertemplate="<b>%{y}</b>, %{x}<br>%{z} commits<extra></extra>",
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=22)),
        xaxis=dict(
            title=dict(text="Hour of day", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            showgrid=False,
            dtick=2,   # label every other hour — 24 labels crammed together is unreadable
        ),
        yaxis=dict(
            title=dict(text="Day of week", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            showgrid=False,
            autorange="reversed",   # Monday at top, Sunday at bottom — natural reading order
        ),
        **_base_layout(colors),
    )

    return fig


def create_top_repos_bar_chart(
    repos: list[dict] | pd.DataFrame,
    title: str = "Top Repositories",
    theme: str = "dark",
    top_n: int = 10,
) -> go.Figure:
    """
    Build a bar chart ranking the top N repos by star count, colored by
    primary language, with each repo's description shown on hover.

    Uses plotly.express rather than graph_objects (unlike the other charts
    in this file) specifically because a categorical color mapping with an
    automatic legend — one legend entry per language — is what px.bar is
    built for. Building that by hand in graph_objects means one go.Bar
    trace per language; px does it in one call and the returned figure is
    still a plain go.Figure underneath, so every theming/layout call below
    works exactly as it does on the other charts.

    Args:
        repos: list of repo dicts (e.g. data["repos"] from
               fetch_user_profile()) or an equivalent DataFrame. Each
               item needs at least "name", "stars", and "language" keys;
               "description" is used for the hover tooltip if present.
        title: chart title. Defaults to "Top Repositories".
        theme: "dark" or "light" — controls background/font/bar-border
               colors. Defaults to "dark".
        top_n: how many repos to show, ranked by star count descending.
               Defaults to 10.

    Returns:
        go.Figure — Plotly bar chart with a per-language legend, no fixed
        width/height set (see module docstring for what that means for
        responsiveness).

    Raises:
        ValueError: `theme` is not "dark" or "light".

    Example:
        >>> from data_fetcher import fetch_user_profile
        >>> from charts import create_top_repos_bar_chart
        >>> data = fetch_user_profile("torvalds")
        >>> fig = create_top_repos_bar_chart(data["repos"])
        >>> fig.show()
    """
    if theme not in _THEMES:
        raise ValueError(f"theme must be 'dark' or 'light', got {theme!r}")
    colors = _THEMES[theme]

    df = repos.copy() if isinstance(repos, pd.DataFrame) else pd.DataFrame(repos)

    if df.empty:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor="center"),
            **_base_layout(colors),
        )
        return fig

    # Defensive fills: a repo can have no detected language or no description
    df["language"]    = df["language"].fillna("Unknown") if "language" in df else "Unknown"
    df["description"] = (
        df["description"].fillna("No description provided.")
        if "description" in df else "No description provided."
    )

    df = df.sort_values("stars", ascending=False).head(top_n).reset_index(drop=True)

    # Assign each language a fixed color from our palette, reusing the same
    # "Unknown -> slate gray" convention as the donut chart's "Other" slice.
    languages = [lang for lang in df["language"].unique() if lang != "Unknown"]
    color_map = {lang: _PALETTE[i % len(_PALETTE)] for i, lang in enumerate(languages)}
    if "Unknown" in df["language"].values:
        color_map["Unknown"] = _PALETTE[-1]

    fig = px.bar(
        df,
        x="name",
        y="stars",
        color="language",
        color_discrete_map=color_map,
        custom_data=["language", "description"],
    )

    # px.bar splits the data into one trace per language; without this, bar
    # order can drift away from strict star-count order once split across
    # traces. Pin the x-axis category order explicitly to the sorted list.
    fig.update_xaxes(categoryorder="array", categoryarray=df["name"].tolist())

    fig.update_traces(
        hovertemplate=(
            "<b>%{x}</b><br>"
            "⭐ %{y:,} stars<br>"
            "Language: %{customdata[0]}<br>"
            "%{customdata[1]}"
            "<extra></extra>"
        ),
        marker_line=dict(color=colors["line_color"], width=1),
    )

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=22)),
        xaxis=dict(
            title=dict(text="Repository", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            tickangle=-35,
            showgrid=False,
        ),
        yaxis=dict(
            title=dict(text="Stars", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            gridcolor="rgba(128,128,128,0.15)",
        ),
        legend=dict(
            title=dict(text="Language", font=dict(color=colors["font_color"])),
            font=dict(color=colors["font_color"]),
        ),
        **_base_layout(colors),
    )

    return fig


def create_contribution_growth_chart(
    commits: list[dict] | pd.DataFrame,
    title: str = "Your Contribution Growth",
    theme: str = "dark",
) -> go.Figure:
    """
    Build a cumulative-commits-over-time area/line chart — a "contribution
    growth" timeline showing total commits climbing as the account ages,
    styled with a bold title, a clean line, and a glowing fill underneath
    (Spotify-Wrapped-reveal styling rather than a plain analytics chart).

    Args:
        commits: list of commit dicts (each needs a "date" ISO 8601
                 string) or an equivalent DataFrame with a "date" column.
                 For a fuller growth curve, prefer
                 data_fetcher.fetch_commits() (up to 100 commits per repo)
                 over fetch_user_profile()["commits"] (capped at the 100
                 most recent commits total, across all repos) — the latter
                 only covers whatever short span those 100 commits happen
                 to fall within.
        title:   chart title. Defaults to "Your Contribution Growth".
        theme:   "dark" or "light" — controls background, line, and fill
                 colors. Defaults to "dark".

    Returns:
        go.Figure — filled area + line chart, x = calendar date, y =
        cumulative commit count, with a callout annotation on the final
        point. No fixed width/height set (responsive — see module
        docstring).

    Raises:
        ValueError: `theme` is not "dark" or "light".

    Example:
        >>> from data_fetcher import fetch_commits
        >>> from charts import create_contribution_growth_chart
        >>> df = fetch_commits("torvalds")          # fuller history than fetch_user_profile()
        >>> fig = create_contribution_growth_chart(df, theme="dark")
        >>> fig.show()
    """
    if theme not in _THEMES:
        raise ValueError(f"theme must be 'dark' or 'light', got {theme!r}")
    colors      = _THEMES[theme]
    line_color  = _GROWTH_COLORS[theme]["line"]
    fill_color  = _GROWTH_COLORS[theme]["fill"]

    df = commits.copy() if isinstance(commits, pd.DataFrame) else pd.DataFrame(commits)

    if df.empty or "date" not in df:
        fig = go.Figure()
        fig.update_layout(
            title=dict(text=title, x=0.5, xanchor="center"),
            **_base_layout(colors),
        )
        return fig

    # Bucket every commit to its calendar day, then reindex across the FULL
    # day range (not just days that had a commit) so inactive stretches
    # show as flat plateaus rather than the line jumping across a gap —
    # a plateau is part of the growth story, a skipped gap is misleading.
    day = pd.to_datetime(df["date"], utc=True).dt.normalize()
    daily_counts = day.value_counts().sort_index()

    full_range = pd.date_range(daily_counts.index.min(), daily_counts.index.max(), freq="D")
    daily_counts = daily_counts.reindex(full_range, fill_value=0)

    cumulative = daily_counts.cumsum()

    fig = go.Figure(data=[go.Scatter(
        x=cumulative.index,
        y=cumulative.values,
        mode="lines",
        line=dict(color=line_color, width=3, shape="spline", smoothing=0.3),
        fill="tozeroy",
        fillcolor=fill_color,
        hovertemplate="<b>%{x|%b %d, %Y}</b><br>%{y:,} total commits<extra></extra>",
        showlegend=False,
    )])

    # Spotify-Wrapped-style reveal: a marker + callout on the final point,
    # stating the headline number rather than leaving it to be read off
    # the axis.
    final_x, final_y = cumulative.index[-1], cumulative.values[-1]
    fig.add_trace(go.Scatter(
        x=[final_x], y=[final_y],
        mode="markers",
        marker=dict(size=10, color=line_color, line=dict(color=colors["paper_bgcolor"], width=2)),
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.add_annotation(
        x=final_x, y=final_y,
        text=f"<b>{final_y:,}</b> commits",
        showarrow=True,
        arrowhead=0,
        arrowcolor=line_color,
        ax=-50, ay=-40,
        font=dict(size=16, color=colors["font_color"]),
        bgcolor=colors["paper_bgcolor"],
        bordercolor=line_color,
        borderwidth=1,
        borderpad=6,
    )

    fig.update_layout(
        title=dict(
            text=title, x=0.5, xanchor="center",
            font=dict(size=26, family="Arial Black, Helvetica Neue, sans-serif", color=colors["font_color"]),
        ),
        xaxis=dict(
            tickfont=dict(color=colors["font_color"]),
            showgrid=False,
            showline=True,
            linecolor=colors["font_color"],
            showspikes=False,
        ),
        yaxis=dict(
            title=dict(text="Total commits", font=dict(color=colors["font_color"])),
            tickfont=dict(color=colors["font_color"]),
            gridcolor="rgba(128,128,128,0.12)",
            zeroline=False,
        ),
        hovermode="x unified",
        **_base_layout(colors),
    )

    return fig


def save_responsive_html(fig: go.Figure, path: str) -> None:
    """
    Save a Plotly figure as a self-contained, responsive HTML file.

    fig.write_html() on its own embeds a chart that only resizes correctly
    if its layout has no fixed width/height — true for every chart built
    in this file — but the HTML wrapper Plotly generates still needs to be
    told to redraw on window resize. config={"responsive": True} does
    that; default_width/height="100%" makes the chart fill whatever
    container it's dropped into rather than a fixed pixel box.

    Args:
        fig:  the Plotly figure to save (e.g. from create_language_donut_chart).
        path: output file path, e.g. "language_donut.html".

    Example:
        >>> fig = create_language_donut_chart(lang_counts)
        >>> save_responsive_html(fig, "language_donut.html")
    """
    fig.write_html(
        path,
        config={"responsive": True},
        default_width="100%",
        default_height="100%",
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    from data_fetcher import fetch_user_profile
    from analytics import commits_to_dataframe, commits_per_language, build_commit_heatmap

    if len(sys.argv) < 2:
        print("Usage: python charts.py <username> [dark|light]")
        sys.exit(1)

    theme = sys.argv[2] if len(sys.argv) > 2 else "dark"

    data = fetch_user_profile(sys.argv[1])
    df = commits_to_dataframe(data)

    lang_counts = commits_per_language(df)
    donut = create_language_donut_chart(lang_counts, theme=theme)
    save_responsive_html(donut, "language_donut.html")
    print("Saved responsive chart to language_donut.html")

    heatmap_data = build_commit_heatmap([c["date"] for c in data["commits"]])
    heatmap_fig = create_commit_heatmap_chart(heatmap_data, theme=theme)
    save_responsive_html(heatmap_fig, "commit_heatmap.html")
    print("Saved responsive chart to commit_heatmap.html")

    repos_fig = create_top_repos_bar_chart(data["repos"], theme=theme)
    save_responsive_html(repos_fig, "top_repos.html")
    print("Saved responsive chart to top_repos.html")

    growth_fig = create_contribution_growth_chart(data["commits"], theme=theme)
    save_responsive_html(growth_fig, "contribution_growth.html")
    print("Saved responsive chart to contribution_growth.html")
