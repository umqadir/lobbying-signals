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

# ─── canonical_client_key ───

_ON_BEHALF_RE = re.compile(r'\s+ON\s+BEHALF\s+OF\s+|\s+OBO\s+', re.IGNORECASE)

# Parentheticals that carry a former/alternate name, not part of the identity.
_FORMER_NAME_PAREN_RE = re.compile(
    r'\(\s*(?:FORMERLY|FKA|F/K/A|D/B/A|DBA)\b[^)]*\)',
    re.IGNORECASE,
)

_LEADING_THE_RE = re.compile(r'^THE\s+', re.IGNORECASE)

_TRAILING_AFFILIATES_RE = re.compile(
    r'\s+AND\s+(?:ITS\s+)?AFFILIATES\s*$', re.IGNORECASE
)
_TRAILING_SUBSIDIARIES_RE = re.compile(
    r'\s+AND\s+SUBSIDIARIES\s*$', re.IGNORECASE
)

# Trailing legal-entity suffixes stripped iteratively (rightmost token first).
_LEGAL_SUFFIXES = {
    'INC', 'INCORPORATED', 'LLC', 'LLP', 'LP', 'LTD', 'LIMITED', 'CORP',
    'CORPORATION', 'COMPANY', 'CO', 'PBC', 'PLC', 'PC', 'GMBH', 'SA', 'NV', 'AG',
}


def canonical_client_key(name: str) -> str:
    """Fold raw client/registrant name variants into one stable grouping key."""
    if not name:
        return ''
    key = str(name).upper().strip()

    # "X on behalf of Y" / "X OBO Y" — the represented org is what appears
    # after the phrase; a name can chain ("A on behalf of B on behalf of C"),
    # so keep whatever follows the LAST occurrence.
    parts = _ON_BEHALF_RE.split(key)
    if len(parts) > 1:
        key = parts[-1].strip()

    key = _FORMER_NAME_PAREN_RE.sub('', key).strip()
    key = _LEADING_THE_RE.sub('', key).strip()
    key = _TRAILING_AFFILIATES_RE.sub('', key)
    key = _TRAILING_SUBSIDIARIES_RE.sub('', key)

    # Punctuation: "&" carries real meaning ("Procter & Gamble"), so spell it
    # out rather than dropping it; other punctuation is just noise.
    key = key.replace('&', ' AND ')
    key = re.sub(r'''[.,'"]''', '', key)
    key = re.sub(r'\s+', ' ', key).strip()
    if not key:
        return ''

    # Iteratively strip trailing legal-entity suffixes ("SOME CORP INC" ->
    # "SOME CORP" -> "SOME"), but never strip down to nothing.
    while True:
        tokens = key.split(' ')
        if len(tokens) <= 1:
            break
        last = tokens[-1].strip(' .,')
        if last in _LEGAL_SUFFIXES:
            candidate = ' '.join(tokens[:-1]).strip()
            if not candidate:
                break
            key = candidate
        else:
            break

    return key


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
