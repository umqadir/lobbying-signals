"""Build/refresh GovInfo bill reference tables in the local production DB."""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bill_reference import refresh_reference
from config import DB_PATH

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--max-seconds', type=float, default=100)
    parser.add_argument('--coverage', action='store_true', help='Print per-Congress/type coverage')
    args = parser.parse_args()
    with sqlite3.connect(args.db) as conn:
        refresh_reference(conn, max_seconds=args.max_seconds)
        if args.coverage:
            for row in conn.execute('SELECT congress, type, count(*), min(introduced_date), max(introduced_date) FROM bill_reference GROUP BY congress, type'):
                print(row)
