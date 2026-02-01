# Lobbying Signals

Automated detection of newsworthy trends in federal lobbying disclosure data. Tracks spikes, new entrants, and emerging policy topics from SOPR filings.

## Live Dashboard

**[View Dashboard](https://uzairqadir.github.io/lobbying-signals/)**

Updated every 6 hours via GitHub Actions.

## Features

- **Real-time SOPR ingestion**: Downloads and parses Senate lobbying disclosure filings
- **LLM-powered extraction**: Uses Gemini 3 Flash to extract topics, entities, and legislation from activity descriptions
- **Trend detection**: Identifies spikes (>50% change), new entrants, and concentration shifts
- **Automated alerts**: Generates newsworthy headlines for significant changes
- **Zero infrastructure cost**: Runs entirely on GitHub (Actions + Pages + Releases)

## Architecture

```
GitHub Actions (cron every 6h)
    │
    ├── Download DB from GitHub Release
    ├── Ingest new SOPR filings
    ├── Extract topics via Gemini API
    ├── Compute trends and alerts
    ├── Export JSON to docs/data/
    ├── Upload DB back to Release
    └── Commit JSON exports → GitHub Pages
```

## Data Sources

- **SOPR Filings**: [Senate Lobbying Disclosure](https://lda.senate.gov/filings/public/filing/search/)
- Covers all federal lobbying activity disclosures

## Local Development

```bash
# Install dependencies
uv pip install -r requirements.txt

# Set API keys
export GEMINI_API_KEY=your_key
export LDA_API_KEY=your_key  # optional, for higher rate limits

# Run full refresh
python 07_refresh.py

# Or run individual steps
python 01_ingest.py              # Download new filings
python 06_extract.py extract 500 # Extract topics from 500 activities
python 08_trends.py export       # Generate JSON exports
```

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `01_ingest.py` | Download and parse SOPR XML filings |
| `06_extract.py` | LLM extraction of topics/entities/legislation |
| `07_refresh.py` | Orchestrate full refresh cycle |
| `08_trends.py` | Compute trends and generate alerts |

## Database

SQLite database stored in GitHub Releases (not in repo due to size). Contains:
- `filings`: Core filing metadata
- `registrants`: Lobbying firms
- `clients`: Clients being represented
- `activities`: Individual lobbying activities
- `activity_extractions`: LLM-extracted topics/entities

## License

MIT
