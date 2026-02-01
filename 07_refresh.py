"""Orchestrate the full data refresh cycle.

This script is called by GitHub Actions to:
1. Ingest new filings from LDA API
2. Extract classifications for new activities
3. Update normalization dictionary (periodically)
4. Compute trends and generate alerts
5. Export JSON for dashboard
"""

import os
import sys
from datetime import datetime

from db import init_db


def refresh(
    ingest_latest: bool = True,
    extract_limit: int = 500,
    normalize: bool = False,
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
        try:
            from import_01_ingest import ingest_latest as do_ingest
            do_ingest()
        except ImportError:
            # Try direct import
            import importlib.util
            spec = importlib.util.spec_from_file_location("ingest", "01_ingest.py")
            ingest_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ingest_module)
            ingest_module.ingest_latest()
        log("  Ingestion complete")

    # 2. Extract classifications for new activities
    if extract_limit > 0:
        log(f"Step 2: Extracting classifications (limit={extract_limit})...")
        try:
            from import_06_extract import extract_batch, init_extraction_tables
            init_extraction_tables()
            extracted = extract_batch(extract_limit)
            log(f"  Extracted {extracted} activities")
        except ImportError:
            import importlib.util
            spec = importlib.util.spec_from_file_location("extract", "06_extract.py")
            extract_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(extract_module)
            extract_module.init_extraction_tables()
            extracted = extract_module.extract_batch(extract_limit)
            log(f"  Extracted {extracted} activities")

    # 3. Build normalization dictionary (weekly or on request)
    if normalize:
        log("Step 3: Building normalization dictionary...")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("extract", "06_extract.py")
            extract_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(extract_module)
            for field in ['topics', 'entities', 'domain']:
                extract_module.build_normalization_batch(field)
        except Exception as e:
            log(f"  Warning: Normalization failed: {e}")

    # 4. Export JSON for dashboard
    if export:
        log("Step 4: Exporting JSON for dashboard...")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("trends", "08_trends.py")
            trends_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(trends_module)
            trends_module.export_json()
        except Exception as e:
            log(f"  Warning: Export failed: {e}")
            raise

    log("Refresh complete!")


def check_env():
    """Check required environment variables."""
    issues = []

    if not os.getenv('GEMINI_API_KEY') and not os.getenv('GOOGLE_API_KEY'):
        issues.append("GEMINI_API_KEY or GOOGLE_API_KEY not set (needed for extraction)")

    if not os.getenv('LDA_API_KEY'):
        print("Warning: LDA_API_KEY not set (ingestion will be slower)")

    if issues:
        print("Environment issues:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Refresh lobbying data')
    parser.add_argument('--no-ingest', action='store_true', help='Skip ingestion')
    parser.add_argument('--extract-limit', type=int, default=500, help='Max activities to extract')
    parser.add_argument('--normalize', action='store_true', help='Rebuild normalization dictionary')
    parser.add_argument('--no-export', action='store_true', help='Skip JSON export')
    parser.add_argument('--check-env', action='store_true', help='Check environment and exit')

    args = parser.parse_args()

    if args.check_env:
        sys.exit(0 if check_env() else 1)

    check_env()

    refresh(
        ingest_latest=not args.no_ingest,
        extract_limit=args.extract_limit,
        normalize=args.normalize,
        export=not args.no_export
    )
