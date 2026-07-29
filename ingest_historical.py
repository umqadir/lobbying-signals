"""Ingest all historical lobbying data from 2020-2024."""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

# Import 01_ingest.py (can't use normal import due to numeric prefix)
spec = importlib.util.spec_from_file_location("ingest", Path(__file__).parent / "01_ingest.py")
ingest = importlib.util.module_from_spec(spec)
sys.modules["ingest"] = ingest
spec.loader.exec_module(ingest)

from db import init_db

def main():
    init_db()

    # Years to backfill (we already have 2025 Q1-Q4 and partial 2024 Q1)
    years = [2020, 2021, 2022, 2023]

    # Also fill in remaining 2024 quarters
    quarters_2024 = [2, 3, 4]

    print(f"Starting historical backfill at {datetime.now()}")
    print("=" * 50)

    for year in years:
        print(f"\n{'='*50}")
        print(f"Ingesting {year}...")
        print("=" * 50)
        try:
            ingest.ingest_year(year)
        except Exception as e:
            print(f"Error ingesting {year}: {e}")
            continue

    # Fill in 2024 Q2-Q4
    print(f"\n{'='*50}")
    print("Filling in 2024 Q2-Q4...")
    print("=" * 50)
    for q in quarters_2024:
        try:
            ingest.ingest_quarter(2024, q)
        except Exception as e:
            print(f"Error ingesting 2024 Q{q}: {e}")
            continue

    print(f"\n{'='*50}")
    print(f"Historical backfill complete at {datetime.now()}")
    print("=" * 50)

if __name__ == "__main__":
    main()
