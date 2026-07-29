"""Re-ingest missing quarters with better error handling."""

import importlib.util
import sys
import time
from pathlib import Path
from datetime import datetime

# Import 01_ingest.py
spec = importlib.util.spec_from_file_location("ingest", Path(__file__).parent / "01_ingest.py")
ingest = importlib.util.module_from_spec(spec)
sys.modules["ingest"] = ingest
spec.loader.exec_module(ingest)

from db import get_db

# Increase timeout to 180 seconds
ingest.httpx.timeout = 180


def get_quarter_count(year: int, quarter: int) -> int:
    """Get current filing count for a quarter."""
    with get_db() as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE year=? AND quarter=?",
            (year, quarter)
        ).fetchone()
        return result[0] if result else 0


def get_expected_count(year: int, quarter: int) -> int:
    """Get expected filing count from API."""
    try:
        import httpx
        params = {
            "filing_year": year,
            "filing_type": f"Q{quarter}",
            "page_size": 1
        }
        resp = httpx.get(
            f"{ingest.API_BASE}/filings/",
            params=params,
            headers=ingest.get_headers(),
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json().get("count", 0)
    except Exception as e:
        print(f"  Error checking count: {e}")
    return 0


def ingest_quarter_robust(year: int, quarter: int, max_retries: int = 3):
    """Ingest a quarter with retry logic."""
    print(f"\n{'='*60}")
    print(f"Ingesting {year} Q{quarter}...")
    print('='*60)

    current_count = get_quarter_count(year, quarter)
    expected_count = get_expected_count(year, quarter)

    print(f"Current: {current_count:,} | Expected: {expected_count:,}")

    if current_count >= expected_count * 0.95:  # 95% complete
        print("  Already complete, skipping")
        return current_count

    for attempt in range(max_retries):
        try:
            loaded = ingest.ingest_quarter(year, quarter)
            final_count = get_quarter_count(year, quarter)
            print(f"  Completed: {final_count:,} filings")
            return final_count
        except Exception as e:
            print(f"  Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 10  # 10, 20, 40 seconds
                print(f"  Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                print(f"  Failed after {max_retries} attempts")
                return get_quarter_count(year, quarter)


def main():
    """Re-ingest missing quarters."""
    quarters_to_check = [
        # 2020
        (2020, 2), (2020, 3), (2020, 4),
        # 2021
        (2021, 1), (2021, 2), (2021, 4),
        # 2022
        (2022, 1), (2022, 2), (2022, 3), (2022, 4),
        # 2023
        (2023, 2), (2023, 3), (2023, 4),
        # 2024
        (2024, 2), (2024, 3), (2024, 4),
    ]

    print(f"Starting re-ingestion at {datetime.now()}")
    print(f"Checking {len(quarters_to_check)} quarters")

    total_before = 0
    total_after = 0

    with get_db() as conn:
        total_before = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]

    for year, quarter in quarters_to_check:
        ingest_quarter_robust(year, quarter)

    with get_db() as conn:
        total_after = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]

    print(f"\n{'='*60}")
    print(f"Re-ingestion complete at {datetime.now()}")
    print(f"Total filings: {total_before:,} → {total_after:,} (+{total_after - total_before:,})")
    print('='*60)


if __name__ == "__main__":
    main()
