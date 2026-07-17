"""Regression tests for legislation-tag normalization (08_trends.py).

Covers the edge-case classes mined from the real data:
  - name / number / public-law variants of one law folding together
  - bill-number reuse across Congresses staying distinct
  - unambiguous short-form truncations resolving to the full act
  - ambiguous generic fragments dropping as noise
  - retrospective references disambiguated by an explicit year/Congress

Run: python scripts/test_normalize_legislation.py
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so 08_trends.py can `from db import ...`

spec = importlib.util.spec_from_file_location("trends", ROOT / "08_trends.py")
trends = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trends)
norm = trends.normalize_legislation

# (raw tag, filing year, expected normalized identity)
CASES = [
    # One Big Beautiful Bill Act — name / number / P.L. / spelling variants
    ("H.R. 1", 2026, "One Big Beautiful Bill Act"),
    ("One Big Beautiful Bill Act", 2026, "One Big Beautiful Bill Act"),
    ("One, Big, Beautiful Bill Act", 2026, "One Big Beautiful Bill Act"),
    ("P.L. 119-21", 2026, "One Big Beautiful Bill Act"),
    ("H.R. 1: One Big Beautiful Bill Act (Public Law No. 119-21)", 2026, "One Big Beautiful Bill Act"),

    # H.R. 1 reuse across Congresses stays distinct (the core collision)
    ("H.R. 1", 2018, "Tax Cuts and Jobs Act"),
    ("H.R. 1", 2021, "For the People Act"),
    ("H.R. 1", 2023, "Lower Energy Costs Act"),
    ("H.R. 1 - Lower Energy Costs Act", 2025, "Lower Energy Costs Act"),   # name beats year
    ("H.R. 1 of 2017", 2026, "Tax Cuts and Jobs Act"),                    # explicit year beats filing year

    # CARES Act: name / number / P.L.
    ("CARES Act", 2020, "CARES Act"),
    ("H.R. 748", 2020, "CARES Act"),
    ("P.L. 116-136", 2021, "CARES Act"),

    # IIJA: name / number / P.L. / "bipartisan infrastructure" nickname
    ("Infrastructure Investment and Jobs Act", 2022, "Infrastructure Investment and Jobs Act"),
    ("H.R. 3684", 2021, "Infrastructure Investment and Jobs Act"),
    ("P.L. 117-58", 2022, "Infrastructure Investment and Jobs Act"),
    ("Bipartisan Infrastructure Law", 2023, "Infrastructure Investment and Jobs Act"),

    # IRA: name + enacted P.L. fold; bare number stays a number (BBB/IRA shared vehicle)
    ("Inflation Reduction Act", 2023, "Inflation Reduction Act"),
    ("Inflation Reduction Act of 2022 (Public Law No. 117-169)", 2026, "Inflation Reduction Act"),
    ("P.L. 117-169", 2023, "Inflation Reduction Act"),
    ("Build Back Better Act", 2022, "Build Back Better Act"),

    # CHIPS: name / truncation / year-suffixed truncation / P.L.
    ("CHIPS and Science Act", 2023, "CHIPS and Science Act"),
    ("Chips+Science Act", 2023, "CHIPS and Science Act"),
    ("Science Act", 2023, "CHIPS and Science Act"),
    ("Science Act of 2022", 2025, "CHIPS and Science Act"),   # year-suffixed truncation
    ("P.L. 117-167", 2023, "CHIPS and Science Act"),

    # For the People Act was H.R. 1 in both the 116th (2019) and 117th
    ("H.R. 1", 2019, "For the People Act"),
    ("H.R. 1", 2020, "For the People Act"),
    ("For the People Act of 2019", 2020, "For the People Act"),

    # USICA truncation
    ("United States Innovation and Competition Act of 2021", 2021, "U.S. Innovation and Competition Act"),
    ("Competition Act of 2021", 2021, "U.S. Innovation and Competition Act"),
    ("S. 1260", 2021, "U.S. Innovation and Competition Act"),

    # Ambiguous generic fragments → dropped
    ("Jobs Act", 2022, ""),
    ("America Act", 2021, ""),
    ("Act", 2022, ""),

    # Fiscal-year spelling unification for recurring titles
    ("FY27 National Defense Authorization Act", 2026, "FY2027 National Defense Authorization Act"),
    ("FY2027 National Defense Authorization Act", 2026, "FY2027 National Defense Authorization Act"),

    # A bare number with no known mapping keeps its Congress scope
    ("H.R. 7148", 2026, "H.R. 7148 (119th Congress)"),
    ("H.R. 2670 (118th Congress)", 2024, "H.R. 2670 (118th Congress)"),

    # CARES truncation + Affordable Care Act canonicalization (audit-surfaced)
    ("Economic Security Act", 2020, "CARES Act"),
    ("Coronavirus Aid, Response, and Economic Security Act", 2020, "CARES Act"),
    ("Affordable Care Act", 2024, "Affordable Care Act"),
    ("Patient Protection and Affordable Care Act", 2024, "Affordable Care Act"),

    # A leading article must not drop a real titled act (was a bug)
    ("the Equality Act", 2023, "Equality Act"),
    ("The Safe Banking Act", 2022, "Safe Banking Act"),
    ("the National Defense Authorization Act", 2024, "National Defense Authorization Act"),

    # Bare articles / conjunctions still drop as noise
    ("the", 2023, ""),
    ("an act", 2023, ""),
    ("and extensions", 2023, ""),

    # Truncation tail of "Full-Year Continuing Appropriations and Extensions
    # Act" — the real identity (H.R. 1968 / P.L. 119-4) rides on the same
    # activity, so the bare fragment drops as noise.
    ("Extensions Act", 2025, ""),
    ("the Extensions Act", 2025, ""),
    ("Extensions Act of 2025", 2025, ""),
    ("Appropriations Act, 2025", 2025, ""),
    # The full title keeps its identity (year-ambiguous, so not aliased)
    ("Full-Year Continuing Appropriations and Extensions Act, 2025", 2025,
     "Full-Year Continuing Appropriations and Extensions Act, 2025"),
]


def main() -> int:
    failures = []
    for raw, year, expected in CASES:
        got = norm(raw, year)
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append((raw, year, expected, got))
        print(f"  {status}  [{year}] {raw[:46]:48s} -> {got!r}")
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for raw, year, exp, got in failures:
            print(f"  [{year}] {raw!r}: expected {exp!r}, got {got!r}")
        return 1
    print(f"All {len(CASES)} cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
