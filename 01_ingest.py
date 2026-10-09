"""Ingest lobbying filings from the LDA Senate REST API.

API documentation: https://lda.gov/api/
Rate limits:
  - Unauthenticated: 15/minute
  - With API key: 120/minute

Set LDA_API_KEY env var for faster ingestion.
"""

import os
import json
import math
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config import PROJECT_ROOT
from db import (
    get_db, init_db, get_or_create_registrant, get_or_create_client,
    recompute_is_current, normalize_covered_position
)

API_BASE = "https://lda.gov/api/v1"
PAGE_SIZE = 25  # API caps at 25 results per page
LDA_API_KEY = os.getenv("LDA_API_KEY", "")

# LDA report-period filing types for quarter n (n = 1..4), verified against
# https://lda.gov/api/v1/constants/filing/filingtypes/:
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

    # Amounts and expenses_method are top-level fields. Partial or malformed
    # payloads must not block core ingest.
    details_complete = all(k in filing for k in ("expenses", "income", "lobbying_activities"))
    try:
        expenses = _parse_amount(filing.get("expenses"))
    except (ValueError, TypeError, OverflowError):
        expenses = None
        details_complete = False
    expenses_method = filing.get("expenses_method")
    if expenses_method is not None and not isinstance(expenses_method, str):
        expenses_method = None
        details_complete = False

    # Keep the existing income-or-expenses total for valid amounts.
    income = filing.get("income") or expenses or 0
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
    lobbyist_activities = []
    for index, activity in enumerate(filing.get("lobbying_activities") or []):
        description = activity.get("description") or activity.get("specific_issues") or ""
        issue_code = activity.get("general_issue_code") or ""
        # Government entities include houses and agencies
        entities = activity.get("government_entities") or []
        entity_names = [e.get("name", "") for e in entities]
        houses = ",".join(n for n in entity_names if "HOUSE" in n.upper() or "SENATE" in n.upper())
        agencies = ",".join(n for n in entity_names if "HOUSE" not in n.upper() and "SENATE" not in n.upper())

        # Each activity's lobbyists entry wraps a nested lobbyist identity,
        # with covered_position and new alongside it.
        lobbyists = []
        entries = activity.get("lobbyists")
        if "lobbyists" not in activity or (entries is not None and not isinstance(entries, list)):
            details_complete = False
            entries = []
        for entry in entries or []:
            person = entry.get("lobbyist") if isinstance(entry, dict) else None
            if not isinstance(person, dict):
                details_complete = False
                continue
            lobbyist_id = person.get("id")
            name_parts = [person.get(k) for k in
                          ("prefix", "first_name", "middle_name", "last_name", "suffix")]
            covered_position = entry.get("covered_position")
            is_new = entry.get("new")
            if (type(lobbyist_id) is not int or not 0 < lobbyist_id < 2 ** 63
                    or any(part is not None and not isinstance(part, str) for part in name_parts)
                    or (covered_position is not None and not isinstance(covered_position, str))
                    or (is_new is not None and not isinstance(is_new, bool))):
                details_complete = False
                continue
            name = " ".join(part or "" for part in name_parts)
            lobbyists.append({
                "lda_lobbyist_id": lobbyist_id,
                "name": " ".join(name.split()),
                "covered_position": normalize_covered_position(covered_position),
                "is_new": is_new,
            })
        parsed_activity = {
            "description": description, "issue_code": issue_code,
            "houses": houses, "agencies": agencies,
            "activity_index": index, "lobbyists": lobbyists,
        }
        lobbyist_activities.append(parsed_activity)
        if description:
            activities.append(parsed_activity)

    filing_date = filing.get("dt_posted") or filing.get("filing_date")
    filing_type = filing.get("filing_type")

    return {
        "filing_id": str(filing_id),
        "year": year,
        "quarter": quarter,
        "income": income,
        "expenses": expenses,
        "expenses_method": expenses_method,
        "is_self_filer": (True if expenses is not None else
                          False if filing.get("income") is not None else None),
        "details_complete": details_complete,
        "lobbyist_activities": lobbyist_activities,
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


def _parse_amount(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = float(value.replace(",", "").replace("$", ""))
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("unexpected expense amount")
    return float(value)


def store_filing_details(conn, filing_db_id: int, filing: dict):
    """Replace detail fields only; leave income and classified activities intact.

    Match activities by their stored content, rather than assuming API order
    still agrees with the DB after a historical dedupe or an in-place edit.
    Unmatched/blank activities retain their API index and a NULL activity_id.
    """
    conn.execute("""
        UPDATE filings SET expenses = ?, expenses_method = ?,
               is_self_filer = COALESCE(?, is_self_filer), details_fetched_at = ?
        WHERE id = ?
    """, (filing.get("expenses"), filing.get("expenses_method"),
          filing.get("is_self_filer"),
          datetime.now().isoformat() if filing.get("details_complete") else None,
          filing_db_id))
    stored = defaultdict(deque)
    for row in conn.execute("SELECT * FROM activities WHERE filing_id = ? ORDER BY id", (filing_db_id,)):
        key = tuple(row[k] or "" for k in ("description", "issue_code", "houses_lobbied", "agencies"))
        stored[key].append(row["id"])
    conn.execute("DELETE FROM filing_lobbyists WHERE filing_id = ?", (filing_db_id,))
    for activity in filing.get("lobbyist_activities", []):
        key = tuple(activity.get(k) or "" for k in ("description", "issue_code", "houses", "agencies"))
        activity_id = stored[key].popleft() if stored[key] else None
        for index, person in enumerate(activity["lobbyists"]):
            conn.execute("""
                INSERT INTO filing_lobbyists
                    (filing_id, activity_index, lobbyist_index, activity_id,
                     lda_lobbyist_id, name, covered_position, is_new)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (filing_db_id, activity["activity_index"], index, activity_id,
                  person["lda_lobbyist_id"], person["name"],
                  normalize_covered_position(person["covered_position"]), person["is_new"]))


def _try_store_filing_details(conn, filing_db_id: int, filing: dict):
    """A detail write failure must not roll back the core filing or activities."""
    conn.execute("SAVEPOINT filing_details")
    try:
        store_filing_details(conn, filing_db_id, filing)
    except Exception as e:
        conn.execute("ROLLBACK TO filing_details")
        print(f"Warning: Details skipped for filing {filing.get('filing_id')}: {e}")
    finally:
        conn.execute("RELEASE filing_details")


def load_filings_to_db(filings: list[dict]):
    """Load new filings, and capture details on every refetch of an existing UUID."""
    loaded = 0
    with get_db() as conn:
        for f in filings:
            try:
                existing = conn.execute(
                    "SELECT id FROM filings WHERE sopr_filing_id = ?", (f.get("filing_id"),)
                ).fetchone()
                if existing:
                    _try_store_filing_details(conn, existing["id"], f)
                    conn.commit()
                    continue
                if not f.get("registrant_id") or not f.get("registrant_name"):
                    continue
                if not f.get("client_id") or not f.get("client_name"):
                    continue
                reg_id = get_or_create_registrant(conn, f["registrant_id"], f["registrant_name"])
                client_id = get_or_create_client(conn, f["client_id"], f["client_name"])
                cur = conn.execute("""
                    INSERT INTO filings (sopr_filing_id, registrant_id, client_id,
                        year, quarter, income, filing_date, filing_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (f["filing_id"], reg_id, client_id, f["year"], f["quarter"],
                      f.get("income"), f.get("filing_date"), f.get("filing_type")))
                filing_db_id = cur.lastrowid
                for activity in f.get("activities", []):
                    conn.execute("""
                        INSERT INTO activities (filing_id, description, issue_code, houses_lobbied, agencies)
                        VALUES (?, ?, ?, ?, ?)
                    """, (filing_db_id, activity["description"], activity.get("issue_code"),
                          activity.get("houses"), activity.get("agencies")))
                _try_store_filing_details(conn, filing_db_id, f)
                conn.commit()
                loaded += 1
            except Exception as e:
                conn.rollback()
                print(f"Error loading filing {f.get('filing_id')}: {e}")
    return loaded


def dashboard_filing_uuids(conn, clients_path=None) -> set[str]:
    """All mover filings in both legs of each exported frame, including amendments.

    Use each frame's own mover set. Include whole quarters (also for QTD),
    all name variants and all registrants, rather than just example UUIDs.
    """
    from clients_norm import canonical_client_key

    path = Path(clients_path) if clients_path else PROJECT_ROOT / "docs/data/clients.json"
    data = json.loads(path.read_text())
    client_keys = {row["id"]: canonical_client_key(row["name"])
                   for row in conn.execute("SELECT id, name FROM clients")}
    wanted = set()
    for frame in data["frames"].values():
        keys = {m["key"] for group in ("risers", "fallers", "new_entrants")
                for m in frame.get(group, [])}
        for leg in ("current_quarter", "baseline_quarter"):
            period = frame[leg]
            for row in conn.execute("""
                SELECT sopr_filing_id, client_id FROM filings
                WHERE year = ? AND quarter = ? AND sopr_filing_id IS NOT NULL
            """, (period["year"], period["quarter"])):
                if client_keys.get(row["client_id"]) in keys:
                    wanted.add(row["sopr_filing_id"])
    return wanted


def backfill_details(uuids=None, max_requests: int = 600, max_minutes: float = 8,
                     clients_path=None) -> dict:
    """Resume missing dashboard details within a request and wall-clock budget.

    NULL expense methods and empty lobbyist rosters are legitimate. A complete
    response is marked by details_fetched_at so those filings won't loop forever.
    Retries count toward max_requests; persistent API trouble ends the run.
    """
    init_db()
    deadline = time.monotonic() + max_minutes * 60
    stats = {"eligible": 0, "requests": 0, "updated": 0, "errors": 0,
             "missing_expenses_method": 0}
    with get_db() as conn:
        wanted = set(uuids) if uuids is not None else dashboard_filing_uuids(conn, clients_path)
        pending = [row for row in conn.execute("""
            SELECT id, sopr_filing_id FROM filings WHERE details_fetched_at IS NULL ORDER BY id
        """) if row["sopr_filing_id"] in wanted]
        stats["eligible"] = len(pending)
        next_request = time.monotonic()
        with httpx.Client(headers=get_headers(), follow_redirects=True) as client:
            for row in pending:
                for attempt in range(3):
                    wait = max(0, next_request - time.monotonic())
                    if stats["requests"] >= max_requests or time.monotonic() + wait >= deadline:
                        print(f"Detail backfill: {stats} (budget reached)")
                        return stats
                    time.sleep(wait)
                    stats["requests"] += 1
                    remaining = deadline - time.monotonic()
                    try:
                        resp = client.get(f"{API_BASE}/filings/{row['sopr_filing_id']}/",
                                          timeout=max(0.01, min(10, remaining / 4)))
                        next_request = time.monotonic() + RATE_LIMIT_DELAY
                        resp.raise_for_status()
                        payload = resp.json()
                        if isinstance(payload, dict) and "expenses_method" not in payload:
                            stats["missing_expenses_method"] += 1
                        parsed = parse_api_filing(payload)
                        if not parsed or parsed["filing_id"] != row["sopr_filing_id"] or not parsed["details_complete"]:
                            raise ValueError("incomplete or unexpected filing detail response")
                        store_filing_details(conn, row["id"], parsed)
                        conn.commit()
                        stats["updated"] += 1
                        break
                    except (httpx.HTTPError, ValueError) as e:
                        conn.rollback()
                        retryable = (not isinstance(e, httpx.HTTPStatusError) or
                                     e.response.status_code == 429 or e.response.status_code >= 500)
                        if retryable and attempt < 2:
                            delay = 5 * 2 ** attempt
                            if isinstance(e, httpx.HTTPStatusError):
                                try:
                                    delay = max(delay, float(e.response.headers.get("Retry-After", 0)))
                                except ValueError:
                                    pass
                            next_request = time.monotonic() + max(delay, RATE_LIMIT_DELAY)
                            continue
                        stats["errors"] += 1
                        print(f"  Detail fetch failed for {row['sopr_filing_id']}: {e}")
                        if retryable:
                            print(f"Detail backfill: {stats} (API unavailable; resume next run)")
                            return stats
                        break
    print(f"Detail backfill: {stats}")
    return stats


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
    """Daily sweep: everything POSTED to the LDA system in the last 7 days,
    against ANY report period since 2020, all report types.

    The API filters server-side by posted date (filing_dt_posted_after), so
    this catches the deadline surge, stragglers, and amendments to years-old
    periods in one pass — off-peak it's a couple of pages; in deadline week
    it's the same volume the old per-quarter re-walks fetched, without
    re-paginating whole quarters to find them. The 7-day window overlaps
    daily runs generously (UUID dedupe makes overlap free), so a few missed
    days of CI cannot drop filings, and the cutoff extends automatically
    when the newest stored filing shows a deeper gap (see below). A MONTHLY
    drift audit (audit_sample) measures whether stored records ever diverge
    from the live API — the cases a posted-date filter can't see (in-place
    edits, deletions). No such divergence has been observed; scheduled
    re-walks are deliberately absent unless the audit starts reporting drift.
    """
    init_db()
    # Outage-resilient cutoff: normally 7 days, but if the newest stored
    # filing is older than that (CI was down, or the DB was restored from an
    # older snapshot), extend the window back to just before it so the gap is
    # re-covered automatically. Capped at 120 days — a gap deeper than that
    # means real disaster recovery: run full-sweep / a historical backfill.
    with get_db() as conn:
        row = conn.execute(
            'SELECT MAX(substr(filing_date,1,10)) FROM filings WHERE filing_date IS NOT NULL'
        ).fetchone()
    max_posted = row[0] if row else None
    cutoff_dt = datetime.now() - timedelta(days=7)
    if max_posted:
        stale_dt = datetime.strptime(max_posted, '%Y-%m-%d') - timedelta(days=3)
        cutoff_dt = min(cutoff_dt, stale_dt)
    floor_dt = datetime.now() - timedelta(days=120)
    if cutoff_dt < floor_dt:
        print(f"WARNING: computed sweep window start {cutoff_dt:%Y-%m-%d} capped at "
              f"{floor_dt:%Y-%m-%d}; the DB looks >120 days stale — run "
              f"'01_ingest.py full-sweep' or a historical backfill to recover fully.")
        cutoff_dt = floor_dt
    ingest_posted_after(cutoff_dt.strftime('%Y-%m-%d'), start_year=2020)


def audit_sample(n: int = 100, max_minutes: float = None) -> dict:
    """Drift audit: re-fetch a random sample of stored current filings by
    UUID and compare against the live API.

    Detects the failure modes incremental posted-date ingestion cannot see:
    a filing edited in place, or expunged from the Senate system entirely
    (which is how the occasional junk filing actually disappears). Drift is
    so far entirely hypothetical, so this runs as a broad MONTHLY check
    sized to the CI window (max_minutes caps wall-clock; ~30K filings fit in
    ~4.5h at the keyed rate limit, ~6-7% of the corpus per month). Results
    accumulate in the audit_log table (persisted via the release DB) and are
    surfaced by compute_data_checks. If drift is ever actually observed,
    redesign the approach around the observed behavior.
    """
    init_db()
    deadline = (time.monotonic() + max_minutes * 60) if max_minutes else None
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                sampled INTEGER NOT NULL,
                missing INTEGER NOT NULL,
                income_mismatch INTEGER NOT NULL,
                activity_mismatch INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                details TEXT
            )
        """)
        cols = {r[1] for r in conn.execute('PRAGMA table_info(audit_log)')}
        if 'activity_mismatch' not in cols:
            conn.execute('ALTER TABLE audit_log ADD COLUMN activity_mismatch INTEGER NOT NULL DEFAULT 0')
        if 'errors' not in cols:
            conn.execute('ALTER TABLE audit_log ADD COLUMN errors INTEGER NOT NULL DEFAULT 0')
        conn.commit()
        rows = conn.execute("""
            SELECT f.sopr_filing_id, f.income,
                   (SELECT COUNT(*) FROM activities a WHERE a.filing_id = f.id) AS n_activities
            FROM filings f
            WHERE f.is_current = 1 AND f.sopr_filing_id IS NOT NULL
            ORDER BY RANDOM() LIMIT ?
        """, (n,)).fetchall()

    missing, mismatched, act_mismatched, errors, checked, details = 0, 0, 0, 0, 0, []
    for uuid, stored_income, stored_n_activities in rows:
        if deadline and time.monotonic() > deadline:
            print(f"  Time budget reached after {checked} of {len(rows)} sampled filings.")
            break
        try:
            resp = httpx.get(f"{API_BASE}/filings/{uuid}/", headers=get_headers(),
                             timeout=60, follow_redirects=True)
            if resp.status_code == 429:
                time.sleep(30)
                resp = httpx.get(f"{API_BASE}/filings/{uuid}/", headers=get_headers(),
                                 timeout=60, follow_redirects=True)
            if resp.status_code == 404:
                checked += 1
                missing += 1
                details.append({'uuid': uuid, 'problem': 'missing_from_api'})
                continue
            resp.raise_for_status()
            live = resp.json()
        except Exception as e:
            # Transient API trouble is not drift — and not a successful check
            # either. Counted separately so "N sampled, 0 mismatches" can't
            # mean "N requests failed".
            errors += 1
            details.append({'uuid': uuid, 'problem': f'fetch_error: {e}'})
            time.sleep(RATE_LIMIT_DELAY)
            continue

        parsed = parse_api_filing(live)
        if parsed:
            load_filings_to_db([parsed])
        checked += 1
        live_income = live.get('income') or live.get('expenses') or 0
        if isinstance(live_income, str):
            live_income = float(live_income.replace(',', '').replace('$', '')) if live_income else 0
        if abs((stored_income or 0) - (live_income or 0)) > 0.01:
            mismatched += 1
            details.append({'uuid': uuid, 'problem': 'income_mismatch',
                            'stored': stored_income, 'live': live_income})
        # Activity-count comparison mirrors parse_api_filing, which only
        # stores activities with a non-empty description.
        live_n_activities = sum(
            1 for a in (live.get('lobbying_activities') or [])
            if (a.get('description') or a.get('specific_issues') or '')
        )
        if live_n_activities != (stored_n_activities or 0):
            act_mismatched += 1
            details.append({'uuid': uuid, 'problem': 'activity_count_mismatch',
                            'stored': stored_n_activities, 'live': live_n_activities})
        time.sleep(RATE_LIMIT_DELAY)

    import json as _json
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, sampled, missing, income_mismatch, activity_mismatch, errors, details) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(), checked, missing, mismatched, act_mismatched, errors,
             _json.dumps(details[:50])),
        )
        conn.commit()  # get_db() closes without committing; without this the row rolls back

    print(f"Drift audit: {checked} checked, {missing} missing from API, "
          f"{mismatched} income mismatches, {act_mismatched} activity-count mismatches, "
          f"{errors} fetch errors.")
    if details:
        for d in details[:10]:
            print(f"  {d}")
    return {'sampled': checked, 'missing': missing, 'income_mismatch': mismatched,
            'activity_mismatch': act_mismatched, 'errors': errors}


def coverage_check(max_backfills: int = 2, tolerance: int = 25) -> int:
    """Compare per-period filing counts in the DB against the live API and
    re-ingest the worst-shortfall periods.

    The posted-after sweeps only catch what the API reports as newly
    posted; a period can sit silently short from an old ingest gap (2020 Q1
    was ~4K filings short for months before anyone noticed it in a chart).
    One count query per (year, filing_type) is cheap; a shortfall beyond
    `tolerance` triggers a full re-walk of that period, which is idempotent
    (sopr_filing_id dedupe). Surpluses (DB > API — expunged filings) are
    only reported: deleting is the drift audit's judgment call, never
    automatic. Backfills are capped per run so one pass can't blow the CI
    window; remaining short periods surface again next week.
    """
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT year, quarter, COUNT(*) FROM filings
               WHERE year IS NOT NULL AND quarter BETWEEN 1 AND 4
               GROUP BY year, quarter ORDER BY year, quarter'''
        ).fetchall()

    shortfalls = []
    print(f"Coverage check across {len(rows)} report periods:")
    for year, quarter, db_count in rows:
        api_count = 0
        failed = False
        for filing_type in _report_types_for_quarter(quarter):
            try:
                data = fetch_filings_page(year, filing_type, page=1)
            except Exception as e:
                print(f"  {year} Q{quarter}: count fetch failed for {filing_type} ({e}); skipping period")
                failed = True
                break
            api_count += int(data.get('count') or 0)
            time.sleep(0.6)  # ~100 req/min, under the keyed 120/min limit
        if failed:
            continue
        diff = api_count - db_count
        flag = ''
        if diff > tolerance:
            flag = '  <-- SHORT'
        elif diff < -tolerance:
            flag = '  (surplus — possible expunges; see monthly drift audit)'
        print(f"  {year} Q{quarter}: db={db_count} api={api_count} diff={diff:+d}{flag}")
        if diff > tolerance:
            shortfalls.append((diff, year, quarter))

    shortfalls.sort(reverse=True)
    for diff, year, quarter in shortfalls[:max_backfills]:
        print(f"\nBackfilling {year} Q{quarter} (short by {diff})...")
        ingest_quarter(year, quarter)
    deferred = len(shortfalls) - max_backfills
    if deferred > 0:
        print(f"\nNOTE: {deferred} more short period(s) deferred to the next weekly run.")
    return len(shortfalls)


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

    if len(sys.argv) >= 2 and sys.argv[1] == "backfill-details":
        import argparse

        parser = argparse.ArgumentParser(description="Resume detail backfill for dashboard mover filings")
        parser.add_argument("--max-requests", type=int, default=600)
        parser.add_argument("--max-minutes", type=float, default=8)
        parser.add_argument("--clients-json", type=Path)
        parser.add_argument("--uuid", action="append", help="Specific filing UUID; repeat for a set")
        args = parser.parse_args(sys.argv[2:])
        if args.max_requests < 0 or args.max_minutes <= 0:
            parser.error("max-requests must be nonnegative and max-minutes must be positive")
        backfill_details(args.uuid, args.max_requests, args.max_minutes, args.clients_json)
    elif len(sys.argv) >= 2 and sys.argv[1] == "recompute-current":
        init_db()
        with get_db() as conn:
            recompute_is_current(conn)
        print("Recomputed is_current for the entire filings table.")
    elif len(sys.argv) >= 2 and sys.argv[1] == "audit-sample":
        n = 100
        max_minutes = None
        if "--n" in sys.argv:
            n = int(sys.argv[sys.argv.index("--n") + 1])
        if "--max-minutes" in sys.argv:
            max_minutes = float(sys.argv[sys.argv.index("--max-minutes") + 1])
        audit_sample(n, max_minutes)
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
    elif len(sys.argv) >= 2 and sys.argv[1] == "coverage-check":
        coverage_check()
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
        print("       python 01_ingest.py latest                        # Daily sweep: everything posted in the last 7 days")
        print("       python 01_ingest.py backfill-details [--max-requests 600] [--uuid UUID]")
        print("       python 01_ingest.py recompute-current             # Recompute is_current for the whole table")
        print("       python 01_ingest.py backfill-non-original --start-year 2020")
        print("                                                          # Historical backfill of amendments/terminations")
        print("       python 01_ingest.py full-sweep --start-year 2020  # Posted-after sweep over ~13 months")
        print("       python 01_ingest.py audit-sample --n 100 [--max-minutes M]  # Drift audit vs live API")
        sys.exit(1)
