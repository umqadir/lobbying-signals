# Lobbying Signals

Automated detection of directional signals in federal lobbying disclosure data. Tracks emerging topics, agencies, domains, legislation, and recent filings from Senate LDA disclosures.

## Live Dashboard

**[View Dashboard](https://umqadir.github.io/lobbying-signals/)**

Updated daily via GitHub Actions.

## Features

- **Real-time LDA ingestion**: Downloads and stores Senate lobbying disclosure filings via the LDA API; coverage runs from 2020 to the present
- **Deterministic extraction**: Uses versioned rules, LDA issue codes, regexes, and dictionaries to extract topics, entities, and legislation without model calls
- **Trend detection**: Compares 30- and 90-day signal windows against prior-period and year-ago baselines, with seasonality-aware year-over-year as the default comparison
- **Static signal browser**: An editorial dashboard — synthesized headline, ranked movers feed with period-comparison charts, detail drawer with quarterly history, command-palette search, and links to each filing's official Senate record
- **Zero infrastructure cost**: Runs entirely on GitHub (Actions + Pages + Releases)

## Architecture

```
GitHub Actions (daily cron)
    │
    ├── Download DB from GitHub Release
    ├── Ingest new LDA filings
    ├── Extract topics/entities/legislation with deterministic rules
    ├── Compute trends and alerts
    ├── Export JSON to docs/data/
    ├── Upload DB back to Release
    └── Commit JSON exports → GitHub Pages
```

## Data Sources

- **Senate LDA Filings**: [Senate Lobbying Disclosure](https://lda.senate.gov/filings/public/filing/search/)
- Covers all federal lobbying activity disclosures

## Methodology Notes

- Mentions are activity-level tags extracted from lobbying activity descriptions, not unique filing counts.
- Trend comparisons are directional signals for exploration, not causal claims about lobbying spend or policy outcomes.
- Filing volume is seasonal because quarterly LDA reports cluster around statutory filing deadlines.
- Associated income is filing income connected to matching activity tags; it should not be read as causal spend on a single topic.

## Local Development

```bash
# Install dependencies
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt

# Optional: set LDA_API_KEY for higher Senate API rate limits
export LDA_API_KEY=your_key

# Run full refresh
python 07_refresh.py

# Or run individual steps
python 01_ingest.py              # Download new filings
python 12_extract_rules.py extract --batch-size 2000000
python 08_trends.py export       # Generate JSON exports
```

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `01_ingest.py` | Download and store filings from the Senate LDA API |
| `06_extract.py` | Legacy optional Gemini extraction helper |
| `12_extract_rules.py` | Deterministic no-LLM extraction + candidate mining + gap reports |
| `07_refresh.py` | Orchestrate full refresh cycle |
| `08_trends.py` | Compute trends and generate alerts |
| `scripts/make_release_db.py` | Produce the slimmed DB copy uploaded to the GitHub Release for CI |

## Deterministic Topic Workflow (No LLM)

Use this workflow to maximize transparent, auditable extraction coverage without model calls:

```bash
# 1) Run rule-based extraction (L0 issue code + L1 coarse + L2 specific topics)
python 12_extract_rules.py extract --batch-size 2000000

# 2) Inspect current coverage and top topics
python 12_extract_rules.py stats

# 3) Sample misses from processed rows (where L2 rules did not fire)
python 12_extract_rules.py sample-unmapped --mode processed_unmapped --limit 30

# 4) Mine candidate terms from misses by issue_code
python 12_extract_rules.py mine-candidates \
  --scope processed_unmapped \
  --per-code-cap 12000 \
  --min-doc-freq 50 \
  --min-lift 2.5 \
  --top-k 30

# 5) Generate low-coverage gap report for rule expansion
python 12_extract_rules.py gap-report --max-codes 25 --terms-per-code 15

# 6) Re-run only selected weak issue codes after updating rules
python 12_extract_rules.py extract \
  --refresh-existing \
  --issue-codes CON,GAM,ART,BNK,SPO,UNM,TOB,RET
```

Rule dictionary lives at `rules/topic_rules.json`.

Coverage levels:
- **L0**: `issue_code` from Senate filings (100% of activities)
- **L1**: coarse topic mapped from `issue_code`
- **L2**: rule-derived topics in tiers:
  - `strict`: high-evidence matches (acts/programs, phrase-level matches, or multi-hit rule support)
  - `relaxed`: code-matched single-keyword evidence for broad-but-reasonable assignment
  - `fallback`: broad `general_*` label derived from LDA `issue_code` when no strict/relaxed topic is found

## Database

SQLite database stored in GitHub Releases (not in repo due to size). Contains:
- `filings`: Core filing metadata
- `registrants`: Lobbying firms
- `clients`: Clients being represented
- `activities`: Individual lobbying activities
- `activity_extractions_rules`: deterministic topic/entity/legislation extraction used by the dashboard
- `activity_extractions`: legacy LLM extraction table retained for historical comparison

## Preview the Dashboard Locally

The dashboard is a static app served from `docs/` (GitHub Pages). To preview it locally:

```bash
cd docs
python -m http.server 8000
```

Then open `http://localhost:8000` in your browser.

## License

MIT
