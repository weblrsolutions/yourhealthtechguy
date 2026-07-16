# Your Health Tech Guy

Free health & technology news aggregator — styled like [Pulse by Zerodha](https://pulse.zerodha.com/). Runs on **GitHub Pages** + **GitHub Actions** (no database, no paid hosting).

**Live (after Pages is enabled):** https://weblrsolutions.github.io/yourhealthtechguy/

## How it works

1. Actions cron every **6 hours UTC** (00:00 / 06:00 / 12:00 / 18:00) fetches RSS feeds (`scripts/feeds.yml`)
2. New items are deduped by URL/title
3. Gemini (primary) or Groq (fallback) tags category, country, and writes a short original summary
4. Results land in [`data/articles.json`](data/articles.json)
5. Static Vite site builds and deploys to GitHub Pages

See [`FUTURE.md`](FUTURE.md) for roadmap and revenue ideas.

## Deploy checklist (this repo)

1. **Repo** — https://github.com/weblrsolutions/yourhealthtechguy (`main` branch)
2. **Pages** — Settings → Pages → Build and deployment → **Source: GitHub Actions**
3. **Secrets** — Settings → Secrets and variables → Actions → New repository secret:
   - `GEMINI_API_KEY` (required for good summaries) — [Google AI Studio](https://aistudio.google.com/apikey)
   - `GROQ_API_KEY` (optional fallback) — [Groq Console](https://console.groq.com/)
4. **First run** — Actions → **Ingest & Deploy** → Run workflow  
   Or wait for the next 6-hour cron. Site shows “Updated …” in each visitor’s **local timezone**.
5. **Confirm** — open https://weblrsolutions.github.io/yourhealthtechguy/

Without API secrets, ingest still runs with heuristics (weaker summaries). With secrets, LLM enrichment runs automatically on schedule.

## Local development

### Ingest

```bash
cd scripts
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# From repo root: copy .env.example → .env and fill keys (optional)
# Windows PowerShell:
#   Copy-Item .env.example .env
#   $env:GEMINI_API_KEY="..."

python ingest.py --skip-llm   # heuristics only
python ingest.py              # with LLM if keys are set
```

### Site

```bash
copy data\articles.json site\public\articles.json   # Windows
# cp data/articles.json site/public/articles.json  # macOS/Linux

cd site
npm install
npm run dev
```

## Adding feeds

Edit [`scripts/feeds.yml`](scripts/feeds.yml). Use official RSS/Atom URLs only.

## Data shape

```json
{
  "updated_at": "ISO-8601",
  "articles": [
    {
      "id": "url-hash",
      "url": "...",
      "title": "...",
      "source": "...",
      "published_at": "ISO-8601",
      "category": "medical-device|ai-in-health|wellness-tech|fitness-tech|other",
      "country": "US",
      "summary": "...",
      "ingested_at": "ISO-8601"
    }
  ]
}
```

Retention default: **120 days** (`RETENTION_DAYS`). Cap new items per run: **80** (`MAX_NEW_PER_RUN`).

## Cost

| Piece | Cost |
|-------|------|
| GitHub Pages + Actions (public repo) | $0 |
| Gemini / Groq free tiers | $0 (rate limits apply) |
| Custom domain | optional later |

## License

MIT — use freely for your showcase / side project.
