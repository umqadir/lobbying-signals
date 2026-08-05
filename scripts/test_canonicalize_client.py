"""Regression tests for client-name canonicalization (clients_norm.py).

Covers the edge-case classes mined from the real data:
  - legal-suffix variants of one org folding to the same key
  - "on behalf of" / "OBO" pass-throughs resolving to the represented org
  - former-name parentheticals (FORMERLY/FKA/F/K/A/D/B/A) stripped
  - leading "THE" and trailing "AND (ITS) AFFILIATES"/"AND SUBSIDIARIES" stripped
  - display-name casing: small words, acronym allowlist, dotted tokens

Run: python scripts/test_canonicalize_client.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients_norm import canonical_client_key, display_client_name

# (raw name, expected canonical key)
KEY_CASES = [
    # Anthropic — bare / PBC / punctuated PBC / on-behalf-of pass-through
    ("ANTHROPIC", "ANTHROPIC"),
    ("ANTHROPIC PBC", "ANTHROPIC"),
    ("ANTHROPIC, PBC", "ANTHROPIC"),
    ("AQUIA GROUP ON BEHALF OF ANTHROPIC, PBC", "ANTHROPIC"),

    # UnitedHealth — bare / suffix / punctuated suffix (GROUP is a legal
    # suffix now, so the corporate shell folds into the brand)
    ("UNITEDHEALTH GROUP INC", "UNITEDHEALTH"),
    ("UNITEDHEALTH GROUP", "UNITEDHEALTH"),
    ("UNITEDHEALTH GROUP, INC.", "UNITEDHEALTH"),

    # Corporate-shell and US-subsidiary tails fold into the brand
    ("THE CIGNA GROUP", "CIGNA"),
    ("AMAZON.COM SERVICES LLC", "AMAZONCOM"),
    ("T-MOBILE USA, INC.", "T-MOBILE"),
    ("ORACLE AMERICA, INC.", "ORACLE"),
    ("VISA U.S.A. INC.", "VISA"),
    ("FRESENIUS MEDICAL CARE NORTH AMERICA", "FRESENIUS MEDICAL CARE"),
    # ...but geographic tails guarded by OF/FOR stay part of the name
    ("BANK OF AMERICA CORPORATION", "BANK OF AMERICA"),
    ("PARTNERSHIP FOR AMERICA", "PARTNERSHIP FOR AMERICA"),

    # Self-referential acronym parentheticals fold; chapter-style ones don't
    ("PHARMACEUTICAL RESEARCH AND MANUFACTURERS OF AMERICA (PHRMA)",
     "PHARMACEUTICAL RESEARCH AND MANUFACTURERS OF AMERICA"),
    ("AMERICA'S HEALTH INSURANCE PLANS INC (AHIP)", "AMERICAS HEALTH INSURANCE PLANS"),
    ("BIOTECHNOLOGY INNOVATION ORGANIZATION (BIO)", "BIOTECHNOLOGY INNOVATION ORGANIZATION"),
    ("AARP (TEXAS)", "AARP (TEXAS)"),

    # Boilerplate parentheticals and bare former-name tails
    ("CVS HEALTH (AND SUBSIDIARIES)", "CVS HEALTH"),
    ("BAYER CORPORATION (CONSOLIDATED REPORT)", "BAYER"),
    ("AMERICAN PROPERTY CASUALTY INSURANCE ASSOCIATION FKA PROPERTY CASUALTY INSURERS",
     "AMERICAN PROPERTY CASUALTY INSURANCE ASSOCIATION"),
    ("META PLATFORMS INC FORMERLY REPORTED AS FACEBOOK", "META PLATFORMS"),

    # Filer typos and interaction bugs seen in the wild: no space after FKA,
    # an on-behalf-of chain inside a former-name parenthetical, and a
    # dangling conjunction left by suffix stripping
    ("RTX CORPORATION AND AFFILIATES (FKARAYTHEON TECHNOLOGIES CORPORATION)", "RTX"),
    ("LIBERTY MUTUAL (FORMERLY KNOWN AS AKIN GUMP ON BEHALF OF LIBERTY MUTUAL)", "LIBERTY MUTUAL"),
    ("MERCK & CO.", "MERCK"),

    # Meta — every raw variant seen in the data folds to one identity:
    # "and various subsidiaries", truncated former-name parenthetical
    # (no closing paren), and the pre-rename FACEBOOK filings via the
    # former-name alias map.
    ("META PLATFORMS, INC. AND VARIOUS SUBSIDIARIES", "META PLATFORMS"),
    ("META PLATFORMS, INC.", "META PLATFORMS"),
    ("META PLATFORMS INC (FORMERLY FACEBOOK INC AND VARIOUS SUBSIDIARIES", "META PLATFORMS"),
    ("META PLATFORMS INC (FKA FACEBOOK)", "META PLATFORMS"),
    ("FACEBOOK, INC.", "META PLATFORMS"),

    # RTX — Raytheon renames fold forward
    ("RAYTHEON TECHNOLOGIES CORPORATION", "RTX"),
    ("RTX CORPORATION", "RTX"),

    # Former-name parentheticals
    ("INTUIT INC. (FORMERLY INTUIT SOFTWARE CORP)", "INTUIT"),
    ("HERBERT J. THOMAS MEMORIAL HOSPITAL ASSOCIATION (FKA THOMAS HEALTH)",
     "HERBERT J THOMAS MEMORIAL HOSPITAL ASSOCIATION"),
    ("SOME HOSPITAL (F/K/A OLD NAME HOSPITAL)", "SOME HOSPITAL"),
    ("JOHN SMITH (D/B/A SMITH ENTERPRISES)", "JOHN SMITH"),
    ("JOHN SMITH (DBA SMITH ENTERPRISES)", "JOHN SMITH"),

    # Leading "THE" + trailing legal suffix both stripped
    ("THE WALT DISNEY COMPANY", "WALT DISNEY"),

    # Iterative suffix stripping (multiple trailing legal tokens)
    ("SOME CORP INC", "SOME"),

    # Never strip down to nothing
    ("INC", "INC"),

    # Trailing "and (its) affiliates" / "and subsidiaries"
    ("ACME CORP AND AFFILIATES", "ACME"),
    ("ACME CORP AND ITS AFFILIATES", "ACME"),
    ("ACME CORP AND SUBSIDIARIES", "ACME"),

    # "&" spelled out rather than dropped
    ("PROCTER & GAMBLE CO", "PROCTER AND GAMBLE"),

    # Plain suffix variants
    ("MICROSOFT CORPORATION", "MICROSOFT"),

    # On-behalf-of / OBO pass-throughs, including a suffix on the represented org
    ("SOME LAW FIRM ON BEHALF OF SMITH LLC", "SMITH"),
    ("SOME GROUP OBO OTHERCO LLC", "OTHERCO"),

    # NVIDIA / C.H. Robinson style — just a suffix strip, no other quirks
    ("NVIDIA CORPORATION", "NVIDIA"),
    ("NVIDIA", "NVIDIA"),
]

# (raw name variants as they'd occur across filings, expected display name)
DISPLAY_CASES = [
    # Most-frequent variant wins; renders in title case
    (["ANTHROPIC", "ANTHROPIC", "ANTHROPIC PBC"], "Anthropic"),

    # Acronym allowlist: USA / HCA / AARP / PG&E / AT&T stay uppercase;
    # Inc/Corp/Co/Corporation/Healthcare stay title-cased
    (["CHAMBER OF COMMERCE OF THE USA"], "Chamber of Commerce of the USA"),
    (["U.S. CHAMBER OF COMMERCE"], "U.S. Chamber of Commerce"),
    (["AARP"], "AARP"),
    (["AT&T CORP"], "AT&T Corp"),
    (["PG&E CORPORATION"], "PG&E Corporation"),
    (["HCA HEALTHCARE INC"], "HCA Healthcare Inc"),
    (["INTUIT INC"], "Intuit Inc"),

    # No-vowel heuristic catches short acronym-like tokens not on the allowlist
    (["ANTHROPIC PBC"], "Anthropic PBC"),

    # Small words lowercase only when not first
    (["THE WALT DISNEY COMPANY"], "The Walt Disney Company"),
    (["BANK OF AMERICA"], "Bank of America"),

    # Hyphenated compounds re-check each segment rather than blind-capitalizing
    # (was a bug: "CTIA-THE" -> "Ctia-The" instead of "CTIA-The")
    (["CTIA-THE WIRELESS ASSOCIATION"], "CTIA-The Wireless Association"),
    (["WAL-MART STORES INC"], "Wal-Mart Stores Inc"),

    # Parenthesized dotted acronyms stay uppercase, not just bare ones
    # (was a bug: "(N.A.C.H.)" -> "(n.a.c.h.)")
    (["NATIONAL ASSOCIATION OF CHILDRENS HOSPITALS (N.A.C.H.)"],
     "National Association of Childrens Hospitals (N.A.C.H.)"),
]


def main() -> int:
    failures = []

    print("canonical_client_key:")
    for raw, expected in KEY_CASES:
        got = canonical_client_key(raw)
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append(("key", raw, expected, got))
        print(f"  {status}  {raw[:60]:62s} -> {got!r}")

    print("\ndisplay_client_name:")
    for raw_names, expected in DISPLAY_CASES:
        got = display_client_name(raw_names)
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append(("display", raw_names, expected, got))
        print(f"  {status}  {str(raw_names)[:60]:62s} -> {got!r}")

    total = len(KEY_CASES) + len(DISPLAY_CASES)
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for kind, raw, exp, got in failures:
            print(f"  [{kind}] {raw!r}: expected {exp!r}, got {got!r}")
        return 1
    print(f"All {total} cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
