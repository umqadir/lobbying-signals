"""Orchestrate the full data refresh cycle.

This script is called by GitHub Actions to:
1. Ingest new filings from LDA API
2. Extract rule-based classifications for new activities
3. Compute trends and generate alerts
4. Export JSON for dashboard
"""

import os
import sys
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
    verbose: bool = True
):
    """Run the full refresh cycle."""

    def log(msg):
        if verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    # Ensure database exists
    init_db()

    # 1. Ingest new filings
    if ingest_latest:
        log("Step 1: Ingesting new filings...")
        ingest_module = _load_module("01_ingest.py", "ingest")
        ingest_module.ingest_latest()
        log("  Ingestion complete")

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

    # 3. Export JSON for dashboard
    if export:
        log("Step 4: Exporting JSON for dashboard...")
        try:
            trends_module = _load_module("08_trends.py", "trends")
            trends_module.export_json()
        except Exception as e:
            log(f"  Warning: Export failed: {e}")
            raise

    log("Refresh complete!")


def check_env():
    """Check required environment variables."""
    if not os.getenv('LDA_API_KEY'):
        print("Warning: LDA_API_KEY not set (ingestion will be slower)")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Refresh lobbying data')
    parser.add_argument('--no-ingest', action='store_true', help='Skip ingestion')
    parser.add_argument('--rules-batch-size', type=int, default=2_000_000, help='Max activities to classify with deterministic rules')
    parser.add_argument('--no-export', action='store_true', help='Skip JSON export')
    parser.add_argument('--check-env', action='store_true', help='Check environment and exit')

    args = parser.parse_args()

    if args.check_env:
        sys.exit(0 if check_env() else 1)

    check_env()

    refresh(
        ingest_latest=not args.no_ingest,
        rules_batch_size=args.rules_batch_size,
        export=not args.no_export
    )
