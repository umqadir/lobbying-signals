# Lobbying Signals - System Specification

## Overview

Automated system to detect newsworthy trends in federal lobbying data. Runs on GitHub infrastructure (free), refreshes every 6 hours, serves a static dashboard.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    GitHub Actions (cron)                         │
│                    Every 6 hours                                 │
│                                                                  │
│  1. gh release download filings.db                              │
│  2. python 07_refresh.py                                         │
│     ├── Ingest new filings from LDA API                         │
│     ├── Extract classifications (Gemini 3 Flash)                │
│     ├── Compute trends and alerts                                │
│     └── Export JSON for dashboard                                │
│  3. gh release upload filings.db --clobber                      │
│  4. git commit & push docs/data/*.json                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    GitHub Pages                                  │
│                    https://<user>.github.io/lobbying-signals     │
│                                                                  │
│  Static HTML/JS dashboard reads:                                 │
│  - docs/data/alerts.json      (today's notable events)          │
│  - docs/data/trends.json      (7d/30d topic trends)             │
│  - docs/data/stats.json       (summary statistics)              │
│  - docs/data/recent.json      (latest filings sample)           │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Ingestion (01_ingest.py)
- Pull new quarterly filings from LDA Senate API
- Incremental: only fetch filings not already in DB
- Rate limited: 120 req/min with API key

### 2. Extraction (06_extract.py)
- Classify new activities using Gemini 3 Flash
- Schema: domain, topics[], entities[], legislation[]
- Apply normalization dictionary for consistency
- ~$0.02 per 100 activities

### 3. Trend Computation (08_trends.py)
- Compare current period vs historical baseline
- Detect: spikes, new entrants, record-breakers
- Rolling windows: 7-day, 30-day, quarter-over-quarter

### 4. Alert Generation (08_trends.py)
- Daily alerts for significant changes
- Thresholds: >50% change, >$1M absolute
- Ranked by newsworthiness score

### 5. JSON Export (07_refresh.py)
- Export pre-computed data for static dashboard
- JSON exports committed to repo for GitHub Pages (size varies; keep payload minimal for fast page loads)

## File Structure

```
lobbying-signals/
├── .github/
│   └── workflows/
│       └── refresh.yml          # Cron job definition
├── docs/                        # GitHub Pages root
│   ├── index.html              # Dashboard
│   ├── app.js                  # Dashboard logic
│   ├── styles.css              # Styling
│   └── data/                   # JSON exports (auto-updated)
│       ├── alerts.json
│       ├── trends.json
│       ├── stats.json
│       └── recent.json
├── 01_ingest.py                # LDA API ingestion
├── 06_extract.py               # LLM classification
├── 07_refresh.py               # Orchestration script
├── 08_trends.py                # Trend/alert computation
├── db.py                       # Database helpers
├── config.py                   # Configuration
└── requirements.txt            # Python dependencies
```

## Database Schema

### Existing Tables
- `filings` - Filing metadata (id, registrant, client, quarter, income)
- `registrants` - Lobbying firms
- `clients` - Clients being represented
- `activities` - Lobbying activities per filing

### New Tables
- `activity_extractions` - LLM classifications (domain, topics, entities, legislation)
- `normalization_dict` - Term normalization mappings
- `trend_snapshots` - Historical trend data for comparison
- `alerts` - Generated alerts

## JSON Export Schemas

### alerts.json
```json
{
  "generated_at": "2025-02-01T12:00:00Z",
  "alerts": [
    {
      "type": "spike",
      "topic": "tariffs",
      "current_count": 450,
      "baseline_count": 150,
      "change_pct": 200,
      "headline": "Tariff lobbying triples in past 30 days",
      "top_clients": ["Client A", "Client B"]
    }
  ]
}
```

### trends.json
```json
{
  "generated_at": "2025-02-01T12:00:00Z",
  "topics": {
    "7d": [{"name": "tariffs", "count": 120, "change_pct": 45}],
    "30d": [{"name": "tariffs", "count": 450, "change_pct": 200}]
  },
  "domains": {...},
  "entities": {...}
}
```

### stats.json
```json
{
  "generated_at": "2025-02-01T12:00:00Z",
  "total_filings": 81000,
  "total_activities": 200000,
  "extracted_pct": 95,
  "date_range": {"start": "2024-01-01", "end": "2025-01-31"},
  "last_refresh": "2025-02-01T12:00:00Z"
}
```

### recent.json
```json
{
  "generated_at": "2025-02-01T12:00:00Z",
  "filings": [
    {
      "id": 12345,
      "client": "Example Corp",
      "registrant": "Lobbying Firm LLC",
      "income": 50000,
      "date": "2025-01-30",
      "topics": ["tariffs", "trade agreements"]
    }
  ]
}
```

## GitHub Actions Workflow

```yaml
name: Refresh Data
on:
  schedule:
    - cron: '0 */6 * * *'  # Every 6 hours
  workflow_dispatch:        # Manual trigger

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          uv venv --python 3.12
          uv pip install -r requirements.txt

      - name: Download database
        run: gh release download data --pattern 'filings.db' -O data/filings.db
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Run refresh
        run: uv run python 07_refresh.py
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          LDA_API_KEY: ${{ secrets.LDA_API_KEY }}

      - name: Upload database
        run: gh release upload data data/filings.db --clobber
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Commit JSON exports
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add docs/data/
          git diff --staged --quiet || git commit -m "Update data exports"
          git push
```

## Secrets Required

- `GEMINI_API_KEY` - Google AI API key for Gemini 3 Flash
- `LDA_API_KEY` - Senate LDA API key (optional, faster ingestion)

## Cost Estimate

| Component | Cost |
|-----------|------|
| GitHub Actions | Free (public repo) |
| GitHub Pages | Free |
| GitHub Releases | Free (unlimited storage) |
| Gemini 3 Flash | ~$0.50/day for new extractions |
| LDA API | Free |

**Total: ~$15/month** (mostly LLM extraction)

## Dashboard Features

1. **Alert Feed** - Today's notable trends
2. **Topic Trends** - 7d/30d trending topics with sparklines
3. **Search** - Find lobbying by topic, client, legislation
4. **Recent Filings** - Latest activity stream

## Refresh Logic

```python
def refresh():
    # 1. Ingest new filings (incremental)
    new_filings = ingest_latest()

    # 2. Extract classifications for new activities
    extract_new_activities(limit=500)  # Cap per run for cost

    # 3. Build normalization dictionary (weekly)
    if is_weekly_run():
        build_normalization()

    # 4. Compute trends
    trends = compute_trends()

    # 5. Generate alerts
    alerts = generate_alerts(trends)

    # 6. Export JSON
    export_json(trends, alerts)
```
