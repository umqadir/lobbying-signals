"""Client-name canonicalization and display-name rendering.

Registrants and clients in the LDA data are entered by hand at filing time, so
the same organization shows up under several raw spellings — legal-suffix
variants ("ANTHROPIC" / "ANTHROPIC PBC" / "ANTHROPIC, PBC"), "on behalf of"
pass-throughs (a law firm filing "AQUIA GROUP ON BEHALF OF ANTHROPIC, PBC"),
and former-name parentheticals. `canonical_client_key` folds those into one
stable, ALL-CAPS grouping key; `display_client_name` turns the group's raw
variants into a single human-readable name for the UI.

Neither function needs to be perfect — just good enough that the dashboard's
organization-spend rollups don't fragment obvious duplicates or render
"Chamber OF Commerce OF The U.s.a."-style casing bugs.
"""

import re

# ─── verified-erroneous filers ───

# Filings verified by hand against the Senate record to report figures that
# are not U.S.-dollar lobbying spend. The LDA system accepts these as filed;
# excluding them is editorial curation, so every entry needs a reason and the
# evidence. Keys are canonical_client_key() outputs.
#
# "State of Loc Nation" (a self-described sovereign micronation; filings
# 4d5b1cb0-0971-43b8-958b-d260e2be8af1, 892864a7-3824-416c-ad6b-413769e9de0c
# et al.) reports a flat $20,000,000 per quarter that its own activity text
# denominates in "Loc Nation Dollars (LND)", a self-issued currency — not
# lobbying income. It would otherwise rank among the top five U.S. spenders.
EXCLUDED_CLIENT_KEYS = {
    'STATE OF LOC NATION GLOBAL PUBLIC BENEFIT',
    'LOC COMMUNITY ASSOCIATION',
    'QUEENDOM STATE OF LOC NATION GLOBAL PUBLIC BENEFIT CORPORATION AND TRUST',
}

# ─── canonical_client_key ───

# "X ON BEHALF OF Y", "X OBO Y", "X O/B/O Y", and the parenthesized forms
# "X (ON BEHALF OF Y)" / "X (OBO Y)" (plus the "BEHALFOF" typo). The marker
# must stand alone — preceded by a space or "(" and followed by a space — so
# words that merely contain the letters ("ROBOTICS") never split.
_ON_BEHALF_RE = re.compile(
    r'(?:\s+|\s*\(\s*)(?:ON\s+BEHALF\s*OF|O/?B/?O)\s+',
    re.IGNORECASE,
)

# A represented party that names no organization: "ON BEHALF OF ITS
# CLIENTS / ITS MEMBERS / VARIOUS CLIENTS / ITSELF". Keying on that tail
# would pool unrelated firms under one "ITS CLIENTS" identity, so the
# filer named before the phrase is kept instead.
_GENERIC_PARTY_RE = re.compile(
    r'^(?:(?:ITS|THEIR|HIS|HER|OUR)\s+(?:OWN\s+)?)?'
    r'(?:(?:VARIOUS|MULTIPLE|SEVERAL|CERTAIN|UNNAMED|UNDISCLOSED|OTHER|ALL)\s+)?'
    r'(?:CLIENTS?|MEMBERS?|MEMBERSHIP|AFFILIATES|SUBSIDIARIES|ITSELF|THEMSELVES|SELF)'
    r'[\s.,;:)]*$',
    re.IGNORECASE,
)
# "ON BEHALF OF THEIR CLIENT ASLRRA" / "OBO ITS SPRAY FOAM COALITION": the
# possessive lead-in is not part of the represented organization's name.
# Requires the bare word ITS (so "IT'S ALL GOOD, LLC" is untouched).
_POSSESSIVE_LEAD_RE = re.compile(
    r'^(?:ITS|THEIR)\s+(?:CLIENTS?\s+|MEMBERS?\s+)?(?=\S)',
    re.IGNORECASE,
)

# Parentheticals that carry a former/alternate name, not part of the identity.
# The closing paren is optional: filers truncate long names mid-parenthetical
# ("META PLATFORMS INC (FORMERLY FACEBOOK INC AND VARIOUS SUBSIDIARIES"),
# which would otherwise mint a whole separate identity.
# No \b after the legacy markers: filers omit the space ("(FKARAYTHEON
# ...)"), and inside a parenthetical those prefixes are unambiguous. The
# newer markers (PKA / AKA / PREVIOUSLY ...) DO require a non-letter after
# them — "(AKAMAI ...)" or "(PREVENTION ...)" are names, not markers.
# A/K/A and AKA name an alternate for the SAME organization, so dropping the
# parenthetical keeps the primary name, which is the identity.
_FORMER_NAME_PAREN_RE = re.compile(
    r'\(\s*(?:FORMERLY|FKA|F/K/A|D/B/A|DBA'
    r'|(?:PKA|P/K/A|AKA|A/K/A|PREVIOUSLY|PREV\.?(?=\s))(?![A-Z]))'
    r'[^)]*(?:\)|$)',
    re.IGNORECASE,
)

_LEADING_THE_RE = re.compile(r'^THE\s+', re.IGNORECASE)

_TRAILING_AFFILIATES_RE = re.compile(
    r'\s+AND\s+(?:ITS\s+)?AFFILIATES\s*$', re.IGNORECASE
)
_TRAILING_SUBSIDIARIES_RE = re.compile(
    r'\s+AND\s+(?:ITS\s+|VARIOUS\s+)?SUBSIDIARIES\s*$', re.IGNORECASE
)

# Corporate renames within the data window (2020+), so an organization's
# history doesn't split at the rename. Maps canonical_client_key output of
# the FORMER name to the canonical key of the CURRENT name. Only add
# well-known, verifiable renames of major filers — this is curation, not
# fuzzy matching. Maintained partly by the monthly identity review
# (.github/workflows/identity-review.yml), which researches candidates
# surfaced by scripts/audit_client_identities.py. Keep every entry pointing
# at the CURRENT canonical key — flatten chains rather than aliasing to an
# aliased key.
_FORMER_NAME_ALIASES = {
    'FACEBOOK': 'META PLATFORMS',                # renamed Oct 2021
    'RAYTHEON': 'RTX',                           # Raytheon Co pre-merger
    'RAYTHEON TECHNOLOGIES': 'RTX',              # renamed Jul 2023

    # ── Monthly identity review, 2026-08 ──
    # Each entry is a rename/rebrand of ONE continuing organization (or the
    # same company relabeling the legal vehicle it files lobbying under),
    # verified against corporate/press records. Acquisitions, spin-offs, and
    # distinct sister subsidiaries were deliberately left split (see the PR).

    # Trade associations & nonprofits that renamed
    'AIR TRANSPORT ASSOCIATION OF AMERICA': 'AIRLINES FOR AMERICA',   # renamed 2011
    'PORTLAND CEMENT ASSOCIATION': 'AMERICAN CEMENT ASSOCIATION',     # renamed May 2025
    'NATIONAL ASSOCIATION OF THEATRE OWNERS': 'CINEMA UNITED',        # renamed Mar 2025
    'INSTITUTE OF SCRAP RECYCLING INDUSTRIES': 'RECYCLED MATERIALS ASSOCIATION',  # ReMA, Apr 2024
    'BIOTECHNOLOGY INDUSTRY ORGANIZATION': 'BIOTECHNOLOGY INNOVATION ORGANIZATION',  # BIO, Jan 2016
    'NATIONAL COUNCIL FOR BEHAVIORAL HEALTH': 'NATIONAL COUNCIL FOR MENTAL WELLBEING',  # renamed May 2021
    'US GLOBAL LEADERSHIP CAMPAIGN': 'US GLOBAL LEADERSHIP COALITION',  # renamed Jul 2009
    'US FUND FOR UNICEF': 'UNICEF',                                   # d/b/a UNICEF USA, ~2017
    'NATIONAL INDIAN GAMING ASSOCIATION': 'INDIAN GAMING ASSOCIATION',  # dropped "National", 2022

    # Companies that renamed/rebranded (same legal entity continuing)
    'GLAXOSMITHKLINE': 'GSK',                    # GSK plc, May 2022
    'AMERISOURCEBERGEN': 'CENCORA',              # renamed Aug 2023
    'MEDLINE INDUSTRIES': 'MEDLINE',             # shorter form of the same filer
    'VOYAGER SPACE': 'VOYAGER TECHNOLOGIES',     # Voyager Space Holdings -> Voyager Technologies, Feb 2025
    'HARTFORD FINANCIAL': 'HARTFORD INSURANCE',  # The Hartford, charter rename Feb 2025
    'CORELOGIC': 'CORELOGIC DBA COTALITY',       # CoreLogic rebranded as Cotality, Mar 2025
    'SHELL OIL': 'SHELL',                        # Shell Oil Company -> Shell USA, Inc., 2022
    'VISTA OUTDOOR': 'REVELYST',                 # Vista Outdoor Inc renamed after divesting ammo (Kinetic Group), 2024
    'PERIMETER SOLUTIONS': 'PSNA',               # PHOS-CHEK filer relabeled on 2024 Lux->DE redomicile
    'OWENS AND MINOR': 'ACCENDRA HEALTH',        # Owens & Minor Inc renamed itself Accendra Health, Dec 2025
    'HUNTINGTON INGALLS': 'HUNTINGTON INGALLS INDUSTRIES',  # legal-name variant of HII

    # Continuing entity that gained a parent tag in its filing name (NOT a
    # fold into the acquirer's own key — Aerojet Rocketdyne stays its own
    # identity; L3Harris is moving to spin it back out).
    'AEROJET ROCKETDYNE': 'AEROJET ROCKETDYNE AN L3HARRIS TECHNOLOGIES',

    # Same company relabeling its in-house filing vehicle (parent brand ->
    # its own services/finance/operating entity; not a distinct sister sub)
    'AMEREN': 'AMEREN SERVICE',                  # Ameren Corp -> Ameren Services Company
    'AMERICAN EXPRESS': 'AMERICAN EXPRESS TRAVEL RELATED',  # AmEx Co -> its TRS operating sub
    'SYENSQO': 'SYENSQO FINANCE (AMERICA)',      # Syensqo -> its US finance LLC
    'H AND R BLOCK': 'HRB TAX',                  # H&R Block Inc -> HRB Tax Group Inc
    'PMI GLOBAL': 'PMI US CORPORATE',            # PMI Global Services -> PMI US Corporate Services (Philip Morris Intl)
    'VISTRA ENERGY': 'VISTRA CORPORATE',         # Vistra (renamed from Vistra Energy, 2020) -> Vistra Corporate Services Co
    'SI GROUP CLIENT': 'SWISHER INTERNATIONAL',  # SI Group Client Services -> Swisher International, Inc.

    # Dominant continuing entity renamed after a bolt-on merger (Skydance's
    # own small, separate lobbying stays under its own key).
    'PARAMOUNT GLOBAL': 'PARAMOUNT SKYDANCE',    # Paramount Global -> Paramount Skydance Corp, Aug 2025

    # ── Monthly identity review, 2026-09 ──
    # Renames/rebrands of ONE continuing organization, verified against the
    # raw FKA/FORMERLY evidence in the filings plus corporate/press records.
    # Acquisitions, spin-offs, joint ventures, and distinct-operation sister
    # subsidiaries were deliberately left split (see the PR).

    # Trade associations, nonprofits & public agencies that renamed
    'NATIONAL CANNABIS ROUNDTABLE': 'US CANNABIS ROUNDTABLE',   # rebranded 2024
    'GAS TECHNOLOGY INSTITUTE': 'GTI ENERGY',                   # GTI Energy, 2022
    'SONOMA COUNTY WATER AGENCY': 'SONOMA WATER',               # d/b/a Sonoma Water, 2018
    # American Wild Horse Campaign -> American Wild Horse Conservation (2024).
    # The org's raw filing name ("... CONSERVATION - FORMERLY ...") leaves a
    # trailing "-" after the bare-former-tail strip, minting a separate key;
    # both the old name and that dashed artifact fold to the clean key.
    'AMERICAN WILD HORSE CAMPAIGN': 'AMERICAN WILD HORSE CONSERVATION',
    'AMERICAN WILD HORSE CONSERVATION -': 'AMERICAN WILD HORSE CONSERVATION',

    # Companies that renamed/rebranded (same legal entity continuing)
    'FLEXTRONICS INTERNATIONAL': 'FLEX',         # Flextronics International -> Flex Ltd, 2015
    'SHORELIGHT EDUCATION': 'SHORELIGHT',        # dropped "Education" from the LLC name
    'QUALTRICS': 'QUALTRICS INTERNATIONAL',      # Qualtrics LLC -> Qualtrics International Inc
    'ST ENGINEERING': 'STE',                     # ST Engineering North America -> STE North America
    'BRAEBURN PHARMACEUTICALS': 'BRAEBURN',      # Braeburn Pharmaceuticals Inc -> Braeburn Inc
    'BDV SOLUTIONS': 'VANTEO',                   # BDV Solutions rebranded as Vanteo, 2025
    'TETHER OPERATIONS SA DE CV': 'TETHER HOLDINGS SA DE CV',   # same S.A. de C.V. entity renamed

    # Continuing entity that gained a parent tag on its filing name (NOT a
    # fold into a separate acquirer's own key — the shipyard's own lobbying
    # identity continues, cf. AEROJET ROCKETDYNE above).
    'PHILLY SHIPYARD': 'HANWHA PHILLY SHIPYARD',   # renamed after Hanwha's Dec 2024 acquisition

    # Surviving parent entity renamed in a merger of equals. CONSOL Energy
    # Inc (the parent) was renamed Core Natural Resources Inc; Arch became a
    # wholly-owned subsidiary. Arch Resources' own pre-merger lobbying stays
    # under its own key (cf. PARAMOUNT SKYDANCE above).
    'CONSOL ENERGY': 'CORE NATURAL RESOURCES',

    # Same organization relabeling the in-house entity it files under
    'HEALTHFIRST': 'HF MANAGEMENT',              # Healthfirst -> HF Management Services LLC (its corporate entity, a/k/a Healthfirst)
    'NAVIENT': 'NAVIENT SOLUTIONS',              # Navient Corp -> its Navient Solutions LLC operating sub (holds the bulk of the spend)

    # Duplicate keys the mechanical rules left split (suffix/DBA/typo/acronym
    # variants, or a parent's principal operating/services vehicle) that
    # research confirms are ONE organization. Distinct-operation sisters,
    # JVs, and acquired entities with their own histories stay split.
    'VERIZON': 'VERIZON COMMUNICATIONS',         # "Verizon" / "Verizon Services Corp" == Verizon Communications Inc
    'TOYOTA': 'TOYOTA MOTOR',                    # "Toyota (North America)" == Toyota Motor North America
    'DELL': 'DELL TECHNOLOGIES',                 # Dell Inc / Dell USA LP == Dell Technologies
    'ABBOTT': 'ABBOTT LABORATORIES',             # brand vs full legal name
    'ASTRAZENECA': 'ASTRAZENECA PHARMACEUTICALS',# brand vs US operating LP
    'MASTERCARD WORLDWIDE': 'MASTERCARD',        # legacy branding of the same company
    'ORACLE AMERICAN': 'ORACLE',                 # typo of "Oracle America, Inc."
    'DOW CHEMICAL COMPANY DBA DOW': 'DOW CHEMICAL',  # bare "DBA DOW" tail not stripped mechanically
    'ELECTRONIC PAYMENTS COALITION (EPC)': 'ELECTRONIC PAYMENTS COALITION',  # quoted acronym survived the self-acronym test
    'NATIONAL ASSOCIATION OF CHILDRENS HOSPITALS (NACH)-CHA': 'NATIONAL ASSOCIATION OF CHILDRENS HOSPITALS',  # N.A.C.H.-CHA == the same association
    'GENERAL ELECTRIC COMPANY (INCLUDING SUBSIDIARIES)': 'GENERAL ELECTRIC',  # scope-note parenthetical, same GE (Aerospace) filer
    'CHARTER COMMUNICATIONS OPERATING': 'CHARTER COMMUNICATIONS',  # Charter's operating LLC
    'DUKE ENERGY BUSINESS': 'DUKE ENERGY',       # Duke Energy Business Services LLC, its shared-services shell
    'FLUOR ENTERPRISES': 'FLUOR',                # Fluor's principal US operating subsidiary
    'LIBERTY MUTUAL INSURANCE': 'LIBERTY MUTUAL',# Liberty Mutual Insurance Co == Liberty Mutual Group
    'NORTHROP GRUMMAN SYSTEMS': 'NORTHROP GRUMMAN',  # Northrop Grumman Systems Corp, its principal operating entity
    # Same subsidiary (General Atomics Aeronautical Systems, Inc.) split only
    # by a hyphen; heal to the un-hyphenated GA-ASI key. GA-ASI stays its own
    # identity, distinct from the General Atomics parent key.
    'GENERAL ATOMICS - AERONAUTICAL SYSTEMS': 'GENERAL ATOMICS AERONAUTICAL SYSTEMS',

    # ── Monthly identity review, 2026-10 ──
    # Renames/rebrands of ONE continuing organization, plus same-company
    # short-form / operating-/holding-vehicle variants, verified against the
    # raw FKA/FORMERLY evidence in the filings and corporate/press records.
    # Acquisitions, spin-offs, joint ventures, generic-name collisions, and
    # distinct-operation sister subsidiaries were deliberately left split
    # (see the PR).

    # Trade associations & companies that renamed (former name -> current)
    'MOTION PICTURE ASSOCIATION OF AMERICA': 'MOTION PICTURE ASSOCIATION',  # MPAA -> MPA, Sep 2019
    'SEMPRA ENERGY': 'SEMPRA',                   # dropped "Energy", effective Jul 2021
    'MORGAN STANLEY DEAN WITTER': 'MORGAN STANLEY',  # reverted to Morgan Stanley, 2002
    # Same US operating entity (Domtar Corporation) briefly renamed Paper
    # Excellence Holdings Corporation after Paper Excellence's 2021 acquisition,
    # then back to Domtar; the raw chains "...FKA PAPER EXCELLENCE HOLDINGS
    # ... FKA DOMTAR". Paper Excellence had no separate US LDA identity, so
    # this is a rename of the continuing filer, not a fold into an acquirer.
    'PAPER EXCELLENCE': 'DOMTAR',
    # US entity EADS North America -> Airbus Group, Inc. (2014) -> Airbus
    # Americas, Inc.; the bare "AIRBUS" key comes from the pre-rename "Airbus
    # Group, Inc." filings. The global parent files as "Airbus SE" (its own key).
    'AIRBUS': 'AIRBUS AMERICAS',

    # Duplicate keys the mechanical rules left split (short form, principal
    # operating/holding/services vehicle, or parent<->flagship-subsidiary
    # relabel) that research confirms are ONE organization.
    'MASTERCARD INTERNATIONAL': 'MASTERCARD',    # Mastercard International Inc, the operating sub of Mastercard Inc (cf. MASTERCARD WORLDWIDE)
    'MICRON': 'MICRON TECHNOLOGY',               # short form of Micron Technology, Inc.
    'VALERO': 'VALERO ENERGY',                   # short form of Valero Energy Corporation
    'DISTILLED SPIRITS COUNCIL': 'DISTILLED SPIRITS COUNCIL OF THE US',  # DISCUS, same trade association
    'LUMEN TECHNOLOGIES SERVICE': 'LUMEN TECHNOLOGIES',  # Lumen Technologies Service Group LLC, its services vehicle (cf. CHARTER COMMUNICATIONS OPERATING)
    'EXELON BUSINESS': 'EXELON',                 # Exelon Business Services Co, Exelon Corp's shared-services shell (cf. DUKE ENERGY BUSINESS)
    'NEW YORK LIFE': 'NEW YORK LIFE INSURANCE',  # short form of New York Life Insurance Company
    'HCA': 'HCA HEALTHCARE',                     # HCA Healthcare, Inc. (f/k/a HCA Holdings / Hospital Corp. of America)
    'BOEHRINGER INGELHEIM PHARMACEUTICALS': 'BOEHRINGER INGELHEIM',  # BI Pharmaceuticals Inc, the US human-pharma entity (Animal Health stays split)
    'INVESCO': 'INVESCO HOLDING COMPANY (US)',   # Invesco == its US holding entity, where the bulk of the spend files
    'AMERICAN PUBLIC UNIVERSITY SYSTEM': 'APEI',  # APUS, the wholly-owned flagship of American Public Education, Inc.; filer relabeled to the parent

    # JBS USA files as "JBS USA", "JBS USA Food Company" (its legal name) and
    # "JBS USA Food Company Holdings (previously registered as JBS USA Food
    # Company)" — one continuing US filer; "JBS USA" folds to bare "JBS" via
    # the geographic-tail strip, so the legal-name key points there.
    'JBS USA FOOD': 'JBS',
}

# Any other trailing parenthetical is stripped when it clearly carries no
# identity: boilerplate scope notes, or a self-referential acronym
# ("PHARMACEUTICAL RESEARCH AND MANUFACTURERS OF AMERICA (PHRMA)") whose
# letters all appear in order in the name itself. A parenthetical that
# fails both tests — "(TEXAS)", a chapter, a distinct unit — is kept.
_TRAILING_PAREN_RE = re.compile(r'\s*\(([^)]*)\)\s*$')
_PAREN_NOISE_RE = re.compile(
    r'^(?:AND\s+(?:ITS\s+|VARIOUS\s+)?(?:SUBSIDIARIES|AFFILIATES)'
    r'|CONSOLIDATED(?:\s+(?:REPORT|REPORTING|FILING))?'
    r'|ON\s+ITS\s+OWN\s+BEHALF)$',
    re.IGNORECASE,
)

# Former-name tails written WITHOUT parentheses ("... FKA PROPERTY CASUALTY
# INSURERS", "... FORMERLY REPORTED AS FACEBOOK") — everything from the
# marker on is the old name, not the identity.
_BARE_FORMER_TAIL_RE = re.compile(
    r'(?:[\s,]+|\s*-\s*)(?:FKA|F/K/A|PKA|P/K/A|AKA|A/K/A'
    r'|FORMERLY(?:\s+(?:KNOWN|REPORTED|REGISTERED)\s+AS)?'
    r'|PREVIOUSLY\s+(?:KNOWN|REPORTED|REGISTERED)\s+AS)[\s:]+.*$',
    re.IGNORECASE,
)


def _represented_party(name: str) -> str:
    """The organization a raw client name actually stands for, as far as
    "on behalf of" pass-throughs go: the text after the LAST marker (names
    chain: "A OBO B OBO C"), minus a possessive lead-in, falling back past
    generic tails ("... ON BEHALF OF ITS CLIENTS") to the nearest part that
    names an organization. Case is preserved; unbalanced parens left by a
    parenthesized marker ("FIRM (OBO CLIENT)") are dropped."""
    parts = _ON_BEHALF_RE.split(name)
    if len(parts) == 1:
        return name
    chosen = ''
    for part in reversed(parts):
        part = part.strip(' ,;:-')
        if not part or _GENERIC_PARTY_RE.match(part):
            continue
        chosen = _POSSESSIVE_LEAD_RE.sub('', part).strip()
        break
    chosen = chosen or parts[0].strip(' ,;:-')
    # "FIRM (OBO CLIENT)" leaves "CLIENT)": drop the closing paren the
    # marker's opening paren owned, then any parens still unbalanced.
    if chosen.count(')') > chosen.count('('):
        chosen = re.sub(r'\)\s*$', '', chosen)
    if chosen.count('(') != chosen.count(')'):
        chosen = chosen.replace('(', ' ').replace(')', ' ')
    return re.sub(r'\s+', ' ', chosen).strip(' ,;:-')


def _is_self_acronym(inner: str, base: str) -> bool:
    """True when a parenthetical is an acronym of the name it follows:
    one short token whose letters occur in order within the base name."""
    token = inner.strip()
    if not token or ' ' in token:
        return False
    letters = [ch for ch in token if ch.isalpha()]
    if not (2 <= len(letters) <= 10) or len(letters) < len(token.replace('-', '').replace('.', '')):
        return False
    base_letters = [ch for ch in base if ch.isalpha()]
    it = iter(base_letters)
    return all(ch in it for ch in letters)


# Trailing legal-entity suffixes stripped iteratively (rightmost token first).
# GROUP/HOLDINGS/SERVICES are here deliberately: they mark a corporate shell
# around the same lobbying identity ("CIGNA GROUP", "AMAZON.COM SERVICES"),
# and stripping them heals splits between a parent and its filing vehicle.
_LEGAL_SUFFIXES = {
    'INC', 'INCORPORATED', 'LLC', 'LLP', 'LP', 'LTD', 'LIMITED', 'CORP',
    'CORPORATION', 'COMPANY', 'CO', 'PBC', 'PLC', 'PC', 'GMBH', 'SA', 'NV', 'AG',
    'GROUP', 'HOLDINGS', 'SERVICES',
}

# Trailing geographic qualifiers on a US subsidiary of the same brand
# ("VISA USA", "ORACLE AMERICA", "FRESENIUS MEDICAL CARE NORTH AMERICA").
# Guarded: never stripped when the preceding word makes it part of the name
# proper ("BANK OF AMERICA", "PARTNERSHIP FOR AMERICA").
_GEO_TAIL_TOKENS = {'USA', 'US', 'AMERICA'}
_GEO_GUARD_TOKENS = {'OF', 'FOR', 'IN', 'THE', 'TO'}


def _strip_geo_tail(key: str) -> str:
    tokens = key.split(' ')
    while len(tokens) > 1:
        take = 0
        if tokens[-1] in _GEO_TAIL_TOKENS:
            take = 2 if (tokens[-1] == 'AMERICA' and len(tokens) > 2 and tokens[-2] == 'NORTH') else 1
        if not take:
            break
        prev = tokens[-take - 1] if len(tokens) > take else ''
        if prev in _GEO_GUARD_TOKENS or not prev:
            break
        tokens = tokens[:-take]
    return ' '.join(tokens)


def canonical_client_key(name: str) -> str:
    """Fold raw client/registrant name variants into one stable grouping key."""
    if not name:
        return ''
    key = str(name).upper().strip()

    # Former-name parentheticals go FIRST: "LIBERTY MUTUAL (FORMERLY KNOWN
    # AS AKIN GUMP ON BEHALF OF LIBERTY MUTUAL)" must lose the whole
    # parenthetical before the on-behalf split can grab its tail.
    key = _FORMER_NAME_PAREN_RE.sub('', key).strip()

    # "X on behalf of Y" / "X OBO Y" — the represented org is what appears
    # after the phrase; a name can chain ("A on behalf of B on behalf of C"),
    # so keep whatever follows the LAST occurrence (unless that is a generic
    # "its clients"-style tail; see _represented_party).
    key = _represented_party(key)

    key = _BARE_FORMER_TAIL_RE.sub('', key).strip()
    key = _LEADING_THE_RE.sub('', key).strip()
    key = _TRAILING_AFFILIATES_RE.sub('', key)
    key = _TRAILING_SUBSIDIARIES_RE.sub('', key)

    # Trailing parenthetical: dropped only if boilerplate or a
    # self-referential acronym (see _is_self_acronym).
    m = _TRAILING_PAREN_RE.search(key)
    if m:
        base = key[:m.start()].strip()
        inner = m.group(1)
        if base and (_PAREN_NOISE_RE.match(inner.strip()) or _is_self_acronym(inner, base)):
            key = base

    # Punctuation: "&" carries real meaning ("Procter & Gamble"), so spell it
    # out rather than dropping it; other punctuation is just noise. Orphan
    # parens (their partner was stripped with a parenthetical) are noise too.
    key = key.replace('&', ' AND ')
    key = re.sub(r'''[.,'"]''', '', key)
    if ('(' in key) != (')' in key):
        key = key.replace('(', '').replace(')', '')
    key = re.sub(r'\s+', ' ', key).strip()
    if not key:
        return ''

    # Iteratively strip trailing legal-entity suffixes and geographic
    # qualifiers ("T-MOBILE USA, INC." -> "T-MOBILE USA" -> "T-MOBILE"),
    # but never strip down to nothing.
    while True:
        before = key
        tokens = key.split(' ')
        if len(tokens) > 1:
            last = tokens[-1].strip(' .,')
            if last in _LEGAL_SUFFIXES:
                candidate = ' '.join(tokens[:-1]).strip()
                if candidate:
                    key = candidate
        key = _strip_geo_tail(key)
        # A suffix strip can leave a dangling conjunction ("MERCK & CO." ->
        # "MERCK AND CO" -> "MERCK AND") — drop it.
        tokens = key.split(' ')
        while len(tokens) > 1 and tokens[-1] in ('AND', 'OF', 'THE'):
            tokens.pop()
        key = ' '.join(tokens)
        if key == before:
            break

    return _FORMER_NAME_ALIASES.get(key, key)


# ─── display_client_name ───

# Lowercased when not the first word of the name. Abbreviated former-/
# alternate-name markers read as connectives inside a name ("Paramount
# Skydance Corporation (fka Paramount Global)"), not as title words.
_SMALL_WORDS = {
    'of', 'the', 'and', 'for', 'on', 'in', 'at', 'to', 'by', 'or', 'a', 'an',
    'as', 'c/o', 'd/b/a', 'dba', 'f/k/a', 'fka', 'a/k/a', 'aka', 'p/k/a', 'pka',
}

# Always rendered uppercase regardless of position (acronyms with vowels that
# the no-vowel heuristic below can't catch, plus familiar brand acronyms).
_ACRONYM_ALLOWLIST = {
    'USA', 'US', 'LLC', 'LLP', 'AARP', 'AFLCIO', 'AFL-CIO', 'PG&E', 'IBM',
    'AT&T', 'CTIA', 'HCA', 'NACDS', 'AHIP', 'NCTA', 'SIIA', 'II', 'III', 'IV',
}

# Stylized brand spellings no casing rule can recover from the ALL-CAPS
# source ("THEGROUP DC, LLC" is the firm theGROUP DC).
_WORD_OVERRIDES = {
    'THEGROUP': 'theGROUP',
    'PHRMA': 'PhRMA',
}

# Foreign legal-form suffixes, uppercased when they END the name ("Norse
# Atlantic Airways AS", "Airbus SE") — mid-name they are ordinary words.
_TRAILING_LEGAL_FORMS = {'AS', 'ASA', 'AB', 'SA', 'AG', 'NV', 'BV', 'SE', 'SPA', 'PLC', 'KK', 'OY', 'OYJ'}

# A single letter-dot run like "U.S.A." or "D.C." — uppercase the whole token.
_DOTTED_ACRONYM_RE = re.compile(r'^[A-Za-z](\.[A-Za-z])+\.?$')
_EDGE_PUNCT_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


def _has_vowel(s: str) -> bool:
    return bool(re.search(r'[AEIOU]', s.upper()))


def _bare(token: str) -> str:
    return _EDGE_PUNCT_RE.sub('', token)


def _capitalize(seg: str) -> str:
    """Lowercase a token and capitalize its first LETTER (not its first
    character: "(NATIONAL" -> "(National", not "(national"), with the
    surname conventions that also show up in organization names:
    "MCDERMOTT" -> "McDermott", "O'BRIEN" -> "O'Brien"."""
    lead = re.match(r'^[^A-Za-z0-9]*', seg).group(0)
    rest = seg[len(lead):].lower()
    if re.fullmatch(r'\d+(?:st|nd|rd|th)\W*', rest):
        return lead + rest                       # ordinals: "21st"
    if re.match(r'\d+[a-z]{1,2}\W*$', rest):
        return lead + rest.upper()               # "3M", "5G"
    if not rest[:1].isalpha():
        return lead + rest
    rest = rest[:1].upper() + rest[1:]
    if re.match(r'Mc[a-z]{2}', rest):
        rest = 'Mc' + rest[2].upper() + rest[3:]
    elif re.match(r"[A-Z]'[a-z]", rest):
        rest = rest[:2] + rest[2].upper() + rest[3:]
    return lead + rest


def _render_segment(seg: str, acronyms: frozenset = frozenset()) -> str:
    """One hyphen/slash/ampersand-free piece of a word."""
    bare = _bare(seg)
    if not bare:
        return seg
    upper = bare.upper()
    if upper in _WORD_OVERRIDES:
        return seg.replace(bare, _WORD_OVERRIDES[upper])
    if upper in _ACRONYM_ALLOWLIST or upper in acronyms:
        return seg.upper()
    # Pragmatic heuristic: short, all-consonant tokens are almost always
    # acronyms/initialisms ("PBC", "NV", "GMBH"), not ordinary words.
    if len(bare) <= 4 and bare.isalpha() and not _has_vowel(bare):
        return seg.upper()
    return _capitalize(seg)


def _render_word(word: str, is_first: bool, acronyms: frozenset = frozenset()) -> str:
    if not word:
        return word

    bare = _bare(word)
    if not bare:
        return word

    # Check the dotted-acronym pattern against the bare token too, so a
    # parenthesized/punctuated acronym like "(N.A.C.H.)" is recognized (not
    # just a bare "U.S.A."). The whole original token — punctuation and
    # all — is already uppercase in the source data, so upper-casing it is
    # a safe no-op for the wrapping punctuation.
    if _DOTTED_ACRONYM_RE.match(word) or _DOTTED_ACRONYM_RE.match(bare):
        return word.upper()

    upper = bare.upper()
    if upper in _ACRONYM_ALLOWLIST or upper in acronyms:
        return word.upper()

    if not is_first and bare.lower() in _SMALL_WORDS:
        return word.lower()

    # Ampersand-joined initials ("D&P", "S&P", "P&I") are one acronym;
    # longer ampersand compounds render piece by piece.
    if '&' in bare:
        pieces = bare.split('&')
        if all(p.isalpha() and len(p) <= 2 for p in pieces):
            return word.upper()
        return '&'.join(_render_word(p, True, acronyms) for p in word.split('&'))

    # Slash- and hyphen-joined compounds ("Klein/Johnson Group", "CTIA-The
    # Wireless Association", "Wal-Mart") get each segment re-checked against
    # the allowlist/no-vowel heuristic rather than a blind capitalize, and
    # never get the small-word lowering (a compound segment isn't a title
    # word in the "of/the/and" sense — it's part of a brand name).
    if '/' in word:
        return '/'.join(_render_word(p, True, acronyms) for p in word.split('/'))
    if '-' in word:
        return '-'.join(_render_segment(seg, acronyms) for seg in word.split('-'))
    return _render_segment(word, acronyms)


def _abbreviates(token: str, words: list) -> bool:
    """True when token reads as an abbreviation of `words`: same first
    letter as the first word, and every letter found in order in the words'
    letters ("SIFMA" for "Securities Industry and Financial Markets
    Association", "WADA" for "World Anti-Doping Association", "NFIB" for
    "National Federation of Independent Business"). A token that is itself
    one of the words ("TEXAS ... (TEXAS)") is a name, not an abbreviation."""
    bares = [_bare(w).upper() for w in words]
    letters = [ch for b in bares for ch in b if ch.isalpha()]
    token = token.upper()
    if not letters or token[0] != letters[0] or token in bares:
        return False
    it = iter(letters)
    return all(ch in it for ch in token)


_PAREN_TOKEN_RE = re.compile(r'[(\[]["\']?([A-Za-z]{2,8})["\']?[)\],;:.]+')


def _context_acronyms(words: list) -> frozenset:
    """Acronyms the name itself spells out: a one-token parenthetical that
    abbreviates the words before it ("... Authorities (CIFA)"), or a token
    followed by a parenthesized expansion ("NFIB (National Federation ...)",
    "UPS (United Parcel Service)")."""
    found = set()
    for i, w in enumerate(words):
        m = _PAREN_TOKEN_RE.fullmatch(w)
        # Two-letter parentheticals are state/country codes ("(WA)", "(UK)").
        if m and (len(m.group(1)) == 2 or (i >= 2 and _abbreviates(m.group(1), words[:i]))):
            found.add(m.group(1).upper())
        if i + 1 < len(words) and words[i + 1].startswith('(') and re.fullmatch(r'[A-Za-z]{2,8},?', w):
            expansion = []
            for x in words[i + 1:]:
                expansion.append(x)
                if ')' in x:
                    break
            if len(expansion) >= 2 and _abbreviates(_bare(w), expansion):
                found.add(_bare(w).upper())
    return frozenset(found)


def _title_case_name(name: str) -> str:
    words = name.split(' ')
    acronyms = _context_acronyms(words)
    out = []
    for i, w in enumerate(words):
        if w == '&':
            out.append('&')
            continue
        # A word after a standalone separator starts a new title phrase
        # ("NCTA - The Internet & Television Association").
        is_first = i == 0 or words[i - 1] in ('-', '–', '—') or words[i - 1].endswith(':')
        if i == len(words) - 1 and i > 0 and _bare(w).upper() in _TRAILING_LEGAL_FORMS:
            out.append(w.upper())
            continue
        # The last word is capitalized even when it is a small word.
        out.append(_render_word(w, is_first=is_first or i == len(words) - 1, acronyms=acronyms))
    return ' '.join(out)


def display_client_name(raw_names: list) -> str:
    """Pick a representative raw name from a canonical group and render it
    in readable display casing.

    raw_names may contain repeats (one entry per occurrence/filing) so the
    most common spelling wins; ties prefer the shorter raw string. An "on
    behalf of" pass-through ("FIRM ON BEHALF OF CLIENT") counts as the
    represented organization's name — the same reading canonical_client_key
    groups it under — so a client is never displayed under its lobbying
    firm's name.
    """
    names = []
    for n in raw_names or []:
        if not n:
            continue
        n = re.sub(r'\s+', ' ', str(n).strip())
        if _ON_BEHALF_RE.search(n):
            n = _represented_party(_FORMER_NAME_PAREN_RE.sub('', n).strip())
        if n:
            names.append(n)
    if not names:
        return ''

    from collections import Counter
    counts = Counter(names)
    best = max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    return _title_case_name(best[0])


# ─── display_person_name ───

# Courtesy titles the LDA name fields carry ("mr", "ms", ...) add nothing to
# a list of people and are often wrong; other honorifics are kept.
_COURTESY_TITLES = {'MR', 'MRS', 'MS', 'MISS', 'MX'}
_HONORIFICS = {'DR': 'Dr.', 'HON': 'Hon.', 'REV': 'Rev.', 'SEN': 'Sen.', 'REP': 'Rep.',
               'AMB': 'Amb.', 'GEN': 'Gen.', 'ADM': 'Adm.', 'COL': 'Col.', 'PROF': 'Prof.'}
_NAME_SUFFIXES = {'JR': 'Jr.', 'SR': 'Sr.', 'II': 'II', 'III': 'III', 'IV': 'IV',
                  'MD': 'MD', 'PHD': 'PhD', 'ESQ': 'Esq.'}


def display_person_name(name: str) -> str:
    """Readable casing for a lobbyist name as the LDA API assembles it
    ("mr JOHN FRANCIS WILLIAMS iii" -> "John Francis Williams III"). A name
    that already carries its own mixed casing is left alone."""
    tokens = (name or '').split()
    if not tokens:
        return ''
    if tokens[0].rstrip('.').upper() in _COURTESY_TITLES and len(tokens) > 1:
        tokens = tokens[1:]
    core = [t for i, t in enumerate(tokens)
            if t.rstrip('.,').upper() not in _NAME_SUFFIXES
            and not (i == 0 and t.rstrip('.').upper() in _HONORIFICS)]
    if any(c.islower() for t in core for c in t):
        return ' '.join(tokens)
    out = []
    for i, tok in enumerate(tokens):
        key = tok.rstrip('.,').upper()
        if i == 0 and key in _HONORIFICS and len(tokens) > 1:
            out.append(_HONORIFICS[key])
        elif i > 0 and key in _NAME_SUFFIXES:
            out.append(_NAME_SUFFIXES[key] + (',' if tok.endswith(',') else ''))
        elif len(_bare(tok)) == 1:
            out.append(tok.upper())              # middle initial
        else:
            out.append('-'.join(_capitalize(seg) for seg in tok.split('-')))
    return ' '.join(out)
