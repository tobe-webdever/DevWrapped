[README.md](https://github.com/user-attachments/files/29489441/README.md)
# 🐙 DevWrapped

> Your GitHub year, Spotify-Wrapped style.

DevWrapped turns your public GitHub activity into a shareable, visual report — language breakdowns, commit heatmaps, your most-starred repos, a contribution growth timeline, and a handful of personality-flavored "fun facts" about how you code. Enter any public username, and get a dashboard back in seconds.

---

## ✨ What is DevWrapped?

Most GitHub profile pages are static: a pinned-repo grid and a contribution graph. DevWrapped asks a more interesting question — *what does your coding behavior actually look like?* It analyzes a user's public repos and recent commit history and turns that into:

- 🌐 **Language Breakdown** — a donut chart of where your commits actually go, by language
- 🔥 **Commit Heatmap** — a 7×24 grid showing exactly when you code (day of week × hour of day)
- ⭐ **Top Repositories** — your highest-starred repos, colored by language
- 📈 **Contribution Growth** — a cumulative timeline of your commit history
- 🎉 **Fun Facts** — auto-generated one-liners like *"Your code peaks at 3 PM (you're a daytime grinder)"* or *"Your longest streak was 12 days in a row"*
- 📄 **PDF Export** — every section above, bundled into a downloadable report

No GitHub login required — it works on any public username, using GitHub's public REST API.

---


![DevWrapped demo](docs/demo.gif)

---

## 🛠️ Tech Stack

| Layer | Tools |
|---|---|
| **UI** | [Streamlit](https://streamlit.io/) |
| **Data fetching** | [aiohttp](https://docs.aiohttp.org/) (async GitHub REST API client) |
| **Data wrangling** | [pandas](https://pandas.pydata.org/) |
| **Charts** | [Plotly](https://plotly.com/python/) (graph_objects + express) |
| **PDF export** | [ReportLab](https://www.reportlab.com/) + [Kaleido](https://github.com/plotly/Kaleido) (static chart rendering) |
| **Config / secrets** | [python-dotenv](https://github.com/theskumar/python-dotenv) |
| **Data source** | [GitHub REST API](https://docs.github.com/en/rest) |

---

## 🚀 Live Demo

**[devwrapped.streamlit.app](https://your-app-name.streamlit.app)** ← *TODO: replace with your actual Streamlit Cloud URL once deployed*

To deploy your own copy: push this repo to GitHub, then go to [share.streamlit.io](https://share.streamlit.io), connect the repo, and point it at `app.py`. Add your `GITHUB_TOKEN` under the app's **Settings → Secrets** (same key as your local `.env` — see below) rather than committing it.

---

## 💻 Running Locally

**1. Clone the repo**
```bash
git clone https://github.com/<your-username>/devwrapped.git
cd devwrapped
```

**2. (Recommended) create a virtual environment**
```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Add a GitHub token** (optional, but you'll hit a 60-requests/hour limit fast without one)
```bash
cp .env.example .env
```
Then open `.env` and paste in a token from **github.com → Settings → Developer settings → Fine-grained tokens** (no scopes needed — it only reads public data). With a token, the limit jumps to 5,000 requests/hour.

**5. One-time setup for PDF export**

Chart images in the PDF are rendered by Kaleido, which needs a local Chrome install (it doesn't bundle one):
```bash
plotly_get_chrome
```
This is a one-time step per machine, not per report. Skip it if you don't need PDF export yet — every other feature works without it.

**6. Run the app**
```bash
streamlit run app.py
```
Streamlit will open `http://localhost:8501` in your browser automatically.

---

## 📂 Project Structure

```
devwrapped/
├── app.py                 # Streamlit UI — the only file that imports streamlit
├── github_analyzer.py     # GitHubAnalyzer — the facade tying everything below together
├── data_fetcher.py        # Public fetch API (loads GITHUB_TOKEN from .env)
├── github_profile.py      # Async fetch: profile + repos + recent commits
├── github_fetch.py        # Async fetch: bulk commit history pipeline
├── _core.py               # Shared exceptions, rate-limit tracking, HTTP helper
├── analytics.py           # Pure transforms: DataFrames, streaks, language ranking, heatmap
├── timestamps.py          # ISO 8601 timestamp parsing → hour/day/month fields
├── insights.py             # Turns analytics output into human-readable "fun facts"
├── charts.py               # Plotly chart builders (donut, heatmap, bar, growth)
├── pdf_report.py           # Assembles the downloadable PDF report
├── example.py              # Standalone usage reference, outside the Streamlit app
├── .streamlit/
│   └── config.toml         # Dark theme + custom fonts/colors
├── .env.example            # Template for your GITHUB_TOKEN
├── requirements.txt
└── LICENSE
```

The fetch/transform/presentation layers are kept deliberately separate: `data_fetcher.py` and friends only ever produce plain dicts/DataFrames with no network code; `analytics.py` and `insights.py` only ever consume that data and produce numbers/text with no I/O; `charts.py` and `pdf_report.py` only ever turn that output into something visual. New contributors can usually work in one layer without needing to understand the others.

---

## 🤝 Contributing

Contributions are welcome — this started as a learning project, so reasonable refactors, bug fixes, and new "fun facts"/chart ideas are all good fits.

1. **Fork** the repo and create a branch: `git checkout -b feature/your-idea`
2. **Match the existing layering** described above — if you're adding a new stat, it likely belongs in `analytics.py` (numbers) or `insights.py` (text), not directly in `app.py`.
3. **Test before submitting.** Most functions here are pure (no network calls) and easy to check with a hand-built fixture dict — see the `if __name__ == "__main__":` block at the bottom of most files for a working example.
4. **Keep network calls opt-in and rate-limit aware.** If a new feature needs extra GitHub API calls (like `fetch_language_bytes()` does), make it an explicit opt-in rather than something that runs by default.
5. Open a PR with a short description of what changed and why.

Found a bug or have an idea but don't want to write the code? Opening an issue is just as useful.

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
