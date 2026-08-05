"""Surface client-identity candidates that deterministic rules can't judge.

clients_norm.canonical_client_key folds mechanical variants (suffixes,
boilerplate, self-acronyms), but three classes need judgment and are
maintained by curation:

1. Corporate renames -> _FORMER_NAME_ALIASES. Evidence comes from the data
   itself: raw names carrying "(FORMERLY X)" / "FKA X" markers whose old
   name still holds unaliased income under a separate key.
2. Suspected duplicate keys among top spenders (one key a word-prefix of
   another) that the mechanical rules deliberately leave split — same
   lobbying organization vs genuinely distinct sister entities is a
   research question, not a string rule.
3. Single-quarter income outliers, candidates for the verified-erroneous
   EXCLUDED_CLIENT_KEYS list (the Loc Nation precedent).

Deterministic and read-only. Writes a Markdown report (default stdout) for
the monthly identity review to research; nothing here edits the pipeline.

    python scripts/audit_client_identities.py [--db data/filings.db] [--out report.md] [--min-income 1000000] [--top 40]
"""

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clients_norm import canonical_client_key, EXCLUDED_CLIENT_KEYS  # noqa: E402

# Rename evidence in raw names. D/B/A is deliberately absent — "doing
# business as" is not a rename.
_PAREN_RENAME_RE = re.compile(
    r'\(\s*(?:FORMERLY(?:\s+(?:KNOWN|REPORTED)\s+AS)?|FKA|F/K/A)\s*[:,]?\s*([^)]+)\)',
    re.IGNORECASE,
)
_BARE_RENAME_RE = re.compile(
    r'\b(?:FKA|F/K/A|FORMERLY(?:\s+(?:KNOWN|REPORTED)\s+AS)?)\s+(.+)$',
    re.IGNORECASE,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(ROOT / 'data' / 'filings.db'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--min-income', type=float, default=1_000_000)
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--outlier-quarter-income', type=float, default=25_000_000)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)

    income_by_key = defaultdict(float)
    names_by_key = defaultdict(set)
    for name, inc in conn.execute(
        '''SELECT c.name, SUM(f.income) FROM filings f
           JOIN clients c ON f.client_id = c.id
           WHERE f.is_current = 1 GROUP BY c.name'''
    ):
        key = canonical_client_key(name)
        if not key:
            continue
        income_by_key[key] += inc or 0
        names_by_key[key].add(name)

    lines = ['# Client identity audit', '']

    # ── 1. Rename evidence ──
    rename_candidates = {}
    for key, raw_names in names_by_key.items():
        for raw in raw_names:
            m = _PAREN_RENAME_RE.search(raw) or _BARE_RENAME_RE.search(raw)
            if not m:
                continue
            old_key = canonical_client_key(m.group(1))
            # An already-aliased old name canonicalizes to the current key,
            # so existing _FORMER_NAME_ALIASES entries self-filter here.
            if not old_key or old_key == key:
                continue
            if income_by_key.get(old_key, 0) < args.min_income:
                continue
            rename_candidates.setdefault((old_key, key), raw)

    lines.append('## Rename candidates (old key still holds unaliased income)')
    lines.append('')
    if rename_candidates:
        lines.append('| Old key | Old income | Current key | Evidence (raw filing name) |')
        lines.append('|---|---|---|---|')
        ranked = sorted(rename_candidates.items(),
                        key=lambda kv: income_by_key.get(kv[0][0], 0), reverse=True)
        for (old_key, new_key), raw in ranked[:args.top]:
            lines.append(f'| `{old_key}` | ${income_by_key[old_key]/1e6:.1f}M '
                         f'| `{new_key}` | {raw} |')
    else:
        lines.append('None found.')
    lines.append('')

    # ── 2. Suspected duplicate keys (word-prefix pairs among top spenders) ──
    big = sorted(k for k, v in income_by_key.items() if v >= args.min_income)
    pairs = []
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            if not b.startswith(a + ' '):
                if not b.startswith(a):
                    break
                continue
            if len(b[len(a):].split()) > 3:
                continue
            pairs.append((income_by_key[a] + income_by_key[b], a, b))
    pairs.sort(reverse=True)

    lines.append('## Suspected duplicate keys (prefix pairs — research each; many are distinct orgs)')
    lines.append('')
    if pairs:
        lines.append('| Key A | Income A | Key B | Income B |')
        lines.append('|---|---|---|---|')
        for _, a, b in pairs[:args.top]:
            lines.append(f'| `{a}` | ${income_by_key[a]/1e6:.1f}M '
                         f'| `{b}` | ${income_by_key[b]/1e6:.1f}M |')
    else:
        lines.append('None found.')
    lines.append('')

    # ── 3. Single-quarter income outliers (EXCLUDED_CLIENT_KEYS candidates) ──
    outliers = []
    quarter_income = defaultdict(float)
    for name, year, quarter, inc in conn.execute(
        '''SELECT c.name, f.year, f.quarter, SUM(f.income) FROM filings f
           JOIN clients c ON f.client_id = c.id
           WHERE f.is_current = 1 GROUP BY c.name, f.year, f.quarter'''
    ):
        key = canonical_client_key(name)
        if key:
            quarter_income[(key, year, quarter)] += inc or 0
    seen_keys = set()
    for (key, year, quarter), inc in sorted(quarter_income.items(), key=lambda kv: -kv[1]):
        if inc < args.outlier_quarter_income or key in seen_keys:
            continue
        seen_keys.add(key)
        excluded = ' (already excluded)' if key in EXCLUDED_CLIENT_KEYS else ''
        outliers.append(f'- `{key}`: ${inc/1e6:.1f}M in {year} Q{quarter}{excluded}')

    lines.append(f'## Single-quarter totals ≥ ${args.outlier_quarter_income/1e6:.0f}M '
                 '(exclusion candidates — most are legitimate big spenders)')
    lines.append('')
    lines.extend(outliers[:args.top] or ['None found.'])
    lines.append('')

    report = '\n'.join(lines)
    if args.out:
        Path(args.out).write_text(report)
        print(f'Wrote {args.out} ({len(rename_candidates)} renames, '
              f'{len(pairs)} pairs, {len(outliers)} outliers)')
    else:
        print(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
