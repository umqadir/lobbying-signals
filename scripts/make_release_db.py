"""Produce a slimmed copy of filings.db for the GitHub Release data asset.

The release DB is what the daily GitHub Actions refresh downloads and builds
on, so it must contain everything the active pipeline reads or writes:
filings/registrants/clients/activities (01_ingest), activity_extractions_rules
and topic_candidate_terms (12_extract_rules), issues and signals (db schema).

Everything else is local analysis history — the legacy LLM extraction table
and the issue-dictionary experiment artifacts — which together account for
roughly half the file size. Those stay in the local working DB (and in
data/archives snapshots); they just don't ship to CI.

Usage:
    python scripts/make_release_db.py [--src data/filings.db] [--out data/filings.release.db]
"""

import argparse
import sqlite3
from pathlib import Path

DROP_TABLES = [
    "activity_extractions",          # legacy LLM extraction (superseded by rules)
    "issue_matches_loop",            # issue-dictionary experiment artifacts
    "issue_dictionary_loop",
    "issue_dictionary_v2",
    "activity_issue_map_v2",
    "issue_dictionary_runs_v2",
    "issue_dictionary_iterations_v2",
    "activity_topics",               # abandoned early prototype table
    "normalization_dict",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/filings.db")
    ap.add_argument("--out", default="data/filings.release.db")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if out.exists():
        out.unlink()

    print(f"Copying {src} -> {out} (sqlite backup)...")
    src_conn = sqlite3.connect(src)
    out_conn = sqlite3.connect(out)
    src_conn.backup(out_conn)
    src_conn.close()

    for t in DROP_TABLES:
        out_conn.execute(f"DROP TABLE IF EXISTS [{t}]")
        print(f"  dropped {t}")
    out_conn.commit()

    print("VACUUM...")
    out_conn.execute("VACUUM")
    out_conn.close()

    mb = out.stat().st_size / 1e6
    print(f"Done: {out} ({mb:.0f} MB)")


if __name__ == "__main__":
    main()
