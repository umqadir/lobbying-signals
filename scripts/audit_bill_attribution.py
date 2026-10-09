"""Report current-filing tag changes and unresolved occurrences by posted month.

Before editing, save the prior 08_trends.py, then run:
  python scripts/audit_bill_attribution.py --before-module /tmp/before_trends.py
Bill counts deduplicate normalized identities per activity (not distinct organizations); only current
filings are included, matching exports. This script never modifies the DB.
"""
import argparse
import importlib.util
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bill_reference import read_reference, BILL_TYPES
from config import DB_PATH


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--before-module', required=True)
    args = parser.parse_args()
    before = module(args.before_module, 'before_trends')
    after = module(ROOT / '08_trends.py', 'after_trends')
    conn = sqlite3.connect(args.db)
    ref = read_reference(conn)
    old_counts, new_counts, unresolved, months = Counter(), Counter(), Counter(), Counter()
    rows = conn.execute('''
        SELECT e.legislation, f.year, f.filing_date
        FROM activity_extractions_rules e
        JOIN activities a ON e.activity_id = a.id
        JOIN filings f ON a.filing_id = f.id
        WHERE f.is_current = 1 AND e.legislation IS NOT NULL AND e.legislation != '[]'
    ''')
    scanned = 0
    for legislation, year, posted in rows:
        scanned += 1
        old_seen, new_seen = set(), set()
        for tag in json.loads(legislation):
            old = before.normalize_legislation(tag, year)
            new = after.normalize_legislation(tag, year, posted, reference=ref, unresolved=unresolved)
            if old:
                old_seen.add(old)
            if new:
                new_seen.add(new)
            months[str(posted or '')[:7] or 'unknown'] += 1
        old_counts.update(old_seen)
        new_counts.update(new_seen)
    print(f'Scanned {scanned:,} tagged activities on current filings; bill counts below are mentions deduplicated per activity across all report years.\n')
    print('| Congress | H.R. | S. | H.Res. | S.Res. | H.J.Res. | S.J.Res. | H.Con.Res. | S.Con.Res. | Total |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
    for congress in sorted({c for c, _, _ in ref}):
        counts = [sum(1 for c, t, _ in ref if c == congress and t == kind) for kind in BILL_TYPES]
        print('| ' + ' | '.join(map(str, [congress, *counts, sum(counts)])) + ' |')
    changed = [n for n in old_counts.keys() | new_counts.keys() if old_counts[n] != new_counts[n]]
    changed.sort(key=lambda n: (-abs(new_counts[n] - old_counts[n]), n))
    print('\nLargest 20 tag-count changes:\n')
    print('| Bill tag | Before | After | Change |')
    print('|---|---:|---:|---:|')
    for name in changed[:20]:
        print(f'| {name} | {old_counts[name]:,} | {new_counts[name]:,} | {new_counts[name] - old_counts[name]:+,} |')
    print(f'\nUnresolved: {sum(unresolved.values()):,} occurrences; missing/invalid dates appear as unknown. Explicit qualifiers and recognized names are excluded.\n')
    print('| Posted month | Unresolved |')
    print('|---|---:|')
    for month in sorted(months.keys() | unresolved.keys()):
        print(f'| {month} | {unresolved[month]:,} |')


if __name__ == '__main__':
    main()
