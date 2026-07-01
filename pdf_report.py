"""
pdf_report.py
-------------
Generates a downloadable PDF report: profile info, the Plotly charts
(rendered as static images), and the fun facts list.

Requires: reportlab, kaleido==0.2.1, requests
    pip install reportlab "kaleido==0.2.1" requests

A note on the kaleido version pin, because it's load-bearing:
    kaleido 1.0+ dropped its bundled renderer and now requires a separate
    Google Chrome install (`plotly_get_chrome`). That download fails
    behind any restrictive firewall — confirmed in this project's own dev
    sandbox (HTTP 403 on the Chrome download), and a common failure mode
    in CI runners, corporate networks, and some hosted deploy
    environments too. kaleido==0.2.1 bundles Chromium directly in the
    package instead, so chart rendering works with zero extra setup.

    kaleido<1.0 is deprecated upstream (support officially ended after
    September 2025), but it still functions correctly with current Plotly
    releases — verified directly against this project's installed
    version. If you're running locally with an unrestricted connection
    and prefer the actively-maintained path, switch the pin to
    "kaleido>=1.0" and run `plotly_get_chrome` once; PDFGenerationError
    below still catches and explains that case if Chrome isn't found.
"""

import io

import requests
import plotly.graph_objects as go
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


class PDFGenerationError(Exception):
    """Raised when the PDF can't be generated for a fixable, explainable reason."""


# ── Image helpers ─────────────────────────────────────────────────────────────

def _fetch_avatar_image(avatar_url: str, size: int = 100) -> Image | None:
    """
    Download the user's avatar and wrap it as a reportlab Image flowable.

    Returns None on any failure (network error, bad URL, timeout) rather
    than raising — a missing avatar is cosmetic and shouldn't prevent the
    rest of the report from generating.
    """
    if not avatar_url:
        return None
    try:
        response = requests.get(avatar_url, timeout=10)
        response.raise_for_status()
        return Image(io.BytesIO(response.content), width=size, height=size)
    except Exception:
        return None


def _sanitize_fig(fig: go.Figure) -> go.Figure:
    """
    Convert any pandas Timestamp values in a figure's trace data to ISO
    strings before passing to kaleido.

    kaleido 1.x uses orjson for serialization, which raises
    "Type is not JSON serializable: Timestamp" when a figure contains
    pandas Timestamp objects in its x/y data (e.g. the contribution
    growth chart's date axis). Converting them to plain ISO strings
    first makes the figure safe for any kaleido version.

    Works by round-tripping through Plotly's own JSON serializer (which
    already handles Timestamps correctly) then back to a Figure object,
    so the figure's visual appearance is completely unchanged.
    """
    import json
    return go.Figure(json.loads(fig.to_json()))


def _fig_to_image(fig: go.Figure, width: int = 900, height: int = 520) -> Image:
    """
    Render a Plotly figure to a static PNG (via kaleido) and wrap it as a
    reportlab Image flowable, scaled to fit the page width while keeping
    the chart's aspect ratio.

    Raises:
        PDFGenerationError: if kaleido's Chrome dependency isn't set up.
            With the kaleido==0.2.1 pin this project uses, that shouldn't
            happen — Chromium is bundled — but this stays in place so the
            error is still clear and actionable if someone upgrades
            kaleido later instead of silently producing a cryptic
            RuntimeError traceback.
    """
    # Sanitize before rendering — converts Timestamps to ISO strings so
    # orjson (used by kaleido 1.x) can serialize the figure without error.
    fig = _sanitize_fig(fig)
    try:
        png_bytes = fig.to_image(format="png", width=width, height=height, scale=2)
    except RuntimeError as e:
        if "Chrome" in str(e):
            raise PDFGenerationError(
                "Chart rendering needs a one-time setup step. Run this once "
                "in your terminal, then try generating the report again:\n\n"
                "    plotly_get_chrome\n\n"
                "(Or switch back to the bundled-renderer version, which needs "
                "no extra setup: pip install \"kaleido==0.2.1\")"
            ) from e
        raise

    display_width = 6.5 * inch
    display_height = display_width * (height / width)   # preserve aspect ratio
    return Image(io.BytesIO(png_bytes), width=display_width, height=display_height)


# ── Report builder ────────────────────────────────────────────────────────────

def generate_pdf_report(
    profile: dict,
    figures: dict[str, go.Figure],
    fun_facts: list[str],
) -> bytes:
    """
    Build a complete PDF report and return it as raw bytes, ready to hand
    straight to st.download_button().

    Args:
        profile:   dict from GitHubAnalyzer.get_profile() — uses "name",
                   "username", "bio", "followers", "public_repos",
                   "avatar_url".
        figures:   dict mapping a section label -> go.Figure, e.g.
                   {"Language Breakdown": donut_fig, "Commit Activity": heatmap_fig, ...}.
                   Each one is rendered to a static image via kaleido and
                   embedded on its own page.
        fun_facts: list[str] from GitHubAnalyzer.get_fun_facts().

    Returns:
        bytes — the complete PDF file content.

    Raises:
        PDFGenerationError: if kaleido's Chrome dependency isn't installed
            (see module docstring for the one-time fix).

    Example:
        >>> figures = {
        ...     "Language Breakdown": donut_fig,
        ...     "Commit Activity": heatmap_fig,
        ...     "Top Repositories": repos_fig,
        ...     "Contribution Growth": growth_fig,
        ... }
        >>> pdf_bytes = generate_pdf_report(profile, figures, fun_facts)
        >>> st.download_button("Download PDF", data=pdf_bytes, file_name="report.pdf")
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"], fontSize=26, spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=styles["Normal"], fontSize=12,
        textColor=colors.HexColor("#555867"), spaceAfter=18, alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "SectionHeading", parent=styles["Heading2"], spaceBefore=10, spaceAfter=10,
        textColor=colors.HexColor("#0B3D2E"),
    )
    body_style = styles["Normal"]

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("DevWrapped Report", title_style))
    story.append(Paragraph(f"@{profile['username']}", subtitle_style))

    # ── Profile ───────────────────────────────────────────────────────────────
    avatar_img = _fetch_avatar_image(profile.get("avatar_url", ""))
    if avatar_img:
        avatar_img.hAlign = "CENTER"
        story.append(avatar_img)
        story.append(Spacer(1, 10))

    name_style = ParagraphStyle("Name", parent=styles["Heading2"], alignment=TA_CENTER)
    story.append(Paragraph(profile.get("name") or profile["username"], name_style))

    if profile.get("bio"):
        bio_style = ParagraphStyle("Bio", parent=body_style, alignment=TA_CENTER, spaceAfter=8)
        story.append(Paragraph(profile["bio"], bio_style))

    stats_style = ParagraphStyle("Stats", parent=body_style, alignment=TA_CENTER, spaceBefore=6)
    story.append(Paragraph(
        f"<b>{profile['followers']:,}</b> followers &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>{profile['public_repos']}</b> public repos",
        stats_style,
    ))

    story.append(PageBreak())

    # ── Charts — one per page ───────────────────────────────────────────────
    for label, fig in figures.items():
        story.append(Paragraph(label, section_style))
        story.append(_fig_to_image(fig))
        story.append(PageBreak())

    # ── Fun facts ─────────────────────────────────────────────────────────────
    story.append(Paragraph("✨ Fun Facts", section_style))
    if fun_facts:
        bullets = [ListItem(Paragraph(fact, body_style), spaceAfter=8) for fact in fun_facts]
        story.append(ListFlowable(
            bullets, bulletType="bullet", bulletColor=colors.HexColor("#00AB6B"),
            leftIndent=14,
        ))
    else:
        story.append(Paragraph(
            "Not enough commit history yet to generate fun facts.", body_style
        ))

    doc.build(story)
    return buffer.getvalue()
