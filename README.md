# Lobbying Signals

Year-over-year signals from Senate Lobbying Disclosure Act (LDA) filings: topics, agencies, legislation, organizations, recent filings.

Dashboard: https://umqadir.github.io/lobbying-signals/

## Data sources

| Source | Endpoint | Used for |
|---|---|---|
| Senate LDA API | https://lda.gov/api/v1 | Filings, registrants, clients, activities, lobbyists |
| GovInfo BILLSTATUS bulk XML | https://www.govinfo.gov/bulkdata/BILLSTATUS | Bill introduction dates and official titles, 116th Congress on |
| FRED CPIAUCSL | https://fred.stlouisfed.org/series/CPIAUCSL | CPI deflators |

| Parameter | Value |
|---|---|
| Report years | 2020 to present |
| Filing types | `Q1`-`Q4`, `1A`-`4A`, `1T`-`4T`, `1@`-`4@` |
| Daily ingest window | Posted in last 7 days; extended after a gap |
| Drift audit | Monthly, up to 40,000 stored filings re-fetched |

The Senate Office of Public Records cannot vouch for this data or analyses derived from it.

## Metrics

| Term | Definition |
|---|---|
| Mention | One activity-level tag from an activity description; not a filing count |
| Associated income | Income of filings with a matching activity tag; not issue-allocated |
| Organization spend | Reported income + expenses per client per report quarter (`compute_client_movers()`, `08_trends.py`) |
| Client key | `clients_norm.canonical_client_key()`; folds legal suffixes, on-behalf-of filers, former names |
| Current filing | Latest filed row per (registrant, client, report quarter); `filings.is_current = 1`; all metrics use current rows |
| `quarter` frame | Latest complete report quarter vs same quarter a year earlier; complete = ~40 days after quarter end |
| `qtd` frame | Current partial quarter through data-through date vs same quarter a year earlier, filings posted by the same point in the cycle |
| Bare bill number | Report-year Congress if introduced by posted date, else preceding Congress; explicit year/Congress and named acts take priority |
| Topic L0 / L1 / L2 | LDA `issue_code` / coarse topic from `issue_code` / rule-derived topic (`strict`, `relaxed`, `fallback`) from `rules/topic_rules.json` |

## Pipeline

| Script | Role |
|---|---|
| `07_refresh.py` | Daily entry point: ingest, rule extraction, bill reference, export, detail backfill |
| `01_ingest.py` | LDA ingestion; `full-sweep`, `coverage-check`, `audit-sample`, `backfill-details`, `<year> <quarter>` |
| `12_extract_rules.py` | Rule-based topic, entity, legislation extraction |
| `scripts/build_bill_reference.py` | GovInfo bill reference refresh |
| `08_trends.py` | Trends, alerts, organization movers; JSON export to `docs/data/` |
| `clients_norm.py` | Client keys and display names |
| `bill_reference.py`, `db.py`, `config.py` | Shared modules |
| `scripts/audit_client_identities.py` | Identity review report |
| `scripts/audit_legislation_aliases.py` | Legislation alias review report |
| `scripts/make_release_db.py` | Slimmed DB copy for the release asset |
| `scripts/test_*.py` | Offline tests |
| `02_`-`06_`, `09_`-`11_`, `llm.py` | Legacy, not in refresh path; `requirements-llm.txt` |

## Workflows

| Workflow | Schedule (UTC) | Action |
|---|---|---|
| `refresh.yml` | Daily 10:00; 03:30 and 21:30 if last run failed; Sun 06:00 coverage check; 3rd 06:00 drift audit | Update release DB and `docs/data/` |
| `legislation-review.yml` | 1st 12:00 | Claude proposes `08_trends.py` alias PR |
| `identity-review.yml` | 5th 12:00 | Claude proposes `clients_norm.py` alias PR |
| `pr-review.yml` | Mon 13:00 | Gate, review, merge or close automation PRs |
| `tests.yml` | Push / PR to `main` | `scripts/test_*.py` |

| Release asset (`data`) | Contents |
|---|---|
| `filings.db.zst` | Current DB |
| `filings.prev.db.zst` | DB before the latest refresh |
| `filings.YYYY-MM.db.zst` | Monthly copy, newest 3 kept |

## Database

| Table | Contents |
|---|---|
| `filings` | Filing metadata, income, expenses, `filing_type`, `is_current` |
| `registrants` | Registrants |
| `clients` | Clients |
| `activities` | Activities: issue code, description, houses, agencies |
| `filing_lobbyists` | Lobbyists per activity |
| `activity_extractions_rules` | Rule-based topics, entities, legislation per activity |
| `topic_candidate_terms` | Mined candidate rule terms |
| `bill_reference` | `(congress, type, number)` -> `introduced_date`, `title` |
| `bill_reference_sync` | `(congress, type)` -> `checked_at` |
| `audit_log` | Drift audit results |
| `issues`, `signals`, `migrations` | Legacy issue labels, legacy signals, schema migrations |

## Local use

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
gh release download data --pattern filings.db.zst -D data/ && zstd -d --rm data/filings.db.zst -o data/filings.db
.venv/bin/python 07_refresh.py
cd docs && python -m http.server 8000
```

| Variable | Use |
|---|---|
| `LDA_API_KEY` | Optional; higher LDA API rate limit |

## Corrections

GitHub issues: https://github.com/umqadir/lobbying-signals/issues

## License

MIT (`LICENSE`), code only. Data is from public LDA filings.
