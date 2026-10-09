# LDA Senate API Reference

## Rate Limits
- **Unauthenticated:** 15 requests/minute
- **API Key (registered):** 120 requests/minute

## Authentication
Register at: https://lda.gov/api/register/

Get API key via POST:
```http
POST https://lda.gov/api/auth/login/
Content-Type: application/json

{"username": "your_username", "password": "your_password"}
```

Use in requests:
```http
Authorization: Token z944b09199c62bcf9418ad846dd0e4bbdfc6ee4b
```

## Pagination
- Max 25 results per page (fixed, cannot increase)
- Must include at least one filter param (e.g., `filing_year`) to paginate beyond page 1

## Key Endpoints

### Filings (LD-2 quarterly reports)
```
GET /api/v1/filings/
```
Parameters:
- `filing_year` - e.g., 2025
- `filing_type` - Q1, Q2, Q3, Q4, RR (registration), etc.
- `registrant_id` - Filter by registrant
- `client_id` - Filter by client
- `ordering` - Sort by field (prefix with `-` for descending)

### Filing Types
- Q1, Q2, Q3, Q4 - Quarterly reports
- RR - Registration
- 1A, 2A, 3A, 4A - Amendments
- 1T, 2T, 3T, 4T - Terminations

### Constants (no rate limit)
```
/api/v1/constants/filing/filingtypes/
/api/v1/constants/filing/lobbyingactivityissues/
/api/v1/constants/filing/governmententities/
```

## Important Notes
- Use lda.gov directly for the API and public filing documents.
- Government entities broken down per activity only for filings after 2/14/2021

`filings` stores `expenses`, `expenses_method`, `is_self_filer`, and `details_fetched_at`.
`income` retains the existing income-or-expenses total.
`filing_lobbyists` stores per-activity names, LDA IDs, covered positions, and new flags.
The API reports top-level `income`, `expenses`, `expenses_method`, and `expenses_method_display`.
Each `lobbying_activities[].lobbyists[]` entry contains a nested `lobbyist` identity
(`id`, `prefix`, `first_name`, `nickname`, `middle_name`, `last_name`, `suffix`),
with `covered_position` and `new` alongside it. Covered-position placeholders
(`N/A`, `NA`, `None`, `-`, and blank text, ignoring case and surrounding whitespace) mean no covered position.
Run `python 01_ingest.py backfill-details` for dashboard movers in both frames and quarter legs.
Use repeated `--uuid UUID` options to select specific filings.
Defaults: 600 HTTP attempts including retries and eight minutes; rate limits and backoff apply.
Completed fetches are marked even when `expenses_method` is absent; the run logs that count.
Malformed details remain incomplete without blocking core ingest; refresh backfill is nonfatal.
