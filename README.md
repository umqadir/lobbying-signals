# Lobbying Signals

Automated detection of directional signals in federal lobbying disclosure data. Tracks organizations, topics, agencies, legislation, and recent filings from Senate LDA disclosures.

## Live Dashboard

**[View Dashboard](https://umqadir.github.io/lobbying-signals/)**

Updated daily via GitHub Actions. The daily run ingests everything posted to
the LDA system in the last 7 days — originals, amendments, and terminations
against any report period since 2020 — via the API's posted-date filter, so
even a correction to a years-old quarter lands within a day (the window
extends automatically if a CI gap left the database stale). A monthly drift
audit re-fetches a large random sample of stored filings (~30K, sized to the
CI window) and compares them against the live API, flagging any in-place
edits or deletions a posted-date filter can't see; none has been observed.

## Features

- **Real-time LDA ingestion**: Downloads and stores Senate lobbying disclosure filings via the LDA API; coverage runs from 2020 to the present
- **Deterministic extraction**: Uses versioned rules, LDA issue codes, regexes, and dictionaries to extract topics, entities, and legislation without model calls
- **Trend detection**: Every view shares the same two year-over-year, report-quarter comparison frames — the latest complete quarter vs the same quarter a year earlier (default), and the current partial quarter so far vs the same point in last year's filing cycle (a freshness lens). There are no rolling day-windows: LDA disclosure is quarterly, so rolling windows would mostly measure filing-clerk timing
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

- Mentions are activity-level tags extracted from lobbying activity descriptions, not unique filing counts.
- Trend comparisons are directional signals for exploration, not causal claims about lobbying spend or policy outcomes.
- **Comparison frames**: every view uses the same two year-over-year frames, both defined on report quarters (the filing's year/quarter metadata, not its submission date). `quarter` compares the latest COMPLETE report quarter to the same quarter a year earlier — a quarter counts as complete once ~40 days past its calendar end, well past the 20th-of-the-following-month statutory deadline, so late filers don't read as a false drop. `qtd` compares the current partial quarter's filings posted through the data-through date against last year's same-quarter filings posted by the same point in the cycle — like-for-like even mid-cycle, flagged as a small sample early on. Rolling 30/90-day filing-date windows were removed: LDA is a quarterly regime, so they measured filing-clerk timing rather than lobbying activity.
- Filing volume is seasonal because quarterly LDA reports cluster around statutory filing deadlines.
- Associated income is filing income connected to matching activity tags; it should not be read as causal spend on a single topic.
- **Organization spend**: the Organizations view (`compute_client_movers()` in `08_trends.py`, exported to `docs/data/clients.json`) sums each client's reported filing income/expenses per report quarter under both frames above. Name variants for one organization (legal-suffix differences, "on behalf of" filers, former names) are folded together by `clients_norm.canonical_client_key`; see `scripts/test_canonicalize_client.py` for the regression cases. These are quarterly LDA totals, not issue-allocated — an organization's total isn't split across the topics it lobbied on.
- **Amendments and termination reports**: ingestion covers original quarterly reports (`Q1`-`Q4`), amendments (`1A`-`4A`, plus no-activity variants), termination reports (`1T`-`4T`), and termination amendments (`1@`-`4@`), all keyed to the same report period as the original. Every `filings` row carries an `is_current` flag; for each (registrant, client, report quarter) group, only the most recently filed row is current, and every metric in `08_trends.py` reads current rows only. An amendment is treated as a complete restatement — its figures replace the original's, including a zeroed-out income if a no-activity amendment supersedes a report that had reported income.

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

## Monthly alias review (Claude)

Legislation tags are normalized into stable identities by `normalize_legislation`
in `08_trends.py` — folding a law's name, bill number, and public-law number into
one entity and scoping bare bill numbers to their Congress. The landmark-law alias
table is hand-maintained, so `.github/workflows/legislation-review.yml` runs a
monthly audit and has Claude propose additions:

1. `scripts/audit_legislation_aliases.py` scans every legislation tag and reports
   the highest-volume identities left unmapped (and the largest tags dropped as
   noise, to catch a real law being discarded).
2. The Claude Code GitHub Action reviews that report, researches each candidate,
   and opens a PR adding well-established mappings plus test cases — leaving
   year-ambiguous titles (appropriations, NDAAs) and shared-vehicle bill numbers
   unmapped on purpose.

This runs through a **Claude Pro/Max subscription, not the metered API**. One-time
setup (repo admin):

```bash
# 1. Install the Claude GitHub App on this repo
open https://github.com/apps/claude

# 2. Generate a subscription-scoped token (opens a browser auth flow)
claude setup-token

# 3. Add the printed token as a repository secret
gh secret set CLAUDE_CODE_OAUTH_TOKEN --body '<token from step 2>'
```

Trigger a run manually with **Actions → Monthly Legislation Alias Review → Run
workflow** once the secret is set. The audit script is also runnable locally:
`python scripts/audit_legislation_aliases.py` (needs `data/filings.db`).

Regression guard: `python scripts/test_normalize_legislation.py` (49 cases). Client-name canonicalization has its own regression guard: `python scripts/test_canonicalize_client.py`.

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
