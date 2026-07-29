# Lobbying Signals

Automated detection of directional signals in federal lobbying disclosure data. Tracks organizations, topics, agencies, legislation, and recent filings from Senate LDA disclosures.

## Live Dashboard

**[View Dashboard](https://umqadir.github.io/lobbying-signals/)**

Updated daily via GitHub Actions. The daily run ingests everything posted to the LDA system in the last 7 days: originals, amendments, and terminations against any report period since 2020, by posted-date filter. The window extends automatically after a CI gap. A monthly drift audit re-fetches ~30K stored filings and compares them against the live API for in-place edits and deletions.

## Features

- **Real-time LDA ingestion**: Downloads and stores Senate lobbying disclosure filings via the LDA API; coverage runs from 2020 to the present
- **Deterministic extraction**: Uses versioned rules, LDA issue codes, regexes, and dictionaries to extract topics, entities, and legislation without model calls
- **Trend detection**: Every view shares the same two year-over-year, report-quarter comparison frames — the latest complete quarter vs the same quarter a year earlier (default), and the current partial quarter so far vs the same point in last year's filing cycle
- **Organization spend movers**: Tracks which organizations raised or cut reported lobbying dollars under the same two frames, with name-variant folding so one organization's filings aren't split across spellings
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

- Mentions are activity-level tags from lobbying activity descriptions, not unique filing counts.
- Trend comparisons are directional signals, not causal claims.
- Comparison frames are defined on report quarters, not submission dates. `quarter`: latest complete report quarter against the same quarter a year earlier, a quarter counting as complete ~40 days past its calendar end. `qtd`: current partial quarter through the data-through date against last year's same-quarter filings posted by the same point in the cycle, flagged as a small sample early on. No rolling day-windows.
- Filing volume is seasonal around statutory filing deadlines.
- Associated income is filing income connected to matching activity tags, not issue-allocated spend.
- Organization spend: `compute_client_movers()` in `08_trends.py`, exported to `docs/data/clients.json`, sums each client's reported filing income and expenses per report quarter under both frames. Name variants (legal suffixes, "on behalf of" filers, former names) fold together via `clients_norm.canonical_client_key`; regression cases in `scripts/test_canonicalize_client.py`. Quarterly LDA totals, not split across topics.
- Amendments and terminations: ingestion covers `Q1`-`Q4`, amendments `1A`-`4A` including no-activity variants, terminations `1T`-`4T`, and termination amendments `1@`-`4@`, all keyed to the original report period. Every `filings` row carries `is_current`; per (registrant, client, report quarter) only the latest filed row is current, and all metrics in `08_trends.py` read current rows only. An amendment restates completely, including a zeroed income when a no-activity amendment supersedes a reported one.

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
| `08_trends.py` | Compute trends, organization spend movers, and generate alerts |
| `clients_norm.py` | Client-name canonicalization and display-name rendering used by `08_trends.py` |
| `scripts/make_release_db.py` | Produce the slimmed DB copy uploaded to the GitHub Release for CI |

## Deterministic Topic Workflow (No LLM)

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
  - `strict`: acts/programs, phrase-level matches, or multi-hit rule support
  - `relaxed`: code-matched single-keyword evidence
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
