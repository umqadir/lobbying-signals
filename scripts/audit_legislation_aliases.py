"""Surface legislation tags that the normalizer leaves as opaque identities.

The alias table in 08_trends.py folds landmark laws' name / bill-number /
public-law variants into one identity, but it is hand-maintained, so a bill
cited mostly by number that we haven't mapped shows up as a bare
"H.R. N (Cth Congress)" / "P.L. X-Y" identity. This script scans every
legislation tag in the database, applies the live normalizer, and reports the
highest-volume identities that look like a specific law but aren't mapped —
the candidates a reviewer should research and, where the mapping is
well-established, add to LEGISLATION_ALIASES / KNOWN_ACT_PATTERNS.

It also reports the largest tags currently DROPPED as noise, so we can catch a
real law being discarded by an over-broad fragment rule.

Deterministic and read-only. Writes a Markdown report (default stdout).

    python scripts/audit_legislation_aliases.py [--db data/filings.db] [--out report.md] [--min-mentions 1500] [--top 40]
"""

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("trends", ROOT / "08_trends.py")
trends = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trends)

# Identities the normalizer already resolves to a canonical named law.
CANONICAL = set(trends.LEGISLATION_ALIASES.values()) | {
    canonical for _, canonical in trends.KNOWN_ACT_PATTERNS
}

BARE_NUMBER = re.compile(r'^(H\.R\.|S\.) \d+ \(\d+\w+ Congress\)$')
PUBLIC_LAW = re.compile(r'^P\.L\. \d+-\d+$')


def looks_like_specific_law(identity: str) -> bool:
    """A bare bill/public-law number, or a titled act/law we haven't canonicalized."""
    if BARE_NUMBER.match(identity) or PUBLIC_LAW.match(identity):
        return True
    low = identity.lower()
    if identity in CANONICAL:
        return False
    # A multi-word title ending in Act/Law that isn't a generic recurring title.
    words = identity.split()
    if len(words) >= 3 and (low.endswith(' act') or low.endswith(' law')):
        generic = ('appropriations', 'authorization act', 'continuing resolution')
        if not any(g in low for g in generic):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(ROOT / 'data' / 'filings.db'))
    ap.add_argument('--out', default='-')
    ap.add_argument('--min-mentions', type=int, default=1500)
    ap.add_argument('--top', type=int, default=40)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    rows = conn.execute('''
        SELECT e.legislation, f.year
        FROM activity_extractions_rules e
        JOIN activities a ON e.activity_id = a.id
        JOIN filings f ON a.filing_id = f.id
        WHERE e.legislation IS NOT NULL AND e.legislation != '[]'
    ''').fetchall()

    identity_counts = Counter()
    dropped_counts = Counter()          # raw fragment -> count (when it drops to '')
    raw_for_identity = {}               # identity -> Counter of raw variants
    for legjson, year in rows:
        try:
            tags = json.loads(legjson)
        except (TypeError, ValueError):
            continue
        for raw in tags:
            ident = trends.normalize_legislation(raw, year)
            if not ident:
                dropped_counts[trends.normalize_tag(raw).lower().strip(' .,;:')] += 1
                continue
            identity_counts[ident] += 1
            raw_for_identity.setdefault(ident, Counter())[raw] += 1

    candidates = [
        (ident, cnt) for ident, cnt in identity_counts.most_common()
        if cnt >= args.min_mentions and looks_like_specific_law(ident)
    ][:args.top]

    lines = []
    w = lines.append
    w("# Legislation alias audit\n")
    w(f"Scanned {len(rows):,} activities. {len(identity_counts):,} distinct "
      f"normalized identities; {len(CANONICAL)} canonical named laws mapped.\n")
    w(f"## Unmapped high-volume identities (≥ {args.min_mentions:,} mentions)\n")
    w("Each is a bill cited mostly by number, or a titled act we don't canonicalize. "
      "Research whether it is a well-known law with name/number/public-law variants "
      "that should fold together, and if confident add it to `LEGISLATION_ALIASES` "
      "or `KNOWN_ACT_PATTERNS` in `08_trends.py` (plus a case in "
      "`scripts/test_normalize_legislation.py`).\n")
    if not candidates:
        w("_None above threshold — the alias table covers the current high-volume tail._\n")
    else:
        w("| identity | mentions | top raw variants |")
        w("|---|---:|---|")
        for ident, cnt in candidates:
            variants = ", ".join(f"`{v}` ({n})" for v, n in raw_for_identity[ident].most_common(3))
            w(f"| **{ident}** | {cnt:,} | {variants} |")
    w("\n## Largest tags dropped as noise\n")
    w("Confirm none of these is a real law being discarded by an over-broad fragment rule.\n")
    w("| dropped fragment | mentions |")
    w("|---|---:|")
    for frag, cnt in dropped_counts.most_common(12):
        w(f"| `{frag}` | {cnt:,} |")
    report = "\n".join(lines) + "\n"

    if args.out == '-':
        sys.stdout.write(report)
    else:
        Path(args.out).write_text(report, encoding='utf-8')
        print(f"Wrote {args.out} ({len(candidates)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
