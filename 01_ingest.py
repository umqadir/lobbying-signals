"""Ingest lobbying filings from the LDA Senate REST API.

API documentation: https://lda.senate.gov/api/
Rate limits:
  - Unauthenticated: 15/minute
  - With API key: 120/minute

Set LDA_API_KEY env var for faster ingestion.
"""

import os
import time
from datetime import datetime, timedelta

import httpx

from config import DATA_DIR
from db import (
    get_db, init_db, get_or_create_registrant, get_or_create_client,
    insert_filing, insert_activity, recompute_is_current
)

API_BASE = "https://lda.senate.gov/api/v1"
PAGE_SIZE = 25  # API caps at 25 results per page
LDA_API_KEY = os.getenv("LDA_API_KEY", "")

# LDA report-period filing types for quarter n (n = 1..4), verified against
# https://lda.senate.gov/api/v1/constants/filing/filingtypes/:
#   QnY   original quarterly report (activity / no-activity)
#   nA/nAY    amendment — a COMPLETE restatement that supersedes the original
#   nT/nTY    termination report — filer's final-period activity
#   n@/n@Y    termination amendment — restatement of a termination
# Registration types (RR/RA) are out of scope; they aren't period reports.
# The API does not accept a comma-separated filing_type param (confirmed:
# it 400s), so each type is swept as its own request series.
def _report_types_for_quarter(quarter: int) -> list[str]:
    n = quarter
    return [f"Q{n}", f"{n}A", f"{n}AY", f"{n}T", f"{n}TY", f"{n}@", f"{n}@Y"]


def _non_original_types_for_quarter(quarter: int) -> list[str]:
    n = quarter
    return [f"{n}A", f"{n}AY", f"{n}T", f"{n}TY", f"{n}@", f"{n}@Y"]


def _prev_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 4:
        return year + 1, 1
    return year, quarter + 1

def get_headers() -> dict:
    """Get request headers, including auth if API key is set."""
    headers = {}
    if LDA_API_KEY:
        headers["Authorization"] = f"Token {LDA_API_KEY}"
    return headers

# Rate limit delay: 0.5s with key (120/min), 4s without (15/min)
RATE_LIMIT_DELAY = 0.5 if LDA_API_KEY else 4.0


def fetch_filings_page(year: int, filing_type: str, page: int = 1, max_retries: int = 5,
                       posted_after: str = None) -> dict:
    """Fetch a page of filings from the API with retry logic.

    filing_type is one of the codes from _report_types_for_quarter (e.g.
    "Q1", "1A", "1AY", "1T", ...), or None to fetch every type for the year.
    posted_after (YYYY-MM-DD) filters server-side to filings POSTED on or
    after that date — the cheap way to sweep for late arrivals against old
    report periods without re-paginating the entire year.
    """
    params = {
        "filing_year": year,
        "page": page,
        "page_size": PAGE_SIZE,
    }
    if filing_type is not None:
        params["filing_type"] = filing_type
    if posted_after is not None:
        params["filing_dt_posted_after"] = posted_after

    for attempt in range(max_retries):
        try:
            response = httpx.get(
                f"{API_BASE}/filings/",
                params=params,
                headers=get_headers(),
                timeout=60,
                follow_redirects=True
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Rate limited
                wait_time = 2 ** attempt * 5  # 5, 10, 20, 40, 80 seconds
                print(f"    Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                raise
    raise Exception(f"Failed after {max_retries} retries")


def parse_api_filing(filing: dict) -> dict | None:
    """Parse a filing from the API response."""
    filing_id = filing.get("filing_uuid")
    if not filing_id:
        return None

    # Parse year and quarter
    year = filing.get("filing_year")
    period = filing.get("filing_period")
    quarter = parse_quarter(period)

    if not year or not quarter:
        return None

    # Parse income
    income = filing.get("income") or filing.get("expenses") or 0
    if isinstance(income, str):
        income = float(income.replace(",", "").replace("$", "")) if income else 0

    # Registrant
    registrant = filing.get("registrant", {})
    registrant_id = registrant.get("id") or registrant.get("registrant_id")
    registrant_name = registrant.get("name") or registrant.get("registrant_name", "")

    # Client
    client = filing.get("client", {})
    client_id = client.get("id") or client.get("client_id")
    client_name = client.get("name") or client.get("client_name", "")

    # Lobbying activities
    activities = []
    for activity in filing.get("lobbying_activities", []):
        description = activity.get("description") or activity.get("specific_issues") or ""
        issue_code = activity.get("general_issue_code") or ""
        # Government entities include houses and agencies
        entities = activity.get("government_entities", [])
        entity_names = [e.get("name", "") for e in entities]
        houses = ",".join(n for n in entity_names if "HOUSE" in n.upper() or "SENATE" in n.upper())
        agencies = ",".join(n for n in entity_names if "HOUSE" not in n.upper() and "SENATE" not in n.upper())

        if description:
            activities.append({
                "description": description,
                "issue_code": issue_code,
                "houses": houses,
                "agencies": agencies
            })

    filing_date = filing.get("dt_posted") or filing.get("filing_date")
    filing_type = filing.get("filing_type")

    return {
        "filing_id": str(filing_id),
        "year": year,
        "quarter": quarter,
        "income": income,
        "filing_date": filing_date,
        "filing_type": filing_type,
        "registrant_id": str(registrant_id) if registrant_id else None,
        "registrant_name": registrant_name,
        "client_id": str(client_id) if client_id else None,
        "client_name": client_name,
        "activities": activities
    }


def parse_quarter(period: str) -> int | None:
    """Parse period string to quarter number."""
    if not period:
        return None
    period = period.lower().strip()
    if "1st" in period or "first" in period or period == "q1":
        return 1
    elif "2nd" in period or "second" in period or period == "q2":
        return 2
    elif "3rd" in period or "third" in period or period == "q3":
        return 3
    elif "4th" in period or "fourth" in period or period == "q4":
        return 4
    return None


def load_filings_to_db(filings: list[dict]):
    """Load parsed filings into SQLite database."""
    loaded = 0
    with get_db() as conn:
        for f in filings:
            try:
                existing = conn.execute(
                    "SELECT id FROM filings WHERE sopr_filing_id = ?",
                    (f.get("filing_id"),)
                ).fetchone()
                if existing:
                    continue

                if not f.get("registrant_id") or not f.get("registrant_name"):
                    continue
                if not f.get("client_id") or not f.get("client_name"):
                    continue

                reg_id = get_or_create_registrant(
                    conn, f["registrant_id"], f["registrant_name"]
                )

                client_id = get_or_create_client(
                    conn, f["client_id"], f["client_name"]
                )

                filing_db_id = insert_filing(
                    conn,
                    f["filing_id"],
                    reg_id,
                    client_id,
                    f["year"],
                    f["quarter"],
                    f.get("income"),
                    None,
                    f.get("filing_date"),
                    f.get("filing_type")
                )

                for activity in f.get("activities", []):
                    insert_activity(
                        conn,
                        filing_db_id,
                        activity["description"],
                        activity.get("issue_code"),
                        activity.get("houses"),
                        activity.get("agencies")
                    )

                loaded += 1

            except Exception as e:
                print(f"Error loading filing {f.get('filing_id')}: {e}")
                continue

    return loaded


def _ingest_filing_type(year: int, filing_type: str) -> int:
    """Fetch and load all filings of one filing_type for a year, loading
    incrementally. Low-volume types (amendments/terminations) are a handful
    of pages; the original Q{n} sweep is the bulk of the traffic."""
    total_loaded = 0
    page = 1
    batch = []
    BATCH_SIZE = 100  # Load to DB every 100 filings

    while True:
        if page % 50 == 1:
            print(f"    [{filing_type}] page {page}... ({total_loaded} loaded)")
        try:
            data = fetch_filings_page(year, filing_type, page)
        except httpx.HTTPStatusError as e:
            print(f"    [{filing_type}] API error: {e}")
            break
        except Exception as e:
            print(f"    [{filing_type}] Error: {e}")
            break

        results = data.get("results", [])
        if not results:
            break

        for filing_data in results:
            filing = parse_api_filing(filing_data)
            if filing:
                batch.append(filing)

        # Load batch to DB incrementally
        if len(batch) >= BATCH_SIZE:
            loaded = load_filings_to_db(batch)
            total_loaded += loaded
            batch = []

        # Check for next page
        if not data.get("next"):
            break

        page += 1
        time.sleep(RATE_LIMIT_DELAY)

    # Load remaining batch
    if batch:
        loaded = load_filings_to_db(batch)
        total_loaded += loaded

    return total_loaded


def ingest_quarter(year: int, quarter: int, filing_types: list[str] = None) -> int:
    """Fetch and load all report filings for a (year, quarter) report period,
    then recompute is_current for that period.

    filing_types defaults to the full sweep for the period — the original
    Q{n} report plus amendments ({n}A/{n}AY), terminations ({n}T/{n}TY), and
    termination amendments ({n}@/{n}@Y), all of which share the same
    (year, quarter) report-period metadata even though they may be filed
    months apart. Pass a narrower list (see _non_original_types_for_quarter)
    to sweep only the non-original types, e.g. for trailing-amendment or
    historical-backfill sweeps that skip the already-ingested originals.
    """
    types = filing_types if filing_types is not None else _report_types_for_quarter(quarter)
    print(f"Ingesting {year} Q{quarter} ({', '.join(types)})...")

    total_loaded = 0
    counts_by_type = {}
    for filing_type in types:
        loaded = _ingest_filing_type(year, filing_type)
        counts_by_type[filing_type] = loaded
        total_loaded += loaded

    # Supersede recomputation for exactly the report period just touched —
    # cheap because it's scoped, and correct regardless of which types were
    # actually swept (an amendment ingested now may supersede an original
    # ingested in an earlier run).
    with get_db() as conn:
        recompute_is_current(conn, year, quarter)

    print(f"  Loaded {total_loaded} filings to database {counts_by_type}")
    return total_loaded


def ingest_posted_after(posted_after: str, start_year: int = 2020) -> int:
    """Sweep for filings POSTED since a cutoff date against ANY report period
    from start_year on — the long-tail safety net.

    The daily refresh only watches a ~6-quarter trailing window, but the LDA
    record keeps changing outside it: amendments arrive years after the fact
    and delinquent originals surface (measured May-Jul 2026: ~100 filings
    posted against 2021-2024 report periods in ten weeks). Filtering
    server-side by filing_dt_posted_after makes this sweep a few dozen pages
    instead of re-paginating ~500K records, which exceeds the 6-hour CI job
    limit at the API's 25-per-page cap.

    Registrations (RR/RA) are skipped — they carry no quarterly report
    period. Ends with a global is_current recompute so late amendments
    supersede whatever they correct.
    """
    init_db()
    current_year = datetime.now().year
    total_loaded = 0
    BATCH_SIZE = 100

    for year in range(start_year, current_year + 1):
        page = 1
        batch = []
        year_loaded = 0
        year_seen = 0
        while True:
            try:
                data = fetch_filings_page(year, None, page, posted_after=posted_after)
            except Exception as e:
                print(f"  [{year}] API error on page {page}: {e}")
                break

            results = data.get("results", [])
            if not results:
                break

            for filing_data in results:
                year_seen += 1
                if (filing_data.get("filing_type") or "").upper() in ("RR", "RA"):
                    continue
                filing = parse_api_filing(filing_data)
                if filing:
                    batch.append(filing)

            if len(batch) >= BATCH_SIZE:
                year_loaded += load_filings_to_db(batch)
                batch = []

            if not data.get("next"):
                break
            page += 1
            time.sleep(RATE_LIMIT_DELAY)

        if batch:
            year_loaded += load_filings_to_db(batch)
        total_loaded += year_loaded
        print(f"  {year}: {year_seen} filings posted since {posted_after}, {year_loaded} new")

    with get_db() as conn:
        recompute_is_current(conn)
    print(f"Posted-after sweep complete: {total_loaded} new filings; is_current recomputed globally.")
    return total_loaded


def ingest_year(year: int):
    """Ingest all quarters for a year."""
    init_db()
    total = 0
    for quarter in range(1, 5):
        try:
            total += ingest_quarter(year, quarter)
        except Exception as e:
            print(f"Failed to ingest {year} Q{quarter}: {e}")
    return total


def ingest_range(start_year: int, end_year: int):
    """Ingest a range of years."""
    init_db()
    for year in range(start_year, end_year + 1):
        ingest_year(year)


def ingest_latest():
    """Sweep the two most recent report quarters for new filings (all report
    types — originals, amendments, terminations, termination amendments),
    plus a trailing-amendments sweep of the 4 quarters before that.

    Filings for a report period keep arriving for weeks after the statutory
    deadline (amendments and late filers trail for months, sometimes over a
    year for terminations/amendments specifically), so sweeping only the
    newest quarter silently drops stragglers. ingest_quarter dedupes by
    sopr_filing_id, so re-sweeping is cheap in DB terms and idempotent.
    """
    init_db()
    now = datetime.now()

    # Reports arriving now cover the most recently COMPLETED quarter (Q2
    # reports are due Jul 20, etc.), so that quarter and the one before it
    # are where new filings land.
    cal_q = (now.month - 1) // 3 + 1
    if cal_q == 1:
        sweep = [(now.year - 1, 4), (now.year - 1, 3)]
    elif cal_q == 2:
        sweep = [(now.year, 1), (now.year - 1, 4)]
    else:
        sweep = [(now.year, cal_q - 1), (now.year, cal_q - 2)]

    for y, qq in sweep:
        try:
            count = ingest_quarter(y, qq)
            print(f"Swept {y} Q{qq}: {count} new filings")
        except Exception as e:
            print(f"Could not ingest {y} Q{qq}: {e}")
            continue

    # Trailing-amendments sweep: amendments/terminations for older periods
    # keep trailing in for months after the period itself is "done", so also
    # sweep non-original types for the 4 report quarters before the oldest
    # quarter swept above (idempotent — dedupe makes re-sweeping cheap).
    ty, tq = sweep[-1]
    trailing = []
    for _ in range(4):
        ty, tq = _prev_quarter(ty, tq)
        trailing.append((ty, tq))

    for y, qq in trailing:
        try:
            types = _non_original_types_for_quarter(qq)
            count = ingest_quarter(y, qq, filing_types=types)
            print(f"Swept trailing amendments {y} Q{qq}: {count} new filings")
        except Exception as e:
            print(f"Could not sweep trailing amendments {y} Q{qq}: {e}")
            continue


def backfill_non_original(start_year: int):
    """Historical backfill: sweep only the non-original report types
    (amendments/terminations/termination amendments) for every quarter from
    start_year through the current (in-progress) quarter, then run a single
    global recompute of is_current across the whole table.

    Intended as a one-time catch-up after this feature ships — originals
    were already ingested by the existing pipeline, so only the previously
    excluded types need a historical sweep.
    """
    init_db()
    now = datetime.now()
    end_year = now.year
    end_quarter = (now.month - 1) // 3 + 1

    y, q = start_year, 1
    total = 0
    per_quarter = []
    while (y, q) <= (end_year, end_quarter):
        types = _non_original_types_for_quarter(q)
        try:
            count = ingest_quarter(y, q, filing_types=types)
        except Exception as e:
            print(f"Failed to backfill {y} Q{q}: {e}")
            count = 0
        per_quarter.append((y, q, count))
        total += count
        y, q = _next_quarter(y, q)

    print("\nBackfill per-quarter counts (non-original types):")
    for y, q, count in per_quarter:
        print(f"  {y} Q{q}: {count}")
    print(f"Total non-original filings ingested: {total}")

    print("\nRunning global is_current recompute...")
    with get_db() as conn:
        recompute_is_current(conn)
    print("Global recompute complete.")
    return total


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "recompute-current":
        init_db()
        with get_db() as conn:
            recompute_is_current(conn)
        print("Recomputed is_current for the entire filings table.")
    elif len(sys.argv) >= 2 and sys.argv[1] == "full-sweep":
        # Semiannual safety net (see ingest_posted_after). Default cutoff of
        # 400 days comfortably overlaps the semiannual cadence; --posted-after
        # overrides for a deeper or shallower sweep.
        start_year = 2020
        if "--start-year" in sys.argv:
            idx = sys.argv.index("--start-year")
            start_year = int(sys.argv[idx + 1])
        if "--posted-after" in sys.argv:
            idx = sys.argv.index("--posted-after")
            posted_after = sys.argv[idx + 1]
        else:
            posted_after = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        ingest_posted_after(posted_after, start_year)
    elif len(sys.argv) >= 2 and sys.argv[1] == "backfill-non-original":
        start_year = 2020
        if "--start-year" in sys.argv:
            idx = sys.argv.index("--start-year")
            start_year = int(sys.argv[idx + 1])
        backfill_non_original(start_year)
    elif len(sys.argv) >= 3 and sys.argv[1].isdigit() and sys.argv[2].isdigit():
        year = int(sys.argv[1])
        quarter = int(sys.argv[2])
        init_db()
        ingest_quarter(year, quarter)
    elif len(sys.argv) == 2:
        arg = sys.argv[1]
        if arg == "latest":
            ingest_latest()
        else:
            year = int(arg)
            ingest_year(year)
    else:
        print("Usage: python 01_ingest.py <year> [quarter]")
        print("       python 01_ingest.py 2024 1                        # Ingest Q1 2024 (all report types)")
        print("       python 01_ingest.py 2024                          # Ingest all of 2024")
        print("       python 01_ingest.py latest                        # Ingest most recent quarters + trailing amendments")
        print("       python 01_ingest.py recompute-current             # Recompute is_current for the whole table")
        print("       python 01_ingest.py backfill-non-original --start-year 2020")
        print("                                                          # Historical backfill of amendments/terminations")
        print("       python 01_ingest.py full-sweep --start-year 2020  # Re-sweep every quarter, all report types")
        sys.exit(1)
