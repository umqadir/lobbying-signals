"""Ingest lobbying filings from the LDA Senate REST API.

API documentation: https://lda.senate.gov/api/
Rate limits:
  - Unauthenticated: 15/minute
  - With API key: 120/minute

Set LDA_API_KEY env var for faster ingestion.
"""

import os
import time
from datetime import datetime

import httpx

from config import DATA_DIR
from db import (
    get_db, init_db, get_or_create_registrant, get_or_create_client,
    insert_filing, insert_activity
)

API_BASE = "https://lda.senate.gov/api/v1"
PAGE_SIZE = 25  # API caps at 25 results per page
LDA_API_KEY = os.getenv("LDA_API_KEY", "")

def get_headers() -> dict:
    """Get request headers, including auth if API key is set."""
    headers = {}
    if LDA_API_KEY:
        headers["Authorization"] = f"Token {LDA_API_KEY}"
    return headers

# Rate limit delay: 0.5s with key (120/min), 4s without (15/min)
RATE_LIMIT_DELAY = 0.5 if LDA_API_KEY else 4.0


def fetch_filings_page(year: int, quarter: int, page: int = 1, max_retries: int = 5) -> dict:
    """Fetch a page of filings from the API with retry logic."""
    # API uses Q1, Q2, Q3, Q4 as filing types for quarterly reports
    filing_type = f"Q{quarter}"

    params = {
        "filing_year": year,
        "filing_type": filing_type,
        "page": page,
        "page_size": PAGE_SIZE,
    }

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

    return {
        "filing_id": str(filing_id),
        "year": year,
        "quarter": quarter,
        "income": income,
        "filing_date": filing_date,
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
                    f.get("filing_date")
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


def ingest_quarter(year: int, quarter: int):
    """Fetch and load all filings for a quarter, loading incrementally."""
    print(f"Ingesting {year} Q{quarter}...")

    total_loaded = 0
    page = 1
    batch = []
    BATCH_SIZE = 100  # Load to DB every 100 filings

    while True:
        if page % 50 == 1:
            print(f"  Page {page}... ({total_loaded} loaded)")
        try:
            data = fetch_filings_page(year, quarter, page)
        except httpx.HTTPStatusError as e:
            print(f"  API error: {e}")
            break
        except Exception as e:
            print(f"  Error: {e}")
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

    print(f"  Loaded {total_loaded} filings to database")
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
    """Sweep the two most recent report quarters for new filings.

    Filings for a report period keep arriving for weeks after the statutory
    deadline (amendments and late filers trail for months), so sweeping only
    the newest quarter silently drops the prior quarter's stragglers.
    ingest_quarter dedupes by sopr_filing_id, so re-sweeping is cheap in DB
    terms and idempotent.
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


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
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
        print("       python 01_ingest.py 2024 1     # Ingest Q1 2024")
        print("       python 01_ingest.py 2024       # Ingest all of 2024")
        print("       python 01_ingest.py latest     # Ingest most recent quarter")
        print()
        print("Ingesting 2024 by default...")
        ingest_year(2024)
