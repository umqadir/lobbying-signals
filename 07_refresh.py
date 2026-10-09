"""Orchestrate the full data refresh cycle.

This script is called by GitHub Actions to:
1. Ingest new filings from LDA API
2. Extract rule-based classifications for new activities
3. Compute trends and generate alerts
4. Export JSON for dashboard
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

from db import init_db


def _load_module(path: str, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refresh(
    ingest_latest: bool = True,
    rules_batch_size: int = 2_000_000,
    export: bool = True,
    verbose: bool = True,
    backfill_details: bool = True,
    failure_marker: Path = None
):
    """Run the full refresh cycle."""

    def log(msg):
        if verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    # Ensure database exists
    init_db()
    if failure_marker is not None:
        failure_marker.unlink(missing_ok=True)
    ingest_complete = True

    # 1. Ingest new filings
    if ingest_latest:
        log("Step 1: Ingesting new filings...")
        ingest_module = _load_module("01_ingest.py", "ingest")
        ingest_result = ingest_module.ingest_latest()
        ingest_complete = ingest_result["complete"]
        log("  Ingestion complete" if ingest_complete else
            f"  WARNING: Ingestion incomplete for years {ingest_result['failed_years']}; publishing partial data")

    # 2. Extract deterministic rule-based classifications for new activities
    if rules_batch_size > 0:
        log(f"Step 2: Extracting deterministic classifications (batch={rules_batch_size})...")
        rules_module = _load_module("12_extract_rules.py", "rules_extract")
        conn = rules_module.connect()
        try:
            rules_module.init_tables(conn)
            rules = rules_module.load_rules(rules_module.RULES_PATH)
            extracted = rules_module.process_batch(
                conn=conn,
                rules=rules,
                batch_size=rules_batch_size,
                min_description_len=20,
                refresh_existing=False,
                issue_codes=None,
            )
        finally:
            conn.close()
        log(f"  Rule-extracted {extracted} activities")

    # 3. Reference data persists in the same release DB as the filings.
    log("Step 3: Refreshing bill introduction dates and official titles...")
    try:
        # Shared 100s download/parse budget plus a 110s process watchdog:
        # even a hung source/DNS/XML parser cannot add two minutes to CI.
        # Each successfully fetched group is already committed if it times out.
        subprocess.run(
            [sys.executable, '-u', str(Path(__file__).resolve().parent /
                                      'scripts/build_bill_reference.py'),
             '--max-seconds', '100'],
            check=True, timeout=110,
        )
    except Exception as e:
        log(f"  Warning: Bill reference refresh failed; using cached data: {e}")
    # Also reload after a watchdog timeout: completed groups may have committed.
    from bill_reference import load_reference
    load_reference.cache_clear()

    # 4. Export JSON for dashboard
    if export:
        log("Step 4: Exporting JSON for dashboard...")
        try:
            trends_module = _load_module("08_trends.py", "trends")
            trends_module.export_json()
            if backfill_details:
                log("Step 5: Backfilling dashboard filing details (600 requests / 8 minutes max)...")
                try:
                    ingest_module = _load_module("01_ingest.py", "ingest_details")
                    result = ingest_module.backfill_details(max_requests=600, max_minutes=8)
                    if result["updated"]:
                        trends_module.export_clients_json()
                except Exception as e:
                    log(f"  Warning: Detail backfill skipped: {e}")
        except Exception as e:
            log(f"  Warning: Export failed: {e}")
            raise

    if not ingest_complete and failure_marker is not None:
        failure_marker.write_text(f"Posted-after sweep incomplete for years {ingest_result['failed_years']}\n")
    log("Refresh complete!" if ingest_complete else "Refresh finished with incomplete ingestion.")
    return ingest_complete


def check_env():
    """Check required environment variables."""
    if not os.getenv('LDA_API_KEY'):
        print("Warning: LDA_API_KEY not set (ingestion will be slower)")
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Refresh lobbying data')
    parser.add_argument('--no-ingest', action='store_true', help='Skip ingestion')
    parser.add_argument('--rules-batch-size', type=int, default=2_000_000, help='Max activities to classify with deterministic rules')
    parser.add_argument('--no-detail-backfill', action='store_true', help='Skip optional filing-detail backfill')
    parser.add_argument('--no-export', action='store_true', help='Skip JSON export')
    parser.add_argument('--check-env', action='store_true', help='Check environment and exit')
    parser.add_argument('--failure-marker', type=Path,
                        help='Defer incomplete-ingest failure to CI by writing this file')

    args = parser.parse_args()

    if args.check_env:
        return 0 if check_env() else 1

    check_env()

    complete = refresh(
        ingest_latest=not args.no_ingest,
        rules_batch_size=args.rules_batch_size,
        export=not args.no_export,
        backfill_details=not args.no_detail_backfill,
        failure_marker=args.failure_marker
    )
    return 0 if complete or args.failure_marker is not None else 1


if __name__ == "__main__":
    sys.exit(main())
