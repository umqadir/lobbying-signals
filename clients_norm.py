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

_ON_BEHALF_RE = re.compile(r'\s+ON\s+BEHALF\s+OF\s+|\s+OBO\s+', re.IGNORECASE)

# Parentheticals that carry a former/alternate name, not part of the identity.
# The closing paren is optional: filers truncate long names mid-parenthetical
# ("META PLATFORMS INC (FORMERLY FACEBOOK INC AND VARIOUS SUBSIDIARIES"),
# which would otherwise mint a whole separate identity.
# No \b after the marker: filers omit the space ("(FKARAYTHEON ...)"),
# and inside a parenthetical the prefix alone is unambiguous.
_FORMER_NAME_PAREN_RE = re.compile(
    r'\(\s*(?:FORMERLY|FKA|F/K/A|D/B/A|DBA)[^)]*(?:\)|$)',
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
    r'\s+(?:FKA|F/K/A|FORMERLY(?:\s+(?:KNOWN\s+AS|REPORTED\s+AS))?)\s+.*$',
    re.IGNORECASE,
)


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
    # so keep whatever follows the LAST occurrence.
    parts = _ON_BEHALF_RE.split(key)
    if len(parts) > 1:
        key = parts[-1].strip()

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

# Lowercased when not the first word of the name.
_SMALL_WORDS = {
    'of', 'the', 'and', 'for', 'on', 'in', 'at', 'to', 'by', 'or', 'a', 'an',
    'd/b/a',
}

# Always rendered uppercase regardless of position (acronyms with vowels that
# the no-vowel heuristic below can't catch, plus familiar brand acronyms).
_ACRONYM_ALLOWLIST = {
    'USA', 'US', 'LLC', 'LLP', 'AARP', 'AFLCIO', 'AFL-CIO', 'PG&E', 'IBM',
    'AT&T', 'CTIA', 'HCA', 'NACDS', 'AHIP',
}

# A single letter-dot run like "U.S.A." or "D.C." — uppercase the whole token.
_DOTTED_ACRONYM_RE = re.compile(r'^[A-Za-z](\.[A-Za-z])+\.?$')


def _has_vowel(s: str) -> bool:
    return bool(re.search(r'[AEIOU]', s.upper()))


def _render_word(word: str, is_first: bool) -> str:
    if not word:
        return word

    bare = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", '', word)
    if not bare:
        return word

    # Check the dotted-acronym pattern against the bare token too, so a
    # parenthesized/punctuated acronym like "(N.A.C.H.)" is recognized (not
    # just a bare "U.S.A."). The whole original token — punctuation and
    # all — is already uppercase in the source data, so upper-casing it is
    # a safe no-op for the wrapping punctuation.
    if _DOTTED_ACRONYM_RE.match(word) or _DOTTED_ACRONYM_RE.match(bare):
        return word.upper()

    bare_upper = bare.upper()
    if bare_upper in _ACRONYM_ALLOWLIST:
        return word.upper()

    # Pragmatic heuristic: short, all-consonant tokens are almost always
    # acronyms/initialisms ("PBC", "NV", "GMBH"), not ordinary words.
    if len(bare) <= 4 and bare.isalpha() and not _has_vowel(bare):
        return word.upper()

    if not is_first and bare.lower() in _SMALL_WORDS:
        return word.lower()

    # Default: title-case. Hyphenated compounds ("CTIA-The Wireless
    # Association", "Wal-Mart") get each segment re-checked against the
    # allowlist/no-vowel heuristic rather than a blind capitalize, and never
    # get the small-word lowering (a hyphen segment isn't a title word in the
    # "of/the/and" sense — it's part of a compound brand name).
    if '-' in word:
        return '-'.join(_render_hyphen_segment(seg) for seg in word.split('-'))
    return word[:1].upper() + word[1:].lower()


def _render_hyphen_segment(seg: str) -> str:
    if not seg:
        return seg
    bare = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", '', seg)
    if not bare:
        return seg
    bare_upper = bare.upper()
    if bare_upper in _ACRONYM_ALLOWLIST:
        return seg.upper()
    if len(bare) <= 4 and bare.isalpha() and not _has_vowel(bare):
        return seg.upper()
    return seg[:1].upper() + seg[1:].lower()


def _title_case_name(name: str) -> str:
    words = name.split(' ')
    out = []
    for i, w in enumerate(words):
        if w == '&':
            out.append('&')
            continue
        out.append(_render_word(w, is_first=(i == 0)))
    return ' '.join(out)


def display_client_name(raw_names: list) -> str:
    """Pick a representative raw name from a canonical group and render it
    in readable display casing.

    raw_names may contain repeats (one entry per occurrence/filing) so the
    most common spelling wins; ties prefer the shorter raw string.
    """
    names = [n for n in (raw_names or []) if n]
    if not names:
        return ''

    from collections import Counter
    counts = Counter(names)
    best = max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    chosen = best[0]

    # Collapse repeated internal whitespace before casing.
    chosen = re.sub(r'\s+', ' ', chosen.strip())
    return _title_case_name(chosen)
