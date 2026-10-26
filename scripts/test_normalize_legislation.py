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
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so 08_trends.py can `from db import ...`

spec = importlib.util.spec_from_file_location("trends", ROOT / "08_trends.py")
trends = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trends)
norm = trends.normalize_legislation

# Deterministic fixtures: tests do not depend on network or the production DB.
REFERENCE = {
    (118, 'hr', 9495): ('2024-09-09', 'Stop Terror-Financing and Tax Penalties on American Hostages Act'),
    (119, 'hr', 9495): ('2026-06-26', 'Department of Defense Appropriations Act, 2027'),
    (118, 'hr', 8800): ('2024-06-21', 'Fixture previous-Congress bill'),
    (119, 'hr', 8800): ('2026-05-13', 'Fixture current-Congress bill'),
    (118, 'hr', 9900): ('2024-10-01', 'Previous Congress only'),
    (119, 'hr', 1234): ('2025-02-10', 'Current Congress only'),
    (118, 'hr', 1): ('2023-03-14', 'Lower Energy Costs Act'),
    (119, 'hr', 1): ('2025-05-20', 'Source title must not override curated title'),
}
DATED_CASES = [
    ('H.R. 9495', 2025, '2025-07-15', 'H.R. 9495 (118th Congress)'),
    ('H.R. 9495', 2026, '2026-07-20', 'H.R. 9495 (119th Congress)'),
    ('H.R. 9495', 2026, '2026-06-26T08:00:00-04:00', 'H.R. 9495 (119th Congress)'),
    ('H.R. 8800', 2025, '2025-02-15', 'H.R. 8800 (118th Congress)'),
    ('H.R. 9900', 2025, '2025-02-15', 'H.R. 9900 (118th Congress)'),
    # Report-year Congress stays authoritative; posting year supplies only D.
    ('H.R. 9495', 2024, '2026-07-20', 'H.R. 9495 (118th Congress)'),
    ('H.R. 9495 (119th Congress)', 2025, '2025-07-15', 'H.R. 9495 (119th Congress)'),
    ('H.R. 9495 of 2025', 2024, '2025-02-15', 'H.R. 9495 (119th Congress)'),
    ('H.R. 9495 of 2024', 2026, '2026-07-20', 'H.R. 9495 (118th Congress)'),
    ('H.R. 9495 - CARES Act', 2025, '2025-02-15', 'CARES Act'),
    ('H.R. 1', 2025, '2025-02-15', 'Lower Energy Costs Act'),
    ('H.R. 1', 2025, '2025-05-20', 'One Big Beautiful Bill Act'),
    ('H.R. 1234', 2025, '2025-02-09', 'H.R. 1234 (119th Congress)'),
    ('H.R. 1234', 2025, '2025-02-10', 'H.R. 1234 (119th Congress)'),
    ('H.R. 1234', 2025, None, 'H.R. 1234 (119th Congress)'),
    ('H.R. 1234', 2025, 'invalid', 'H.R. 1234 (119th Congress)'),
    ('H.R. 99999', 2025, '2025-02-15', 'H.R. 99999 (119th Congress)'),
]

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

    # CARES Act: name / number / P.L. / original Senate vehicle (S. 3548)
    ("CARES Act", 2020, "CARES Act"),
    ("H.R. 748", 2020, "CARES Act"),
    ("P.L. 116-136", 2021, "CARES Act"),
    ("S. 3548", 2020, "CARES Act"),   # original Senate CARES bill, 116th
    ("S.3548", 2020, "CARES Act"),

    # Moving Forward Act (H.R. 2, 116th) — name / number / name-beats-wrong-year
    ("Moving Forward Act", 2020, "Moving Forward Act"),
    ("H.R. 2", 2020, "Moving Forward Act"),
    ("H.R. 2 (116th Congress)", 2024, "Moving Forward Act"),   # Congress qual beats filing year
    ("H.R. 2 - Moving Forward Act", 2023, "Moving Forward Act"),  # name beats wrong-year number

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

    # Cross-Congress number REUSE stays unmapped — same number, different law
    # per Congress, so a bare number must NOT fold to the other Congress's act.
    # H.R. 5376 (117th) was BOTH Build Back Better and the IRA vehicle — bare
    # number is ambiguous, only the enacted P.L. 117-169 maps to the IRA.
    ("H.R. 5376", 2022, "H.R. 5376 (117th Congress)"),
    # H.R. 3684 was the IIJA in the 117th; in the 118th it is an unrelated bill.
    ("H.R. 3684", 2023, "H.R. 3684 (118th Congress)"),
    # H.R. 748 was the CARES Act in the 116th; in the 117th it is Ethan's Law.
    ("H.R. 748", 2021, "H.R. 748 (117th Congress)"),
    # Recurring appropriations title stays a scoped number, never a bare name.
    ("H.R. 133", 2020, "H.R. 133 (116th Congress)"),

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

    # "<Subcommittee>, and Related Agencies Appropriations Act" tails lose
    # their subject in every spelling, with or without a year.
    ("Related Agencies Appropriations Act", 2026, ""),
    ("Related Agencies Appropriations Act of 2027", 2026, ""),
    ("Related Agencies Act", 2026, ""),
    ("Related Agencies Subcommittee Appropriations Act", 2026, ""),
    # Statute sections / code citations are provisions, not bills
    ("Section 232", 2026, ""),
    ("Section 45Z", 2026, ""),
    ("49 USC 22907", 2026, ""),
    ("40 C.F.R. 80", 2026, ""),
    # Landmark-law patterns only match when they START the name: a longer
    # act that merely ends with the short name keeps its own identity.
    ("Honoring Our Fallen Heroes Act", 2026, "Honoring Our Fallen Heroes Act"),
    ("Helping Our Heroes Act", 2026, "Helping Our Heroes Act"),
    ("The HEROES Act", 2020, "Heroes Act"),
    ("H.R. 6800 Heroes Act", 2021, "Heroes Act"),
    ("Sound Science Act of 2026", 2026, "Sound Science Act of 2026"),
    ("Science Act of 2026", 2026, "Science Act of 2026"),
    ("SAFE Chips Act", 2026, "SAFE Chips Act"),
    ("Stop Stealing Our Chips Act", 2026, "Stop Stealing Our Chips Act"),
    ("CHIPS Act", 2024, "CHIPS and Science Act"),
    ("Amtrak Funding, SBA Funding, CHIPS Act", 2024, "CHIPS and Science Act"),
    ("FY2022 Inflation Reduction Act", 2023, "Inflation Reduction Act"),
    ("Restoring Affordable Care Act", 2026, "Restoring Affordable Care Act"),
]

# Fixture titles for the title-aware cases (shapes copied from the 119th
# Congress reference: same-titled bills, truncating titles, a curated alias).
TITLES = {
    (119, 'hr', 4776): ('2025-07-24', 'SPEED Act'),
    (119, 's', 1894): ('2025-05-22', 'SPEED Act'),
    (119, 'hr', 7567): ('2026-02-23', 'Farm, Food, and National Security Act of 2026'),
    (119, 'hr', 7153): ('2026-01-20', 'Agriculture and National Security Act of 2026'),
    (119, 'hr', 6644): ('2025-12-11', '21st Century ROAD to Housing Act'),
    (119, 's', 2651): ('2025-07-29', 'ROAD to Housing Act of 2025'),
    (119, 'hr', 2563): ('2025-04-01', 'Fostering Success in Higher Education Act of 2025'),
    (119, 'hr', 3292): ('2025-05-07', 'REPORT Act'),
    (119, 'hr', 4710): ('2025-07-17', 'No Surprises Act Enforcement Act'),
    (119, 'hr', 9497): ('2026-07-30', 'Water Resources Development Act of 2026'),
    (119, 's', 4949): ('2026-07-29', 'Water Resources Development Act of 2026'),
    (119, 'hr', 1968): ('2025-03-06', 'Full-Year Continuing Appropriations and Extensions Act, 2025'),
}
# (raw activity tag list, filing year, expected identities)
TAG_LIST_CASES = [
    # Unpunctuated lists split into their acts; leading leftovers drop
    (["Report Act Guard Act"], 2026, ["REPORT Act", "Guard Act"]),
    (["Act ENFORCE Act"], 2026, ["ENFORCE Act"]),
    (["Bill CONSENT Act"], 2026, ["CONSENT Act"]),
    (["FY27 National Defense Authorization Act FY27 Defense Appropriations Act"], 2026,
     ["FY2027 National Defense Authorization Act", "FY2027 Defense Appropriations Act"]),
    (["Jobs Act One Big Beautiful Bill Act"], 2026, ["One Big Beautiful Bill Act"]),
    (["Beneficial Ownership, Corporate Transparency Act Bank Secrecy Act"], 2026,
     ["Corporate Transparency Act", "Bank Secrecy Act"]),
    (["Science Act, FABS Act"], 2023, ["CHIPS and Science Act", "FABS Act"]),
    # Real titles with an inner "Act" stay whole
    (["No Surprises Act Enforcement Act"], 2026, ["No Surprises Act Enforcement Act"]),
    (["Endangered Species Act Amendments Act of 2025"], 2026,
     ["Endangered Species Act Amendments Act of 2025"]),
    (["Weather Act Reauthorization Act"], 2026, ["Weather Act Reauthorization Act"]),
    # A name repeating or truncating the title of a bill the same activity
    # cites by number counts once, as the number.
    (["H.R. 4776", "SPEED Act"], 2026, ["H.R. 4776 (119th Congress)"]),
    (["HR 6644", "Housing Act"], 2026, ["H.R. 6644 (119th Congress)"]),
    (["H.R. 7567", "National Security Act of 2026"], 2026, ["H.R. 7567 (119th Congress)"]),
    (["S. 4949", "Water Resources Development Act of 2026"], 2026, ["S. 4949 (119th Congress)"]),
    # ...but a different year, or a statute named after an article, doesn't.
    (["S. 2651", "Housing Act of 2024"], 2026, ["S. 2651 (119th Congress)", "Housing Act of 2024"]),
    # A full title shared by several bills takes the official spelling.
    (["Report Act"], 2026, ["REPORT Act"]),
    (["water resources development act of 2026"], 2026, ["Water Resources Development Act of 2026"]),
]
# Learned folding: (rows of (year, raw tags), expected aliases subset, absent names)
ALIAS_ROWS = (
    [(2026, ["H.R. 4776", "SPEED Act"])] * 6 + [(2026, ["SPEED Act"])] * 4
    + [(2026, ["HR 6644 21st Century Road to Housing Act Housing Act"])] * 5
    + [(2026, ["Housing Act"])] * 2
    + [(2026, ["H.R. 7567", "National Security Act of 2026"])] * 4
    + [(2026, ["H.R. 7153", "National Security Act of 2026"])]
    + [(2026, ["National Security Act of 2026"])] * 2
    # A statute that also ends a bill title: cited mostly on its own.
    + [(2026, ["H.R. 2563", "Higher Education Act"])] * 2 + [(2026, ["Higher Education Act"])] * 8
)


def main() -> int:
    failures = []
    for raw, year, expected in CASES:
        got = norm(raw, year, reference={})
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append((raw, year, expected, got))
        print(f"  {status}  [{year}] {raw[:46]:48s} -> {got!r}")
    unresolved = Counter()
    for raw, year, posted, expected in DATED_CASES:
        got = norm(raw, year, posted, reference=REFERENCE, unresolved=unresolved)
        if got != expected:
            failures.append((raw, year, expected, got))
    assert unresolved == {'2025-02': 2, 'unknown': 2}, unresolved
    # Every bill/resolution type follows the same date and fallback rule.
    for kind, prefix in trends.BILL_TYPES.items():
        reference = {(118, kind, 900): ('2024-05-01', 'Previous bill'),
                     (119, kind, 900): ('2026-01-01', 'New bill')}
        for raw in (f'{prefix} 900', f'{kind.upper()}900'):
            assert norm(raw, 2025, '2025-02-15', reference=reference) == f'{prefix} 900 (118th Congress)'
            assert norm(raw, 2026, '2026-01-01', reference=reference) == f'{prefix} 900 (119th Congress)'
    for raw, year, expected in TAG_LIST_CASES:
        got = trends.normalize_legislation_tags(raw, year, f'{year}-07-20', reference=TITLES)
        if got != expected:
            failures.append((raw, year, expected, got))
    aliases = trends.learn_name_aliases(ALIAS_ROWS, TITLES)
    expected_aliases = {
        ('SPEED Act', 119): 'H.R. 4776 (119th Congress)',      # 6 of 10 co-cited
        ('Housing Act', 119): 'H.R. 6644 (119th Congress)',    # 5 of 7
        ('National Security Act of 2026', 119): 'H.R. 7567 (119th Congress)',  # 4 of 7, top bill
    }
    if aliases != expected_aliases:
        failures.append(('learn_name_aliases', 2026, expected_aliases, aliases))
    for raw, expected in ((["SPEED Act"], ["H.R. 4776 (119th Congress)"]),
                          (["Housing Act"], ["H.R. 6644 (119th Congress)"]),
                          (["Higher Education Act"], ["Higher Education Act"])):
        got = trends.normalize_legislation_tags(raw, 2026, '2026-07-20', reference=TITLES, aliases=aliases)
        if got != expected:
            failures.append((raw, 2026, expected, got))
    # Aliases are scoped to their Congress.
    got = trends.normalize_legislation_tags(["SPEED Act"], 2023, '2023-07-20', reference=TITLES, aliases=aliases)
    if got != ["SPEED Act"]:
        failures.append((["SPEED Act"], 2023, ["SPEED Act"], got))
    # Public laws fold into their enacting bill when the law record is held.
    prior_laws = trends.load_public_laws
    trends.load_public_laws = lambda: {(119, 4): (119, 'hr', 1968), (119, 9): (119, '', 0)}
    try:
        for raw, expected in (('P.L. 119-4', 'H.R. 1968 (119th Congress)'),
                              ('PL119-4', 'H.R. 1968 (119th Congress)'),
                              ('P.L. 119-21', 'One Big Beautiful Bill Act'),  # curated alias wins
                              ('P.L. 119-9', 'P.L. 119-9'),     # record names no bill
                              ('P.L. 119-99', 'P.L. 119-99')):  # law not yet held
            got = norm(raw, 2026)
            if got != expected:
                failures.append((raw, 2026, expected, got))
        # A caller-supplied bill reference means no law table.
        assert norm('P.L. 119-4', 2026, reference={}) == 'P.L. 119-4'
    finally:
        trends.load_public_laws = prior_laws
    prior = trends.load_reference
    trends.load_reference = lambda: REFERENCE
    try:
        bill = 'H.R. 8800 (119th Congress)'
        label = bill + ' — Fixture current-Congress bill'
        assert trends.display_legislation(bill) == label
        assert trends.display_legislation('H.R. 1 (119th Congress)') == 'One Big Beautiful Bill Act'
        # Detail lookup keys and name values must remain in agreement.
        payload = {'legislation': [{'name': bill}], 'legislation_series': {bill: [1, 2]},
                   'recent': [{'legislation': [bill]}]}
        out = trends._export_bill_labels(payload)
        assert out['legislation'][0]['name'] == label
        assert out['legislation_series'][label] == [1, 2]
        assert out['recent'][0]['legislation'] == [label]
        assert trends.display_legislation('H.R. 99999 (119th Congress)') == 'H.R. 99999 (119th Congress)'
    finally:
        trends.load_reference = prior
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for raw, year, exp, got in failures:
            print(f"  [{year}] {raw!r}: expected {exp!r}, got {got!r}")
        return 1
    print(f"All {len(CASES)} original, {len(DATED_CASES)} dated and {len(TAG_LIST_CASES)} tag-list cases passed; "
          "learned name aliases, public laws, all eight bill types, unresolved counts, titles and export joins passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
