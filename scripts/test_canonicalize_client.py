"""Regression tests for client-name canonicalization (clients_norm.py).

Covers the edge-case classes mined from the real data:
  - legal-suffix variants of one org folding to the same key
  - "on behalf of" / "OBO" pass-throughs resolving to the represented org
  - former-name parentheticals (FORMERLY/FKA/F/K/A/D/B/A) stripped
  - leading "THE" and trailing "AND (ITS) AFFILIATES"/"AND SUBSIDIARIES" stripped
  - PKA / A/K/A / AKA / PREVIOUSLY (KNOWN|REPORTED|REGISTERED) AS markers
  - parenthesized "(ON BEHALF OF X)" / "(OBO X)" / O/B/O pass-throughs, and
    generic "ON BEHALF OF ITS CLIENTS" tails that must NOT pool firms
  - must-not-merge pairs of distinct organizations
  - display-name casing: small words, acronym allowlist, dotted tokens,
    slash/ampersand compounds, self-spelled acronyms, person names

Run: python scripts/test_canonicalize_client.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients_norm import canonical_client_key, display_client_name, display_person_name

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

    # ── Former-name aliases added by the 2026-08 identity review ──
    # A realistic OLD-name filing folds forward to the CURRENT canonical key.
    ("AIR TRANSPORT ASSOCIATION OF AMERICA, INC.", "AIRLINES FOR AMERICA"),
    ("GLAXOSMITHKLINE LLC", "GSK"),
    ("PORTLAND CEMENT ASSOCIATION", "AMERICAN CEMENT ASSOCIATION"),
    ("NATIONAL ASSOCIATION OF THEATRE OWNERS", "CINEMA UNITED"),
    ("INSTITUTE OF SCRAP RECYCLING INDUSTRIES, INC.", "RECYCLED MATERIALS ASSOCIATION"),
    ("BIOTECHNOLOGY INDUSTRY ORGANIZATION", "BIOTECHNOLOGY INNOVATION ORGANIZATION"),
    ("NATIONAL COUNCIL FOR BEHAVIORAL HEALTH", "NATIONAL COUNCIL FOR MENTAL WELLBEING"),
    ("U.S. GLOBAL LEADERSHIP CAMPAIGN", "US GLOBAL LEADERSHIP COALITION"),
    ("U.S. FUND FOR UNICEF", "UNICEF"),
    ("NATIONAL INDIAN GAMING ASSOCIATION", "INDIAN GAMING ASSOCIATION"),
    ("AMERISOURCEBERGEN CORPORATION", "CENCORA"),
    ("MEDLINE INDUSTRIES, LP", "MEDLINE"),
    ("VOYAGER SPACE HOLDINGS, INC.", "VOYAGER TECHNOLOGIES"),
    ("THE HARTFORD FINANCIAL SERVICES GROUP, INC.", "HARTFORD INSURANCE"),
    ("CORELOGIC, INC.", "CORELOGIC DBA COTALITY"),
    ("SHELL OIL COMPANY", "SHELL"),
    ("VISTA OUTDOOR INC.", "REVELYST"),
    ("PERIMETER SOLUTIONS, INC.", "PSNA"),
    ("OWENS & MINOR, INC.", "ACCENDRA HEALTH"),
    ("HUNTINGTON INGALLS INCORPORATED", "HUNTINGTON INGALLS INDUSTRIES"),
    ("AEROJET ROCKETDYNE, INC.", "AEROJET ROCKETDYNE AN L3HARRIS TECHNOLOGIES"),
    ("AMEREN CORPORATION", "AMEREN SERVICE"),
    ("AMERICAN EXPRESS COMPANY", "AMERICAN EXPRESS TRAVEL RELATED"),
    ("SYENSQO SA", "SYENSQO FINANCE (AMERICA)"),
    ("H&R BLOCK, INC.", "HRB TAX"),
    ("PMI GLOBAL SERVICES INC.", "PMI US CORPORATE"),
    ("VISTRA ENERGY CORP.", "VISTRA CORPORATE"),
    ("SI GROUP CLIENT SERVICES", "SWISHER INTERNATIONAL"),
    ("PARAMOUNT GLOBAL", "PARAMOUNT SKYDANCE"),

    # ── Former-name aliases added by the 2026-09 identity review ──
    # Renames/rebrands of one continuing org: an OLD-name filing folds forward
    ("NATIONAL CANNABIS ROUNDTABLE", "US CANNABIS ROUNDTABLE"),
    ("FLEXTRONICS INTERNATIONAL USA, INC.", "FLEX"),
    ("GAS TECHNOLOGY INSTITUTE", "GTI ENERGY"),
    ("SONOMA COUNTY WATER AGENCY", "SONOMA WATER"),
    ("SHORELIGHT EDUCATION, LLC", "SHORELIGHT"),
    ("QUALTRICS, LLC", "QUALTRICS INTERNATIONAL"),
    ("ST ENGINEERING NORTH AMERICA, INC.", "STE"),
    ("BRAEBURN PHARMACEUTICALS, INC.", "BRAEBURN"),
    ("TETHER OPERATIONS, S.A. DE C.V.", "TETHER HOLDINGS SA DE CV"),
    ("BDV SOLUTIONS, LLC", "VANTEO"),
    ("PHILLY SHIPYARD", "HANWHA PHILLY SHIPYARD"),
    ("CONSOL ENERGY INC", "CORE NATURAL RESOURCES"),
    ("HEALTHFIRST", "HF MANAGEMENT"),
    ("NAVIENT CORPORATION", "NAVIENT SOLUTIONS"),
    # American Wild Horse Campaign -> Conservation: old name and the dashed
    # normalizer artifact both fold to the clean current key
    ("AMERICAN WILD HORSE CAMPAIGN", "AMERICAN WILD HORSE CONSERVATION"),
    ("AMERICAN WILD HORSE CONSERVATION - FORMERLY AMERICAN WILD HORSE CAMPAIGN",
     "AMERICAN WILD HORSE CONSERVATION"),

    # Duplicate keys merged: one org fragmented by a suffix/DBA/typo/acronym
    # variant or a principal operating vehicle
    ("VERIZON", "VERIZON COMMUNICATIONS"),
    ("TOYOTA NORTH AMERICA, INC", "TOYOTA MOTOR"),
    ("DELL INC.", "DELL TECHNOLOGIES"),
    ("ABBOTT", "ABBOTT LABORATORIES"),
    ("ASTRAZENECA", "ASTRAZENECA PHARMACEUTICALS"),
    ("MASTERCARD WORLDWIDE", "MASTERCARD"),
    ("ORACLE AMERICAN INC.", "ORACLE"),
    ("DOW CHEMICAL COMPANY DBA DOW", "DOW CHEMICAL"),
    ('ELECTRONIC PAYMENTS COALITION ("EPC")', "ELECTRONIC PAYMENTS COALITION"),
    ("NATIONAL ASSOCIATION OF CHILDREN'S HOSPITALS (N.A.C.H.)-CHA",
     "NATIONAL ASSOCIATION OF CHILDRENS HOSPITALS"),
    ("GENERAL ELECTRIC COMPANY (INCLUDING SUBSIDIARIES)", "GENERAL ELECTRIC"),
    ("CHARTER COMMUNICATIONS OPERATING, LLC", "CHARTER COMMUNICATIONS"),
    ("DUKE ENERGY BUSINESS SERVICES LLC", "DUKE ENERGY"),
    ("FLUOR ENTERPRISES, INC.", "FLUOR"),
    ("LIBERTY MUTUAL INSURANCE COMPANY", "LIBERTY MUTUAL"),
    ("NORTHROP GRUMMAN SYSTEMS CORPORATION", "NORTHROP GRUMMAN"),
    ("GENERAL ATOMICS - AERONAUTICAL SYSTEMS, INC.", "GENERAL ATOMICS AERONAUTICAL SYSTEMS"),

    # ── Former-name aliases added by the 2026-10 identity review ──
    # Renames/rebrands of one continuing org: an OLD-name filing folds forward
    ("MOTION PICTURE ASSOCIATION OF AMERICA, INC.", "MOTION PICTURE ASSOCIATION"),
    ("SEMPRA ENERGY", "SEMPRA"),
    ("MORGAN STANLEY DEAN WITTER & CO.", "MORGAN STANLEY"),
    # Same US Domtar entity, briefly renamed Paper Excellence Holdings and back
    ("PAPER EXCELLENCE HOLDINGS CORPORATION", "DOMTAR"),
    # US subsidiary EADS North America -> Airbus Group, Inc. -> Airbus Americas
    ("AIRBUS GROUP, INC.", "AIRBUS AMERICAS"),

    # Duplicate keys merged: one org fragmented by a short-form / principal
    # operating-vehicle / parent<->flagship-subsidiary variant
    ("MASTERCARD INTERNATIONAL INCORPORATED", "MASTERCARD"),
    ("MICRON", "MICRON TECHNOLOGY"),
    ("VALERO", "VALERO ENERGY"),
    ("DISTILLED SPIRITS COUNCIL", "DISTILLED SPIRITS COUNCIL OF THE US"),
    ("LUMEN TECHNOLOGIES SERVICE GROUP, LLC", "LUMEN TECHNOLOGIES"),
    ("EXELON BUSINESS SERVICES COMPANY", "EXELON"),
    ("NEW YORK LIFE", "NEW YORK LIFE INSURANCE"),
    ("HCA, INC.", "HCA HEALTHCARE"),
    ("BOEHRINGER INGELHEIM PHARMACEUTICALS, INC.", "BOEHRINGER INGELHEIM"),
    ("INVESCO", "INVESCO HOLDING COMPANY (US)"),
    ("AMERICAN PUBLIC UNIVERSITY SYSTEM, INC.", "APEI"),

    # ── PKA / A/K/A / AKA / PREVIOUSLY markers (former or alternate names) ──
    ("CONSUMER BRANDS ASSOCIATION (PKA: GROCERY MANUFACTURERS ASSOCIATION)",
     "CONSUMER BRANDS ASSOCIATION"),
    ("CONSUMER BRANDS ASSOCIATION", "CONSUMER BRANDS ASSOCIATION"),
    ("ARXADA LLC (PREVIOUSLY REPORTED AS LONZA LLC)", "ARXADA"),
    ("ASHLAND INC (PREVIOUSLY KNOWN AS ASHLAND LLC)", "ASHLAND"),
    ("ANHEUSER-BUSCH COMPANIES INC (PREVIOUSLY: ANHEUSER BUSCH COMPANIES INC )",
     "ANHEUSER-BUSCH COMPANIES"),
    ("BREAKTHROUGH ENERGY VENTURES, LLC (PREV. REPORTED AS BREAKTHROUGH ENERGY, LLC)",
     "BREAKTHROUGH ENERGY VENTURES"),
    ("CONSUMER TECHNOLOGY ASSOCIATION (PREVIOUSLY THE CONSUMER ELECTRONICS ASSOCIATION",
     "CONSUMER TECHNOLOGY ASSOCIATION"),
    ("GE VERNOVA INTERNATIONAL LLC-PREVIOUSLY REPORTED AS GE RENEWABLES NORTH AMERICA",
     "GE VERNOVA INTERNATIONAL"),
    ("MECACC PREVIOUSLY KNOWN AS AMERICAN BUSINESS COUNCIL OF THE GULF COUNTRIES", "MECACC"),
    # A/K/A and AKA keep the primary name
    ("GINER, INC. A/K/A GINER LABS", "GINER"),
    ("SPACEX (AKA SPACE EXPLORATION TECHNOLOGIES CORP.)", "SPACEX"),
    ("EXPLORATION LABORATORIES LLC, AKA EXLABS", "EXPLORATION LABORATORIES"),
    # JBS USA's three filing names are one US filer
    ("JBS USA", "JBS"),
    ("JBS USA FOOD COMPANY", "JBS"),
    ("JBS USA FOOD COMPANY HOLDINGS (PREVIOUSLY REGISTERED AS JBS USA FOOD COMPANY)", "JBS"),
    # Marker letters inside real words are not markers
    ("SKAROREH KATENUAKA NATION, TUSCARORA NATION OF INDIANS",
     "SKAROREH KATENUAKA NATION TUSCARORA NATION OF INDIANS"),
    ("CITY OF APOPKA FL", "CITY OF APOPKA FL"),
    ("KHAWAM RIPKA, LLP", "KHAWAM RIPKA"),
    ("AKAMAI TECHNOLOGIES", "AKAMAI TECHNOLOGIES"),
    ("CENTER FOR DISEASE CONTROL (PREVENTION PROGRAMS)", "CENTER FOR DISEASE CONTROL (PREVENTION PROGRAMS)"),
    # Bare PREVIOUSLY without KNOWN/REPORTED/REGISTERED AS is left alone
    ("COALITION FOR PREVIOUSLY INCARCERATED PEOPLE", "COALITION FOR PREVIOUSLY INCARCERATED PEOPLE"),

    # ── On-behalf-of variants ──
    ("THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)", "BOEING"),
    ("AKIN GUMP STRAUSS HAUER & FELD (OBO GILA RIVER INDIAN COMMUNITY)", "GILA RIVER INDIAN COMMUNITY"),
    ("THORN RUN PARTNERS O/B/O FEDEX", "FEDEX"),
    ("KELLEY DRYE & WARREN LLP (OBO TOBACCO-FREE KIDS ACTION FUND))", "TOBACCO-FREE KIDS ACTION FUND"),
    ("MERCURY PUBLIC AFFAIRS, LLC (OBO CABOT CORP", "CABOT"),
    ("TWINLOGIC STRATEGIES LLP ON BEHALFOF INTERNATIONAL ASSOCIATION OF VENUE MANAGERS",
     "INTERNATIONAL ASSOCIATION OF VENUE MANAGERS"),
    ("EDUCATIONCOUNSEL (ON BEHALF OF INSTITUTE FOR HIGHER EDUCATION POLICY (IHEP)",
     "INSTITUTE FOR HIGHER EDUCATION POLICY"),
    ("LENOVO GROUP (PREV REPORTED AS FGS GLOBAL (ASIA) LIMITED (O/B/O LENOVO GROUP)", "LENOVO"),
    ("MLA ASSOCIATES ON BEHALF OF CLARK STREET ASSOCIATES LLC ON BEHALF OF QORVO", "QORVO"),
    # Possessive lead-ins are not part of the represented org's name...
    ("CHAMBERS CONLON & HARTWELL LLC ON BEHALF OF THEIR CLIENT ASLRRA", "ASLRRA"),
    ("AMERICAN CHEMISTRY COUNCIL OBO ITS CENTER FOR BIOCIDE CHEMISTRIES",
     "CENTER FOR BIOCIDE CHEMISTRIES"),
    # ...but "IT'S" is a word in a real name
    ("FARUK CAPAN ON BEHALF OF IT'S ALL GOOD, LLC", "ITS ALL GOOD"),
    # Generic tails name no organization: keep the filer, never a shared
    # "ITS CLIENTS" key across unrelated firms
    ("INNOVATIVE FEDERAL STRATEGIES ON BEHALF OF ITS CLIENTS", "INNOVATIVE FEDERAL STRATEGIES"),
    ("ACME ADVOCATES LLC ON BEHALF OF ITS MEMBERS", "ACME ADVOCATES"),
    ("ACME ADVOCATES LLC (ON BEHALF OF VARIOUS CLIENTS)", "ACME ADVOCATES"),
    ("SOME COALITION ON BEHALF OF ITSELF", "SOME COALITION"),
    # Letters "OBO" inside a word never split
    ("ROBOTICS INDUSTRIES ASSOCIATION", "ROBOTICS INDUSTRIES ASSOCIATION"),
    ("GLOBO MEDIA OBOE WORKS", "GLOBO MEDIA OBOE WORKS"),
]

# Distinct organizations that must keep distinct keys (guards against the
# marker/pass-through rules over-merging).
MUST_NOT_MERGE = [
    ("INNOVATIVE FEDERAL STRATEGIES ON BEHALF OF ITS CLIENTS",
     "OTHER FIRM LLC ON BEHALF OF ITS CLIENTS"),
    ("ACME ADVOCATES LLC ON BEHALF OF ITS MEMBERS",
     "BETA PARTNERS ON BEHALF OF ITS MEMBERS"),
    ("AMERICAN CHEMISTRY COUNCIL OBO ITS CENTER FOR BIOCIDE CHEMISTRIES",
     "AMERICAN CHEMISTRY COUNCIL"),
    ("SPACEX (AKA SPACE EXPLORATION TECHNOLOGIES CORP.)", "SPACE ALLIANCE"),
    ("AARP (TEXAS)", "AARP"),
    ("CVS PREVIOUSLY AETNA", "AETNA"),
    ("JBS USA", "JBS FOUNDATION"),
    ("ARXADA LLC (PREVIOUSLY REPORTED AS LONZA LLC)", "LONZA LLC"),
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

    # Slash compounds, ampersand initials, stylized brands
    (["KLEIN/JOHNSON GROUP"], "Klein/Johnson Group"),
    (["D&P CREATIVE STRATEGIES, LLC"], "D&P Creative Strategies, LLC"),
    (["INTERNATIONAL GROUP OF P&I CLUBS"], "International Group of P&I Clubs"),
    (["JOHNSON&JOHNSON"], "Johnson&Johnson"),
    (["THEGROUP DC, LLC"], "theGROUP DC, LLC"),
    # Leading punctuation doesn't swallow the capital ("(national" bug)
    (["MUSCOGEE (CREEK) NATION OF OKLAHOMA"], "Muscogee (Creek) Nation of Oklahoma"),
    (["AARP (TEXAS)"], "AARP (Texas)"),
    # Acronyms the name spells out, either direction; two-letter codes
    (["COUNCIL OF INFRASTRUCTURE FINANCING AUTHORITIES (CIFA)"],
     "Council of Infrastructure Financing Authorities (CIFA)"),
    (["HEALTHCARE SUPPLY CHAIN ASSOCIATION (HSCA)"], "Healthcare Supply Chain Association (HSCA)"),
    (["SECURITIES INDUSTRY AND FINANCIAL MARKETS ASSOCIATION (SIFMA)"],
     "Securities Industry and Financial Markets Association (SIFMA)"),
    (["WORLD ANTI-DOPING ASSOCIATION (WADA)"], "World Anti-Doping Association (WADA)"),
    (["NFIB (NATIONAL FEDERATION OF INDEPENDENT BUSINESS)"],
     "NFIB (National Federation of Independent Business)"),
    (["UPS (UNITED PARCEL SERVICE)"], "UPS (United Parcel Service)"),
    (["CITY OF RICHLAND (WA)"], "City of Richland (WA)"),
    (["TEXAS HOSPITAL ASSOCIATION (TEXAS)"], "Texas Hospital Association (Texas)"),
    # Former-name markers read as connectives; separators restart title case
    (["TP-LINK SYSTEMS INC. (FKA TP-LINK GLOBAL INC.)"], "TP-Link Systems Inc. (fka TP-Link Global Inc.)"),
    (["NCTA - THE INTERNET & TELEVISION ASSOCIATION"], "NCTA - The Internet & Television Association"),
    (["ZIONS BANCORPORATION, N.A. (FORMERLY KNOWN AS ZIONS FIRST NATIONAL BANK)"],
     "Zions Bancorporation, N.A. (Formerly Known as Zions First National Bank)"),
    # Surname conventions, numerals, ordinals, trailing foreign legal forms
    (["MCDERMOTT+ CONSULTING"], "McDermott+ Consulting"),
    (["LAND O'LAKES"], "Land O'Lakes"),
    (["3M COMPANY"], "3M Company"),
    (["21ST CENTURY FOX"], "21st Century Fox"),
    (["NORSE ATLANTIC AIRWAYS AS"], "Norse Atlantic Airways AS"),
    (["FIRST AS SECOND"], "First as Second"),

    # On-behalf-of pass-throughs display as the represented organization,
    # the same reading the canonical key groups them under
    (["CHRIS KISE & ASSOCIATES, P.A. ON BEHALF OF BANCREDITO HOLDING CORPORATION"],
     "Bancredito Holding Corporation"),
    (["THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)",
      "THE DOERRER GROUP LLC (ON BEHALF OF THE BOEING COMPANY)", "THE BOEING COMPANY"],
     "The Boeing Company"),
    (["INNOVATIVE FEDERAL STRATEGIES ON BEHALF OF ITS CLIENTS"], "Innovative Federal Strategies"),
]

# (raw LDA lobbyist name, expected display name)
PERSON_CASES = [
    ("mr JOHN FRANCIS WILLIAMS iii", "John Francis Williams III"),
    ("mr ROBERT M MAYER jr", "Robert M Mayer Jr."),
    ("ms ASHLEY ROSE O'SULLIVAN", "Ashley Rose O'Sullivan"),
    ("ANDREW MCKECHNIE", "Andrew McKechnie"),
    ("SAMANTHA CARL-YODER", "Samantha Carl-Yoder"),
    ("JOHN B. GREGG", "John B. Gregg"),
    ("CHARLES BRITTINGHAM ii", "Charles Brittingham II"),
    ("dr JANE DOE", "Dr. Jane Doe"),
    ("CAITLIN VAN SANT", "Caitlin Van Sant"),
    ("Jane McDoe", "Jane McDoe"),   # already cased: left alone
    ("", ""),
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

    print("\nmust not merge:")
    for left, right in MUST_NOT_MERGE:
        a, b = canonical_client_key(left), canonical_client_key(right)
        status = "ok  " if a != b else "FAIL"
        if a == b:
            failures.append(("distinct", (left, right), "different keys", a))
        print(f"  {status}  {left[:40]!r} vs {right[:40]!r}")

    print("\ndisplay_person_name:")
    for raw, expected in PERSON_CASES:
        got = display_person_name(raw)
        status = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures.append(("person", raw, expected, got))
        print(f"  {status}  {raw[:60]:62s} -> {got!r}")

    total = len(KEY_CASES) + len(DISPLAY_CASES) + len(MUST_NOT_MERGE) + len(PERSON_CASES)
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
