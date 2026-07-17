"""Compute trends and generate alerts from lobbying data."""

import calendar
import json
import re
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from db import get_db, query_to_dicts
from clients_norm import canonical_client_key, display_client_name, EXCLUDED_CLIENT_KEYS

RULES_PATH = Path("rules/topic_rules.json")

# Structural noise, not lobbying targets: every filing that mentions lobbying
# "the Congress" tags both chambers, so these show up as the top "agency"
# movers on every window regardless of what's actually happening. Executive
# Office of the President / White House stay in, since executive-branch
# orientation is a real, meaningful signal.
EXCLUDED_ENTITY_CHAMBERS = {
    "HOUSE OF REPRESENTATIVES",
    "U.S. HOUSE OF REPRESENTATIVES",
    "SENATE",
    "U.S. SENATE",
    "CONGRESS",
    "U.S. CONGRESS",
}

COARSE_TOPIC_LABELS = {
    "trade": "Trade",
    "healthcare": "Healthcare",
    "technology": "Technology",
    "energy_environment": "Energy and Environment",
    "defense_security": "Defense and Security",
    "agriculture_food": "Agriculture and Food",
    "labor_immigration": "Labor and Immigration",
    "finance_tax": "Finance and Tax",
    "transportation": "Transportation",
    "education_social": "Education and Social Policy",
    "housing_urban": "Housing and Urban Development",
    "government_budget": "Government and Budget",
    "industry_business": "Industry and Business",
    "other": "Other",
    "unknown": "Unknown",
}


def _humanize_slug(value: str) -> str:
    return " ".join(part for part in value.replace("_", " ").split()).title()


def _load_topic_labels() -> dict[str, str]:
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    labels = {}
    for topic in data.get("topics", []):
        topic_id = topic.get("id")
        label = topic.get("label")
        if topic_id and label:
            labels[topic_id] = label
    return labels


TOPIC_LABELS = _load_topic_labels()

LEGISLATION_NOISE_EXACT = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "to",
    "for",
    "of",
    "extensions",
    "and extensions",
    "related agencies appropriations",
}


@dataclass
class _Agg:
    counts: Counter
    income: defaultdict
    income_seen_filing_ids: defaultdict
    # Client tracking, keyed by canonical_client_key (folds name variants):
    client_keys: defaultdict          # tag -> set(canonical_key) — for client_count
    client_income: defaultdict        # tag -> Counter(canonical_key -> income)
    client_income_seen: defaultdict   # tag -> canonical_key -> set(filing_id), for income dedupe
    client_raw_names: defaultdict     # tag -> canonical_key -> Counter(raw_name), for display_client_name


def normalize_tag(value: str) -> str:
    """Normalize extracted tag spacing."""
    if value is None:
        return ''
    return ' '.join(str(value).split()).strip()


# Bill numbers are only unique within a Congress ("H.R. 1" has been the Tax
# Cuts and Jobs Act, the For the People Act, the Lower Energy Costs Act, and
# the One Big Beautiful Bill Act in four consecutive Congresses), so a bare
# number tag conflates unrelated laws across years. Numbers are therefore
# scoped to a Congress, and well-known scoped numbers / public-law numbers are
# folded into the act's name so one law isn't split across number, name, and
# P.L. variants.

# Named acts detected inside a tag string take priority over any number in the
# same string ("H.R. 1 - Lower Energy Costs Act" is about that act, whatever
# the filing year). Patterns are matched case-insensitively and tried in order,
# so specific names precede short-form truncations that resolve unambiguously.
KNOWN_ACT_PATTERNS = [
    # 119th Congress
    (re.compile(r'\b(?:one,?\s+big,?\s+)?beautiful\s+(?:bill\s+)?act\b', re.I), 'One Big Beautiful Bill Act'),
    # 118th
    (re.compile(r'\bfiscal responsibility act\b', re.I), 'Fiscal Responsibility Act of 2023'),
    (re.compile(r'\blower energy costs act\b', re.I), 'Lower Energy Costs Act'),
    # 117th landmark laws
    (re.compile(r'\binfrastructure investment and jobs act\b', re.I), 'Infrastructure Investment and Jobs Act'),
    (re.compile(r'\bbipartisan infrastructure (?:law|deal|framework)\b', re.I), 'Infrastructure Investment and Jobs Act'),
    (re.compile(r'\binflation reduction act\b', re.I), 'Inflation Reduction Act'),
    (re.compile(r'\bbuild back better\b', re.I), 'Build Back Better Act'),
    (re.compile(r'\bchips\b[\s+&/.,-]{0,4}(?:and\s+|for\s+america\s+)?science\b', re.I), 'CHIPS and Science Act'),
    (re.compile(r'\bchips act\b', re.I), 'CHIPS and Science Act'),
    (re.compile(r'\bamerican rescue plan\b', re.I), 'American Rescue Plan Act'),
    (re.compile(r'\bfamilies first coronavirus\b', re.I), 'Families First Coronavirus Response Act'),
    (re.compile(r'\bheroes act\b', re.I), 'Heroes Act'),
    (re.compile(r'\b(?:u\.?s\.?|united states) innovation and competition act\b', re.I), 'U.S. Innovation and Competition Act'),
    (re.compile(r'\bamerica competes act\b', re.I), 'America COMPETES Act'),
    (re.compile(r'\bfor the people act\b', re.I), 'For the People Act'),
    # 116th
    (re.compile(r'\bcoronavirus aid,? (?:relief|response),? and economic security\b', re.I), 'CARES Act'),
    (re.compile(r'\bcares act\b', re.I), 'CARES Act'),
    (re.compile(r'^economic security act$', re.I), 'CARES Act'),  # common truncation
    (re.compile(r'\baffordable care act\b', re.I), 'Affordable Care Act'),
    (re.compile(r'\bpatient protection and affordable care\b', re.I), 'Affordable Care Act'),
    # 115th
    (re.compile(r'\btax cuts (?:and|&) jobs act\b', re.I), 'Tax Cuts and Jobs Act'),
    # Unambiguous short-form truncations (only one federal law each matches).
    # Unanchored so year-suffixed variants fold too ("Science Act of 2022").
    (re.compile(r'\bscience act\b', re.I), 'CHIPS and Science Act'),
    (re.compile(r'\binnovation and competition act of 2021\b', re.I), 'U.S. Innovation and Competition Act'),
    (re.compile(r'^competition act of 2021$', re.I), 'U.S. Innovation and Competition Act'),
    (re.compile(r'^competes act$', re.I), 'America COMPETES Act'),
]

# Generic truncations that map to several different laws depending on context
# ("Jobs Act" = Tax Cuts and Jobs / IIJA / American Jobs / American Innovation
# and Jobs; "America Act" = INVEST in America / Made in America / CHIPS for
# America / …). They carry no identity on their own and, in the data, almost
# always co-occur with the real bill number or full name on the same activity,
# so they are dropped as noise rather than misattributed.
LEGISLATION_DROP_FRAGMENTS = {
    'jobs act', 'america act', 'competes', 'act', 'bill', 'legislation',
    'appropriations', 'appropriations act', 'reconciliation', 'reconciliation act',
    'tax act', 'energy act', 'health act', 'defense act', 'budget act',
    # Tail of "Full-Year Continuing Appropriations and Extensions Act, 20XX"
    # (and successors) — co-occurs with the real H.R./P.L. number on the same
    # activity, so the fragment carries no identity of its own.
    'extensions act',
}

# Congress-scoped bill numbers and public-law numbers that are the same law as
# a named act above. Kept deliberately to landmark, unambiguous laws; recurring
# titles (appropriations, NDAA) stay as scoped numbers since their bare names
# are year-ambiguous. NOTE: H.R. 5376 (117th) is intentionally absent — it was
# the vehicle for BOTH Build Back Better and the Inflation Reduction Act, so the
# bare number is genuinely ambiguous; only the enacted P.L. 117-169 maps to IRA.
LEGISLATION_ALIASES = {
    # One Big Beautiful Bill Act (119th)
    'H.R. 1 (119th Congress)': 'One Big Beautiful Bill Act',
    'P.L. 119-21': 'One Big Beautiful Bill Act',
    # Lower Energy Costs Act (118th) / For the People Act (117th) — H.R. 1 reuse
    'H.R. 1 (118th Congress)': 'Lower Energy Costs Act',
    'H.R. 1 (117th Congress)': 'For the People Act',
    'H.R. 1 (116th Congress)': 'For the People Act',  # original 2019 version, same title
    # Fiscal Responsibility Act of 2023 (118th)
    'H.R. 3746 (118th Congress)': 'Fiscal Responsibility Act of 2023',
    'P.L. 118-5': 'Fiscal Responsibility Act of 2023',
    # Inflation Reduction Act (117th) — enacted P.L. only
    'P.L. 117-169': 'Inflation Reduction Act',
    # Infrastructure Investment and Jobs Act (117th)
    'H.R. 3684 (117th Congress)': 'Infrastructure Investment and Jobs Act',
    'P.L. 117-58': 'Infrastructure Investment and Jobs Act',
    # CHIPS and Science Act (117th)
    'H.R. 4346 (117th Congress)': 'CHIPS and Science Act',
    'P.L. 117-167': 'CHIPS and Science Act',
    # American Rescue Plan Act (117th)
    'H.R. 1319 (117th Congress)': 'American Rescue Plan Act',
    'P.L. 117-2': 'American Rescue Plan Act',
    # U.S. Innovation and Competition Act / America COMPETES (117th)
    'S. 1260 (117th Congress)': 'U.S. Innovation and Competition Act',
    'H.R. 4521 (117th Congress)': 'America COMPETES Act',
    # CARES Act & Families First (116th)
    'H.R. 748 (116th Congress)': 'CARES Act',
    'P.L. 116-136': 'CARES Act',
    'H.R. 6201 (116th Congress)': 'Families First Coronavirus Response Act',
    'P.L. 116-127': 'Families First Coronavirus Response Act',
    'H.R. 6800 (116th Congress)': 'Heroes Act',
    # Tax Cuts and Jobs Act (115th)
    'H.R. 1 (115th Congress)': 'Tax Cuts and Jobs Act',
    'P.L. 115-97': 'Tax Cuts and Jobs Act',
}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def _congress_for_year(year: int) -> int:
    # The Nth Congress convenes in odd year 1789 + 2(N-1).
    return (year - 1789) // 2 + 1


def normalize_legislation(value: str, year: int | None = None) -> str:
    """Normalize legislation tags to stable, non-colliding identities.

    year: the filing's report year, used to scope bare bill numbers to a
    Congress. An explicit "of 20XX" / "(NNNth Congress)" qualifier in the tag
    wins over the filing year, and a recognized act NAME wins over any number.

    Known limitation: a truly bare number with no name/year/Congress qualifier
    and no accompanying name tag is scoped to the filing's Congress. A
    retrospective reference (a 2026 filing citing "H.R. 3684" to mean the 2021
    IIJA) therefore misbinds to a wrong-Congress number. Measured footprint is
    ~40/month scattered across a few laws — well below the volume any bill needs
    to surface on the dashboard — so it is left unresolved rather than fixed with
    a fuzzy description parser that would risk mislinks on the common (correct)
    case. The monthly alias audit flags any such number if it ever accumulates.
    """
    tag = normalize_tag(value)
    if not tag:
        return ''

    tag = re.sub(r'^[`"\']+|[`"\']+$', '', tag).strip()
    tag = re.sub(r'\s+', ' ', tag).strip(' ,;:.()[]{}')
    tag = re.sub(r'^(?:issues?\s+related\s+to\s+)(?:the\s+)?', '', tag, flags=re.IGNORECASE).strip()
    tag = re.sub(r'^(?:related\s+to\s+)(?:the\s+)?', '', tag, flags=re.IGNORECASE).strip()
    # Strip a leading article so "the Equality Act" folds with "Equality Act"
    # rather than being dropped by the bare-article noise rule below.
    tag = re.sub(r'^(?:the|an?)\s+', '', tag, flags=re.IGNORECASE).strip()
    tag = re.sub(r'^year\s+(continuing appropriations and extensions)$', r'\1', tag, flags=re.IGNORECASE)
    tag = tag.strip(' ,;:.()[]{}')
    if not tag:
        return ''

    # 1) A recognized act NAME in the tag beats any number in the same tag.
    for pattern, canonical in KNOWN_ACT_PATTERNS:
        if pattern.search(tag):
            return canonical

    # 2) Generic truncation fragments carry no identity — drop as noise.
    # A trailing year qualifier doesn't rescue a fragment ("Extensions Act of
    # 2025", "Appropriations Act, 2025" are as identity-free as the bare
    # forms), so strip it before the lookup.
    fragment_probe = tag.lower().strip(' .,;:')
    fragment_probe_no_year = re.sub(
        r'[,\s]+(?:of\s+)?(?:19|20)\d\d$', '', fragment_probe
    ).strip(' .,;:')
    if fragment_probe in LEGISLATION_DROP_FRAGMENTS or \
            fragment_probe_no_year in LEGISLATION_DROP_FRAGMENTS:
        return ''

    # Explicit qualifiers override the filing year for number scoping.
    scope_year = year
    year_qual = re.search(r'\bof\s+(19|20)(\d\d)\b', tag)
    if year_qual:
        scope_year = int(year_qual.group(1) + year_qual.group(2))
    congress_qual = re.search(r'\b(\d{2,3})(?:st|nd|rd|th)\s+Congress\b', tag, flags=re.IGNORECASE)
    congress = int(congress_qual.group(1)) if congress_qual else (
        _congress_for_year(scope_year) if scope_year else None)

    def scoped(prefix: str, number: str) -> str:
        base = f"{prefix} {number}"
        if congress:
            base = f"{base} ({_ordinal(congress)} Congress)"
        return LEGISLATION_ALIASES.get(base, base)

    hr_any = re.search(r'\bH\.?\s*R\.?\s*(\d{1,5})\b', tag, flags=re.IGNORECASE)
    if hr_any:
        return scoped("H.R.", hr_any.group(1))

    senate_any = re.search(r'(?<![A-Za-z])S\.?\s*(\d{1,5})\b', tag, flags=re.IGNORECASE)
    if senate_any:
        return scoped("S.", senate_any.group(1))

    pl_any = re.search(r'\bP\.?\s*L\.?\s*(\d{1,3}-\d{1,5})\b', tag, flags=re.IGNORECASE)
    if pl_any:
        pl = f"P.L. {pl_any.group(1)}"
        return LEGISLATION_ALIASES.get(pl, pl)

    compact = re.sub(r'[^A-Za-z0-9-]', '', tag).upper()

    hr_match = re.match(r'^HR(\d+)$', compact)
    if hr_match:
        return scoped("H.R.", hr_match.group(1))

    senate_match = re.match(r'^S(\d+)$', compact)
    if senate_match:
        return scoped("S.", senate_match.group(1))

    pl_match = re.match(r'^PL(\d+-\d+)$', compact)
    if pl_match:
        pl = f"P.L. {pl_match.group(1)}"
        return LEGISLATION_ALIASES.get(pl, pl)

    lower = tag.lower()
    if lower in LEGISLATION_NOISE_EXACT:
        return ''
    if lower.startswith('and '):  # leftover conjunction fragment ("and extensions")
        return ''
    if lower.endswith(' and extensions') and 'appropriations' not in lower:
        return ''

    words = re.findall(r"[A-Za-z0-9']+", tag)
    if len(words) == 1 and len(words[0]) <= 2:
        return ''

    # Unify fiscal-year spellings so "FY27 NDAA" and "FY2027 NDAA" are one tag
    tag = re.sub(r'\bFY\s*(\d{2})\b(?!\d)', lambda m: f"FY20{m.group(1)}", tag, flags=re.IGNORECASE)
    tag = re.sub(r'\bFY\s*(20\d\d)\b', r'FY\1', tag, flags=re.IGNORECASE)

    return tag


def display_topic(value: str) -> str:
    tag = normalize_tag(value)
    if not tag:
        return ""
    if tag in TOPIC_LABELS:
        return TOPIC_LABELS[tag]
    if tag.startswith("general_"):
        return f"General: {_humanize_slug(tag.removeprefix('general_'))}"
    return _humanize_slug(tag)


def is_general_topic(value: str) -> bool:
    return normalize_tag(value).startswith("general_")


def display_domain(value: str) -> str:
    tag = normalize_tag(value)
    if not tag:
        return ""
    return COARSE_TOPIC_LABELS.get(tag, _humanize_slug(tag))


def get_extraction_counts(year: int, quarter: int, through: str = None) -> dict:
    """Get counts of topics, entities, legislation from extractions for one
    report quarter — the filings labeled (year, quarter), regardless of when
    they were submitted. `through` optionally caps by filing_date (inclusive)
    for quarter-to-date legs, so a partial quarter can be compared like-for-
    like against the same point in the prior year's filing cycle."""
    with get_db() as conn:
        params: list = [year, quarter]
        date_filter = "AND f.year = ? AND f.quarter = ?"
        if through:
            # filing_date is a full ISO timestamp; compare at day granularity
            # so the through-date's filings are included on both legs.
            date_filter += " AND substr(f.filing_date, 1, 10) <= ?"
            params.append(through)

        sql = f'''
            SELECT f.id as filing_id, f.sopr_filing_id as filing_uuid, f.year as filing_year,
                   e.topics, e.entities, e.legislation,
                   f.filing_date, c.name as client_name, r.name as registrant_name, f.income
            FROM activity_extractions_rules e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            JOIN clients c ON f.client_id = c.id
            JOIN registrants r ON f.registrant_id = r.id
            WHERE e.coarse_topic IS NOT NULL
            AND f.is_current = 1
            {date_filter}
        '''
        rows = query_to_dicts(conn, sql, tuple(params))

    def make_agg() -> _Agg:
        return _Agg(
            counts=Counter(),
            income=defaultdict(float),
            income_seen_filing_ids=defaultdict(set),
            client_keys=defaultdict(set),
            client_income=defaultdict(Counter),
            client_income_seen=defaultdict(lambda: defaultdict(set)),
            client_raw_names=defaultdict(lambda: defaultdict(Counter)),
        )

    # Count occurrences (counts are "mentions"/activity-level, not filings)
    topics = make_agg()
    entities = make_agg()
    legislation = make_agg()
    topic_examples = defaultdict(dict)
    entity_examples = defaultdict(dict)
    legislation_examples = defaultdict(dict)

    def add_example(bucket: defaultdict, tag: str, filing_id: int, filing_date: str, client_name: str, registrant_name: str, income: float, filing_uuid: str = None):
        if not tag or not filing_id:
            return
        if filing_id in bucket[tag]:
            return
        bucket[tag][filing_id] = {
            'id': filing_id,
            'uuid': filing_uuid,  # official LDA filing UUID — links to the Senate record
            'date': filing_date,
            'client': client_name,
            'registrant': registrant_name,
            'income': income or 0
        }

    def record_client(agg: _Agg, tag: str, client_name: str, filing_id: int, income: float):
        """Track which organization sits behind a tag mention, keyed by its
        canonical (name-variant-folded) identity, and accumulate income once
        per (tag, client, filing) so a filing with several matching
        activities doesn't multiply its own income."""
        if not tag or not client_name:
            return
        key = canonical_client_key(client_name)
        if not key:
            return
        agg.client_keys[tag].add(key)
        agg.client_raw_names[tag][key][client_name] += 1
        if filing_id is not None:
            seen = agg.client_income_seen[tag][key]
            if filing_id not in seen:
                agg.client_income[tag][key] += income or 0
                seen.add(filing_id)

    for row in rows:
        filing_id = row.get('filing_id')
        filing_uuid = row.get('filing_uuid')
        client = row.get('client_name')
        registrant = row.get('registrant_name')
        income = row.get('income') or 0
        filing_date = row.get('filing_date')

        # Verified-erroneous filers (see clients_norm.EXCLUDED_CLIENT_KEYS):
        # their activities and non-dollar "income" would pollute every tag
        # they brush against.
        if client and canonical_client_key(client) in EXCLUDED_CLIENT_KEYS:
            continue

        for topic in json.loads(row['topics'] or '[]'):
            if is_general_topic(topic):
                continue
            topic = display_topic(topic)
            if not topic:
                continue
            topics.counts[topic] += 1
            if client:
                record_client(topics, topic, client, filing_id, income)
            if filing_id and filing_id not in topics.income_seen_filing_ids[topic]:
                topics.income[topic] += income
                topics.income_seen_filing_ids[topic].add(filing_id)
            add_example(topic_examples, topic, filing_id, filing_date, client, registrant, income, filing_uuid)

        for entity in json.loads(row['entities'] or '[]'):
            entity = normalize_tag(entity)
            if not entity:
                continue
            if entity.upper() in EXCLUDED_ENTITY_CHAMBERS:
                # Structural noise — every filing tags the chamber(s) it
                # lobbied, so "Congress"/"Senate"/"House" swamp every window
                # without telling readers anything about what's moving.
                continue
            entities.counts[entity] += 1
            if client:
                record_client(entities, entity, client, filing_id, income)
            if filing_id and filing_id not in entities.income_seen_filing_ids[entity]:
                entities.income[entity] += income
                entities.income_seen_filing_ids[entity].add(filing_id)
            add_example(entity_examples, entity, filing_id, filing_date, client, registrant, income, filing_uuid)

        seen_leg = set()
        for leg in json.loads(row['legislation'] or '[]'):
            leg = normalize_legislation(leg, row.get('filing_year'))
            if not leg or leg in seen_leg:
                # Aliases of one law (number, name, P.L.) collapse to a single
                # canonical tag; count it once per activity.
                continue
            seen_leg.add(leg)
            legislation.counts[leg] += 1
            if client:
                record_client(legislation, leg, client, filing_id, income)
            if filing_id and filing_id not in legislation.income_seen_filing_ids[leg]:
                legislation.income[leg] += income
                legislation.income_seen_filing_ids[leg].add(filing_id)
            add_example(legislation_examples, leg, filing_id, filing_date, client, registrant, income, filing_uuid)

    def top_clients(agg: _Agg, limit: int = 10) -> dict:
        """Top clients per tag, ranked by summed filing income (each filing
        counted once per tag-client, not once per mention), rendered as
        display names with name-variant fragmentation folded."""
        result = {}
        for tag, income_counter in agg.client_income.items():
            names = []
            for key, _ in income_counter.most_common(limit):
                raw_counter = agg.client_raw_names[tag].get(key) or Counter()
                names.append(display_client_name(list(raw_counter.elements())))
            result[tag] = names
        return result

    def client_counts_by_tag(agg: _Agg) -> dict:
        return {tag: len(keys) for tag, keys in agg.client_keys.items()}

    def finalize_examples(buckets: defaultdict, limit: int = 8) -> dict:
        result = {}
        for tag, filings in buckets.items():
            ordered = sorted(
                filings.values(),
                key=lambda x: (x.get('date') or '', x.get('income') or 0),
                reverse=True
            )
            result[tag] = ordered[:limit]
        return result

    return {
        'topics': topics.counts,
        'entities': entities.counts,
        'legislation': legislation.counts,
        'topic_clients': top_clients(topics),
        'topic_income': dict(topics.income),
        'topic_client_count': client_counts_by_tag(topics),
        'entity_clients': top_clients(entities),
        'entity_income': dict(entities.income),
        'entity_client_count': client_counts_by_tag(entities),
        'legislation_clients': top_clients(legislation),
        'legislation_income': dict(legislation.income),
        'legislation_client_count': client_counts_by_tag(legislation),
        'topic_examples': finalize_examples(topic_examples),
        'entity_examples': finalize_examples(entity_examples),
        'legislation_examples': finalize_examples(legislation_examples),
        'total_rows': len(rows)
    }


def _anchor_today(conn) -> datetime:
    """The dashboard's "as of" date: the newest filing in the DB, not the wall
    clock. Anchoring windows and quarter math here (rather than to
    date('now')) keeps them consistent with stats.date_range.end and avoids
    silently emptying the dashboard if ingestion stalls."""
    max_date = conn.execute(
        'SELECT MAX(filing_date) FROM filings WHERE filing_date IS NOT NULL'
    ).fetchone()[0]
    if max_date:
        # +1 day so lexicographic BETWEEN covers the whole as-of day
        return datetime.strptime(max_date[:10], '%Y-%m-%d') + timedelta(days=1)
    return datetime.now()


def _quarter_end_date(year: int, quarter: int) -> datetime:
    month = quarter * 3
    last_day = calendar.monthrange(year, month)[1]
    return datetime(year, month, last_day)


def _prev_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


def _latest_complete_quarter(anchor: datetime) -> tuple[int, int]:
    """A report quarter counts as "complete" once the anchor date is more
    than 40 days past its calendar end. The statutory LDA deadline is the
    20th of the following month, but late filers keep trickling in for
    another few weeks after that, so the newest quarter is still filling in
    right up to (and a bit past) its deadline."""
    year = anchor.year
    quarter = (anchor.month - 1) // 3 + 1
    while True:
        end = _quarter_end_date(year, quarter)
        if anchor > end + timedelta(days=40):
            return year, quarter
        year, quarter = _prev_quarter(year, quarter)


def _quarters_back_list(end_year: int, end_quarter: int, count: int) -> list[tuple[int, int]]:
    """The `count` report quarters ending at (end_year, end_quarter),
    oldest first."""
    out = []
    y, q = end_year, end_quarter
    for _ in range(count):
        out.append((y, q))
        y, q = _prev_quarter(y, q)
    out.reverse()
    return out


def _next_quarter(year: int, quarter: int) -> tuple[int, int]:
    if quarter == 4:
        return year + 1, 1
    return year, quarter + 1


# A quarter-to-date frame with fewer current filings than this is flagged
# thin_data so the UI can note "early in the filing cycle". A full quarter
# runs ~20k filings; the weeks right after a quarter closes see a few hundred
# to a couple thousand early filers.
THIN_DATA_MIN_FILINGS = 2000


def _frame_specs(conn) -> dict:
    """The two comparison frames every dashboard view shares. Both are
    year-over-year and report-quarter based (never rolling filing-date
    windows, which measure filing-clerk timing in a quarterly regime):

    - quarter: latest COMPLETE report quarter vs the same quarter a year
      earlier. The headline frame.
    - qtd: the current PARTIAL report quarter so far (filings labeled with
      that quarter, posted on or before the data-through date) vs the same
      quarter last year capped at the same point in its filing cycle
      (data-through minus 365 days — same month/day). Like-for-like even
      mid-cycle, because both legs are equally early.

    Each leg is a (year, quarter, through) triple consumable by
    get_extraction_counts.
    """
    anchor = _anchor_today(conn)
    through = anchor - timedelta(days=1)  # _anchor_today is max filing_date + 1
    cy, cq = _latest_complete_quarter(anchor)
    ny, nq = _next_quarter(cy, cq)
    through_str = through.strftime('%Y-%m-%d')
    baseline_through_str = (through - timedelta(days=365)).strftime('%Y-%m-%d')
    return {
        'quarter': {
            'key': 'quarter',
            'label': f'Q{cq} {cy}',
            'baseline_label': f'Q{cq} {cy - 1}',
            'complete': True,
            'current': (cy, cq, None),
            'baseline': (cy - 1, cq, None),
        },
        'qtd': {
            'key': 'qtd',
            'label': f'Q{nq} {ny} so far',
            'baseline_label': f'same point in Q{nq} {ny - 1}',
            'complete': False,
            'through': through_str,
            'current': (ny, nq, through_str),
            'baseline': (ny - 1, nq, baseline_through_str),
        },
    }


def _filing_count(conn, year: int, quarter: int, through: str = None) -> int:
    # is_current = 1: a superseded original shouldn't inflate the filing
    # count for a period whose real latest-word filing is its amendment.
    if through:
        # Day-granularity comparison: filing_date is a full ISO timestamp.
        return conn.execute(
            'SELECT COUNT(*) FROM filings WHERE year = ? AND quarter = ? AND is_current = 1 AND substr(filing_date, 1, 10) <= ?',
            (year, quarter, through),
        ).fetchone()[0]
    return conn.execute(
        'SELECT COUNT(*) FROM filings WHERE year = ? AND quarter = ? AND is_current = 1',
        (year, quarter),
    ).fetchone()[0]


def compute_trends() -> dict:
    """Compute year-over-year trend data for the two report-quarter frames."""
    with get_db() as conn:
        specs = _frame_specs(conn)
        frame_meta = {}
        for frame_key, spec in specs.items():
            frame_meta[frame_key] = {
                'key': spec['key'],
                'label': spec['label'],
                'baseline_label': spec['baseline_label'],
                'complete': spec['complete'],
                'current_filings': _filing_count(conn, *spec['current']),
                'baseline_filings': _filing_count(conn, *spec['baseline']),
            }
            if not spec['complete']:
                frame_meta[frame_key]['through'] = spec['through']
                frame_meta[frame_key]['thin_data'] = (
                    frame_meta[frame_key]['current_filings'] < THIN_DATA_MIN_FILINGS
                )

    datasets = {
        frame_key: {
            'current': get_extraction_counts(*spec['current']),
            'baseline': get_extraction_counts(*spec['baseline']),
        }
        for frame_key, spec in specs.items()
    }

    def confidence_label(count: int, baseline_count: int, share_delta_pp: float) -> str:
        # Wave-1 thresholds required agreement across two baselines (prior
        # period + year-ago). With one YoY baseline the volume gates stay and
        # the delta gate uses the yoy leg's old threshold. abs() because big
        # decliners are now first-class stories, not noise.
        if count >= 50 and baseline_count >= 50 and abs(share_delta_pp) >= 0.35:
            return 'high'
        if count >= 25 and abs(share_delta_pp) >= 0.15:
            return 'medium'
        return 'low'

    # Maps a calc_change `key` to the get_extraction_counts dict key holding
    # per-tag distinct-organization counts (irregular suffixes: "topics" ->
    # "topic_client_count", not "topics_client_count").
    client_count_keys = {
        'topics': 'topic_client_count',
        'entities': 'entity_client_count',
        'legislation': 'legislation_client_count',
    }

    def calc_change(
        current: dict,
        baseline: dict,
        key: str,
        min_count: int = 1,
        max_items: int = 500
    ) -> tuple[list, dict]:
        current_counts = current[key]
        baseline_counts = baseline[key]
        current_total = current.get('total_rows', 0)
        baseline_total = baseline.get('total_rows', 0)

        client_count_key = client_count_keys.get(key)
        current_client_counts = current.get(client_count_key, {}) or {} if client_count_key else {}
        baseline_client_counts = baseline.get(client_count_key, {}) or {} if client_count_key else {}

        results = []
        dropped_min = 0
        for item, count in current_counts.items():
            if count < min_count:
                dropped_min += 1
                continue

            baseline_count = baseline_counts.get(item, 0)
            current_share = (count / current_total * 100) if current_total else 0
            baseline_share = (baseline_count / baseline_total * 100) if baseline_total else 0
            share_delta_pp = current_share - baseline_share
            ratio = (count / baseline_count) if baseline_count > 0 else None
            confidence = confidence_label(count, baseline_count, share_delta_pp)

            # Change + a small scale term so a big tag edges out a tiny one at
            # equal delta. Ranking uses |score| so collapses surface alongside
            # surges — a topic losing half its share is as much a story as one
            # doubling.
            score = share_delta_pp + min(count / 2000, 1) * 0.1

            results.append({
                'name': item,
                'count': count,
                'baseline_count': baseline_count,
                'client_count': current_client_counts.get(item, 0),
                'baseline_client_count': baseline_client_counts.get(item, 0),
                'current_share_pct': round(current_share, 3),
                'baseline_share_pct': round(baseline_share, 3),
                'share_delta_pp': round(share_delta_pp, 3),
                'ratio': round(ratio, 3) if ratio is not None else None,
                'score': round(score, 4),
                'confidence': confidence,
            })

        sorted_results = sorted(
            results,
            key=lambda x: (-abs(x['score']), -x['count'], x['name'])
        )
        capped_results = sorted_results[:max_items]
        meta = {
            'unique_current': len(current_counts),
            'min_count': min_count,
            'dropped_min_count': dropped_min,
            'ranked': len(sorted_results),
            'exported': len(capped_results),
        }
        return capped_results, meta

    def keep_for(top_list: list[dict], mapping: dict) -> dict:
        if not mapping:
            return {}
        names = {x.get('name') for x in (top_list or []) if x.get('name')}
        return {k: mapping[k] for k in names if k in mapping}

    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'frames': frame_meta,
        'topics': {},
        'entities': {},
        'legislation': {},
        'topic_clients': {},
        'topic_income': {},
        'topic_examples': {},
        'entity_clients': {},
        'entity_income': {},
        'entity_examples': {},
        'legislation_clients': {},
        'legislation_income': {},
        'legislation_examples': {},
        'selection_meta': {},
    }

    for frame_key, parts in datasets.items():
        current = parts['current']
        baseline = parts['baseline']

        topics, topics_meta = calc_change(current, baseline, 'topics', min_count=1, max_items=500)
        entities, entities_meta = calc_change(current, baseline, 'entities', min_count=1, max_items=500)
        legislation, legislation_meta = calc_change(current, baseline, 'legislation', min_count=5, max_items=500)

        result['frames'][frame_key]['current_total_mentions'] = current.get('total_rows', 0)
        result['frames'][frame_key]['baseline_total_mentions'] = baseline.get('total_rows', 0)

        result['topics'][frame_key] = topics
        result['entities'][frame_key] = entities
        result['legislation'][frame_key] = legislation
        result['selection_meta'][frame_key] = {
            'topics': topics_meta,
            'entities': entities_meta,
            'legislation': legislation_meta,
        }

        result['topic_clients'][frame_key] = keep_for(topics, current.get('topic_clients', {}))
        result['topic_income'][frame_key] = keep_for(topics, current.get('topic_income', {}))
        result['topic_examples'][frame_key] = keep_for(topics, current.get('topic_examples', {}))

        result['entity_clients'][frame_key] = keep_for(entities, current.get('entity_clients', {}))
        result['entity_income'][frame_key] = keep_for(entities, current.get('entity_income', {}))
        result['entity_examples'][frame_key] = keep_for(entities, current.get('entity_examples', {}))

        result['legislation_clients'][frame_key] = keep_for(legislation, current.get('legislation_clients', {}))
        result['legislation_income'][frame_key] = keep_for(legislation, current.get('legislation_income', {}))
        result['legislation_examples'][frame_key] = keep_for(legislation, current.get('legislation_examples', {}))

    return result


def generate_alerts(
    trends: dict,
    frame: str = 'quarter',
    min_share_delta_pp: float = 0.25,
    min_count: int = 25
) -> list:
    """Generate alerts from the complete-quarter frame's YoY share changes."""
    alerts = []
    categories = [
        ('topics', 'topic', 'topic_clients', 'topic_income'),
        ('entities', 'entity', 'entity_clients', 'entity_income'),
        ('legislation', 'legislation', 'legislation_clients', 'legislation_income'),
    ]
    baseline_label = trends.get('frames', {}).get(frame, {}).get('baseline_label', 'same quarter last year')

    for trend_key, category, client_key, income_key in categories:
        for item in trends.get(trend_key, {}).get(frame, []):
            count = item.get('count', 0)
            delta = item.get('share_delta_pp', 0) or 0
            if count < min_count:
                continue
            if abs(delta) < min_share_delta_pp:
                continue

            clients = trends.get(client_key, {}).get(frame, {}).get(item['name'], [])[:5]
            income = trends.get(income_key, {}).get(frame, {}).get(item['name'], 0)
            alerts.append({
                'type': 'signal',
                'category': category,
                'name': item['name'],
                'current_count': count,
                'baseline_count': item.get('baseline_count', 0),
                'share_delta_pp': round(delta, 3),
                'signal_confidence': item.get('confidence', 'low'),
                'top_clients': clients,
                'total_income': income,
                'headline': generate_headline(item, category, baseline_label),
            })

    confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
    alerts.sort(
        key=lambda x: (
            confidence_rank.get(x.get('signal_confidence', 'low'), 2),
            -abs(x.get('share_delta_pp', 0)),
            -x.get('current_count', 0),
        )
    )
    return alerts[:20]


def generate_headline(item: dict, category: str, baseline_label: str) -> str:
    """Generate a readable headline for a YoY quarter alert."""
    name = item['name']
    delta = item.get('share_delta_pp', 0) or 0
    direction = 'up' if delta >= 0 else 'down'
    magnitude = abs(delta)
    count = item.get('count', 0)

    if category == 'topic':
        return f"'{name}' share {direction} {magnitude:.2f} pp vs {baseline_label} ({count} mentions)"
    if category == 'entity':
        return f"{name} attention {direction} {magnitude:.2f} pp vs {baseline_label} ({count} mentions)"
    return f"{name} share {direction} {magnitude:.2f} pp vs {baseline_label} ({count} mentions)"


def get_stats() -> dict:
    """Get summary statistics."""
    with get_db() as conn:
        total_filings = conn.execute('SELECT COUNT(*) FROM filings').fetchone()[0]
        total_activities = conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0]
        total_extracted = conn.execute('SELECT COUNT(*) FROM activity_extractions_rules').fetchone()[0]

        date_range = conn.execute('''
            SELECT MIN(filing_date), MAX(filing_date) FROM filings
            WHERE filing_date IS NOT NULL
        ''').fetchone()

        # Get quarter breakdown (is_current=1 so a superseded original isn't
        # double-counted alongside the amendment/termination that replaced it)
        quarters = conn.execute('''
            SELECT year, quarter, COUNT(*) as cnt, SUM(income) as total_income
            FROM filings
            WHERE is_current = 1
            GROUP BY year, quarter
            ORDER BY year DESC, quarter DESC
            LIMIT 8
        ''').fetchall()

        # Latest COMPLETE report quarter vs the same quarter a year ago — the
        # headline "$X.XB, +Y% vs Qn last year" figure. Deliberately separate
        # from the `quarters` breakdown above, which includes the still-filling
        # newest quarter.
        anchor = _anchor_today(conn)
        complete_year, complete_quarter = _latest_complete_quarter(anchor)
        baseline_year = complete_year - 1
        # The partial quarter is the one FOLLOWING the latest complete quarter
        # by the calendar — not max(year, quarter) in the data, which a
        # handful of legitimate early termination reports for future periods
        # would skew (e.g. a 3T filed in April).
        partial_year, partial_quarter = _next_quarter(complete_year, complete_quarter)
        # Per-filing rows so supersede (is_current) and the verified-erroneous
        # filer exclusions apply — these figures must agree with the
        # organization-mover quarter totals in clients.json.
        filing_rows = query_to_dicts(
            conn,
            '''
            SELECT f.year, f.quarter, f.income, c.name AS client_name
            FROM filings f JOIN clients c ON f.client_id = c.id
            WHERE f.is_current = 1
              AND ((f.year = ? AND f.quarter = ?) OR (f.year = ? AND f.quarter = ?)
                   OR (f.year = ? AND f.quarter = ?))
            ''',
            (complete_year, complete_quarter, baseline_year, complete_quarter,
             partial_year, partial_quarter),
        )

    sums = defaultdict(float)
    counts = defaultdict(int)
    for r in filing_rows:
        if canonical_client_key(r.get('client_name') or '') in EXCLUDED_CLIENT_KEYS:
            continue
        yq = (r['year'], r['quarter'])
        sums[yq] += r.get('income') or 0
        counts[yq] += 1
    current_income = sums[(complete_year, complete_quarter)]
    baseline_income = sums[(baseline_year, complete_quarter)]
    current_filings = counts[(complete_year, complete_quarter)]
    baseline_filings = counts[(baseline_year, complete_quarter)]

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_filings': total_filings,
        'total_activities': total_activities,
        'total_extracted': total_extracted,
        'extracted_pct': round(total_extracted / total_activities * 100, 1) if total_activities > 0 else 0,
        'extraction_source': 'deterministic_rules',
        'date_range': {
            'start': date_range[0] if date_range else None,
            'end': date_range[1] if date_range else None
        },
        'quarters': [
            {'year': q[0], 'quarter': q[1], 'filings': q[2], 'income': q[3]}
            for q in quarters
        ],
        'latest_complete_quarter': {
            'year': complete_year,
            'quarter': complete_quarter,
            'label': f'Q{complete_quarter} {complete_year}',
            'income': current_income,
            'filings': current_filings,
            'yoy_income': baseline_income,
            'yoy_filings': baseline_filings,
            'income_change_pct': _pct_change(current_income, baseline_income),
        },
        'current_partial_quarter': {
            'year': partial_year,
            'quarter': partial_quarter,
            'label': f'Q{partial_quarter} {partial_year}',
            'filings': counts[(partial_year, partial_quarter)],
        },
    }


def get_recent_filings(limit: int = 300) -> list:
    """Get recent filings with extractions."""
    with get_db() as conn:
        sql = '''
            WITH recent AS (
                SELECT id, sopr_filing_id, filing_date, income, year, quarter, client_id, registrant_id
                FROM filings
                WHERE filing_date IS NOT NULL AND is_current = 1
                ORDER BY filing_date DESC
                LIMIT ?
            )
            SELECT
                f.id, f.sopr_filing_id AS filing_uuid, f.filing_date, f.income, f.year, f.quarter,
                c.name as client_name, r.name as registrant_name,
                e.coarse_topic AS domain, e.topics, e.entities, e.legislation
            FROM recent f
            JOIN clients c ON f.client_id = c.id
            JOIN registrants r ON f.registrant_id = r.id
            LEFT JOIN activities a ON a.filing_id = f.id
            LEFT JOIN activity_extractions_rules e ON e.activity_id = a.id
        '''
        rows = query_to_dicts(conn, sql, (limit,))

    by_filing = {}
    for row in rows:
        fid = row['id']
        client_name = row.get('client_name')
        if client_name and canonical_client_key(client_name) in EXCLUDED_CLIENT_KEYS:
            continue
        if fid not in by_filing:
            by_filing[fid] = {
                'id': fid,
                'uuid': row.get('filing_uuid'),
                'date': row['filing_date'],
                'client': row['client_name'],
                'registrant': row['registrant_name'],
                'income': row['income'],
                'year': row['year'],
                'quarter': row['quarter'],
                'domain_counts': Counter(),
                'topics': Counter(),
                'general_topics': Counter(),
                'entities': Counter(),
                'legislation': Counter(),
            }

        rec = by_filing[fid]
        domain = display_domain(row.get('domain'))
        if domain:
            rec['domain_counts'][domain] += 1

        for t in json.loads(row.get('topics') or '[]'):
            topic = display_topic(t)
            if topic:
                if is_general_topic(t):
                    rec['general_topics'][topic] += 1
                else:
                    rec['topics'][topic] += 1
        for e in json.loads(row.get('entities') or '[]'):
            entity = normalize_tag(e)
            if entity:
                rec['entities'][entity] += 1
        for l in json.loads(row.get('legislation') or '[]'):
            legislation = normalize_legislation(l, row.get('year'))
            if legislation:
                rec['legislation'][legislation] += 1

    filings = []
    for rec in by_filing.values():
        domain = rec['domain_counts'].most_common(1)[0][0] if rec['domain_counts'] else None
        topic_counts = rec['topics'] if rec['topics'] else rec['general_topics']
        filings.append({
            'id': rec['id'],
            'uuid': rec.get('uuid'),
            'date': rec['date'],
            'client': rec['client'],
            'registrant': rec['registrant'],
            'income': rec['income'],
            'year': rec['year'],
            'quarter': rec['quarter'],
            'domain': domain,
            'domains': [d for d, _ in rec['domain_counts'].most_common(3)],
            'topics': [t for t, _ in topic_counts.most_common(12)],
            'entities': [e for e, _ in rec['entities'].most_common(12)],
            'legislation': [l for l, _ in rec['legislation'].most_common(12)],
        })

    filings.sort(key=lambda f: f.get('date') or '', reverse=True)
    return filings[:limit]



def get_time_series(quarters_back: int = 20, topics_to_track: set[str] | None = None,
                    track_names: dict | None = None) -> dict:
    """Get report-quarter time series data for charts.

    track_names: optional {'entities': set, 'legislation': set}
    of names to emit quarterly series for (in addition to topics).
    """
    def percentile(values: list[int], q: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        idx = int(round((len(ordered) - 1) * q))
        idx = max(0, min(idx, len(ordered) - 1))
        return int(ordered[idx])

    def pct_change(current: float, baseline: float) -> float | None:
        if baseline == 0:
            return None
        return round((current - baseline) / baseline * 100, 1)

    with get_db() as conn:
        quarter_rows = query_to_dicts(
            conn,
            '''
            WITH quarter_base AS (
                SELECT
                    year,
                    quarter,
                    (year * 4 + quarter) AS q_index,
                    COUNT(*) AS filings,
                    SUM(income) AS income
                FROM filings
                WHERE year IS NOT NULL
                  AND quarter BETWEEN 1 AND 4
                  AND is_current = 1
                GROUP BY year, quarter
            ),
            latest AS (
                SELECT *
                FROM quarter_base
                ORDER BY q_index DESC
                LIMIT ?
            )
            SELECT year, quarter, q_index, filings, income
            FROM latest
            ORDER BY q_index
            ''',
            (quarters_back,),
        )

    if not quarter_rows:
        return {
            'quarters': [],
            'top_topics': [],
            'tracked_topics': [],
            'topic_series': {},
            'entity_series': {},
            'legislation_series': {},
            'context': {
                'period_count': 0,
                'reporting_note': 'Each point represents a report quarter from filing metadata.',
            },
        }

    min_q_index = int(quarter_rows[0]['q_index'])
    max_q_index = int(quarter_rows[-1]['q_index'])

    with get_db() as conn:
        topic_rows = query_to_dicts(
            conn,
            '''
            SELECT
                (f.year * 4 + f.quarter) AS q_index,
                f.year AS filing_year,
                e.topics, e.entities, e.legislation
            FROM activity_extractions_rules e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            WHERE f.year IS NOT NULL
              AND f.quarter BETWEEN 1 AND 4
              AND f.is_current = 1
              AND (f.year * 4 + f.quarter) BETWEEN ? AND ?
            ''',
            (min_q_index, max_q_index),
        )

    topic_by_quarter = defaultdict(Counter)
    entity_by_quarter = defaultdict(Counter)
    legislation_by_quarter = defaultdict(Counter)
    for row in topic_rows:
        q_index = int(row.get('q_index') or 0)
        if not q_index:
            continue
        for topic in json.loads(row.get('topics') or '[]'):
            if is_general_topic(topic):
                continue
            topic = display_topic(topic)
            if topic:
                topic_by_quarter[q_index][topic] += 1
        for entity in json.loads(row.get('entities') or '[]'):
            entity = normalize_tag(entity)
            if entity:
                entity_by_quarter[q_index][entity] += 1
        seen_leg = set()
        for leg in json.loads(row.get('legislation') or '[]'):
            leg = normalize_legislation(leg, row.get('filing_year'))
            if leg and leg not in seen_leg:
                seen_leg.add(leg)
                legislation_by_quarter[q_index][leg] += 1

    all_topics = Counter()
    for quarter_topics in topic_by_quarter.values():
        all_topics.update(quarter_topics)
    top_topics = [t[0] for t in all_topics.most_common(10)]

    tracked_topics = []
    if topics_to_track:
        for topic in sorted(topics_to_track):
            if topic in all_topics:
                tracked_topics.append(topic)
    for topic in top_topics:
        if topic not in tracked_topics:
            tracked_topics.append(topic)

    quarters = []
    q_indexes = []
    for row in quarter_rows:
        year = int(row.get('year') or 0)
        quarter = int(row.get('quarter') or 0)
        q_indexes.append(int(row.get('q_index') or 0))
        quarters.append({
            'year': year,
            'quarter': quarter,
            'label': f'{year} Q{quarter}',
            'short': f'{str(year)[-2:]}Q{quarter}',
            'filings': int(row.get('filings') or 0),
            'income': float(row.get('income') or 0),
        })

    topic_series = {}
    for topic in tracked_topics:
        topic_series[topic] = [topic_by_quarter[q].get(topic, 0) for q in q_indexes]

    # Quarterly series for the other categories, bounded to the names the
    # trends windows actually surface (so every drawer gets history without
    # exporting the long tail).
    def build_series(by_quarter: defaultdict, names: set[str] | None) -> dict:
        out = {}
        for name in (names or set()):
            series = [by_quarter[q].get(name, 0) for q in q_indexes]
            if any(series):
                out[name] = series
        return out

    track = track_names or {}
    entity_series = build_series(entity_by_quarter, track.get('entities'))
    legislation_series = build_series(legislation_by_quarter, track.get('legislation'))

    quarterly_filings = [int(q.get('filings') or 0) for q in quarters]
    top_quarters = sorted(quarters, key=lambda x: x.get('filings', 0), reverse=True)[:3]

    latest_4q_filings = sum(quarterly_filings[-4:]) if quarterly_filings else 0
    prior_4q_filings = sum(quarterly_filings[-8:-4]) if len(quarterly_filings) >= 8 else 0

    return {
        'quarters': quarters,
        'top_topics': top_topics,
        'tracked_topics': tracked_topics,
        'topic_series': topic_series,
        'entity_series': entity_series,
        'legislation_series': legislation_series,
        'context': {
            'period_count': len(quarters),
            'start_label': quarters[0]['label'],
            'end_label': quarters[-1]['label'],
            'reporting_note': 'Each point is a report quarter from filing metadata (year/quarter), not filing submission date.',
            'quarterly_filings_min': min(quarterly_filings) if quarterly_filings else 0,
            'quarterly_filings_median': percentile(quarterly_filings, 0.5),
            'quarterly_filings_p90': percentile(quarterly_filings, 0.9),
            'quarterly_filings_max': max(quarterly_filings) if quarterly_filings else 0,
            'latest_4q_filings': latest_4q_filings,
            'prior_4q_filings': prior_4q_filings if len(quarterly_filings) >= 8 else None,
            'latest_4q_change_pct': (
                pct_change(latest_4q_filings, prior_4q_filings)
                if len(quarterly_filings) >= 8 else None
            ),
            'top_report_quarters': [
                {'label': q['label'], 'filings': q['filings']}
                for q in top_quarters
            ],
        },
    }


def compute_client_movers(quarters_back: int = 20) -> dict:
    """Organization-level spend movers: which clients ramped reported
    lobbying income up or down in dollars, under the same two YoY frames as
    the tag views — the latest COMPLETE report quarter vs the same quarter a
    year earlier ('quarter'), and the current partial quarter so far vs the
    same point in last year's filing cycle ('qtd'). This is the story tag
    mention-counts can't tell — mentions are activity-level and don't say who
    is behind them or how much they spent.

    Name-variant fragmentation ("ANTHROPIC" / "ANTHROPIC PBC" / an on-behalf-
    of filer) is folded via clients_norm.canonical_client_key before ranking.
    """
    with get_db() as conn:
        specs = _frame_specs(conn)
        cy, cq, _ = specs['quarter']['current']
        ny, nq, qtd_through = specs['qtd']['current']
        _, _, qtd_baseline_through = specs['qtd']['baseline']

        window_quarters = _quarters_back_list(cy, cq, quarters_back)

        def pre_baseline_quarters(year: int, quarter: int) -> list[tuple[int, int]]:
            """The four report quarters immediately before (year, quarter)."""
            out = []
            py, pq = year, quarter
            for _ in range(4):
                py, pq = _prev_quarter(py, pq)
                out.append((py, pq))
            return out

        # New-entrant screens look at the four FULL quarters before each
        # frame's baseline quarter, so "new" means genuinely new, not
        # cyclical.
        frame_pre_baseline = {
            'quarter': {y * 4 + q for y, q in pre_baseline_quarters(cy - 1, cq)},
            'qtd': {y * 4 + q for y, q in pre_baseline_quarters(ny - 1, nq)},
        }

        needed = set(window_quarters) | {
            (cy, cq), (cy - 1, cq), (ny, nq), (ny - 1, nq)
        }
        needed |= set(pre_baseline_quarters(cy - 1, cq)) | set(pre_baseline_quarters(ny - 1, nq))
        q_indexes_needed = [y * 4 + q for y, q in needed]
        min_index, max_index = min(q_indexes_needed), max(q_indexes_needed)

        rows = query_to_dicts(
            conn,
            '''
            SELECT f.sopr_filing_id AS filing_uuid, f.year, f.quarter,
                   f.income, f.filing_date,
                   c.name AS client_name, r.name AS registrant_name
            FROM filings f
            JOIN clients c ON f.client_id = c.id
            JOIN registrants r ON f.registrant_id = r.id
            WHERE (f.year * 4 + f.quarter) BETWEEN ? AND ?
              AND f.is_current = 1
            ''',
            (min_index, max_index),
        )

        # Per-frame topic profiles, from each frame's CURRENT leg.
        topic_rows_by_frame = {}
        for frame_key, spec in specs.items():
            y, q, through = spec['current']
            sql = '''
                SELECT c.name AS client_name, e.topics
                FROM activity_extractions_rules e
                JOIN activities a ON e.activity_id = a.id
                JOIN filings f ON a.filing_id = f.id
                JOIN clients c ON f.client_id = c.id
                WHERE f.year = ? AND f.quarter = ? AND f.is_current = 1
            '''
            params: list = [y, q]
            if through:
                sql += ' AND substr(f.filing_date, 1, 10) <= ?'
                params.append(through)
            topic_rows_by_frame[frame_key] = query_to_dicts(conn, sql, tuple(params))

    FRAME_KEYS = ('quarter', 'qtd')
    current_q_index = {'quarter': cy * 4 + cq, 'qtd': ny * 4 + nq}
    baseline_q_index = {'quarter': (cy - 1) * 4 + cq, 'qtd': (ny - 1) * 4 + nq}

    income_by_key_q = defaultdict(lambda: defaultdict(float))
    filings_by_key_q = defaultdict(lambda: defaultdict(int))
    raw_names = defaultdict(Counter)  # key -> Counter(raw client name)
    # Per-frame leg aggregates. The quarter frame's legs are full-quarter
    # sums; the qtd frame's legs are additionally capped by filing_date so
    # both years are cut at the same point in the filing cycle.
    leg_income = {fk: {'current': defaultdict(float), 'baseline': defaultdict(float)} for fk in FRAME_KEYS}
    leg_filings = {fk: {'current': defaultdict(int), 'baseline': defaultdict(int)} for fk in FRAME_KEYS}
    registrants_current = {fk: defaultdict(Counter) for fk in FRAME_KEYS}
    examples_current = {fk: defaultdict(list) for fk in FRAME_KEYS}

    def leg_membership(q_index: int, filing_date: str | None) -> list[tuple[str, str]]:
        legs = []
        if q_index == current_q_index['quarter']:
            legs.append(('quarter', 'current'))
        if q_index == baseline_q_index['quarter']:
            legs.append(('quarter', 'baseline'))
        # qtd legs need a verifiable filing_date at or before the cutoff
        if q_index == current_q_index['qtd'] and filing_date and filing_date[:10] <= qtd_through:
            legs.append(('qtd', 'current'))
        if q_index == baseline_q_index['qtd'] and filing_date and filing_date[:10] <= qtd_baseline_through:
            legs.append(('qtd', 'baseline'))
        return legs

    for row in rows:
        client_name = row.get('client_name')
        if not client_name:
            continue
        key = canonical_client_key(client_name)
        if not key or key in EXCLUDED_CLIENT_KEYS:
            continue
        q_index = int(row['year']) * 4 + int(row['quarter'])
        income = row.get('income') or 0
        filing_date = row.get('filing_date')

        income_by_key_q[key][q_index] += income
        filings_by_key_q[key][q_index] += 1
        raw_names[key][client_name] += 1

        for frame_key, leg in leg_membership(q_index, filing_date):
            leg_income[frame_key][leg][key] += income
            leg_filings[frame_key][leg][key] += 1
            if leg == 'current':
                registrant_name = row.get('registrant_name')
                if registrant_name:
                    registrants_current[frame_key][key][registrant_name] += 1
                examples_current[frame_key][key].append({
                    'uuid': row.get('filing_uuid'),
                    'date': filing_date,
                    'client': client_name,
                    'registrant': registrant_name,
                    'income': income,
                })

    topics_by_frame_key = {fk: defaultdict(Counter) for fk in FRAME_KEYS}
    for frame_key, topic_rows in topic_rows_by_frame.items():
        for row in topic_rows:
            client_name = row.get('client_name')
            if not client_name:
                continue
            key = canonical_client_key(client_name)
            if not key:
                continue
            for topic in json.loads(row.get('topics') or '[]'):
                if is_general_topic(topic):
                    continue
                label = display_topic(topic)
                if label:
                    topics_by_frame_key[frame_key][key][label] += 1

    FLOOR = 100_000
    NEW_ENTRANT_FLOOR = 250_000

    def build_frame(frame_key: str) -> dict:
        cur_income = leg_income[frame_key]['current']
        base_income = leg_income[frame_key]['baseline']
        cur_filings = leg_filings[frame_key]['current']
        base_filings = leg_filings[frame_key]['baseline']
        pre_baseline_indexes = frame_pre_baseline[frame_key]

        # Frame-level totals across ALL clients (not just exported movers) —
        # for the headline "$1.63B, +10.7% vs Q1 2025" framing.
        quarter_totals = {
            'current_income': round(sum(cur_income.values()), 2),
            'baseline_income': round(sum(base_income.values()), 2),
            'change_pct': _pct_change(sum(cur_income.values()), sum(base_income.values())),
            'current_filings': sum(cur_filings.values()),
            'baseline_filings': sum(base_filings.values()),
            'current_clients': len(cur_filings),
            'baseline_clients': len(base_filings),
        }

        # Per-client current / baseline / pre-baseline aggregates.
        candidates = {}
        for key in set(cur_income) | set(base_income):
            current_income = cur_income.get(key, 0.0)
            baseline_income = base_income.get(key, 0.0)
            if current_income == 0 and baseline_income == 0:
                continue
            pre_baseline_sum = sum(income_by_key_q[key].get(qi, 0.0) for qi in pre_baseline_indexes)
            candidates[key] = {
                'current': current_income,
                'baseline': baseline_income,
                'pre_baseline_sum': pre_baseline_sum,
                'filings_current': cur_filings.get(key, 0),
                'filings_baseline': base_filings.get(key, 0),
            }

        def build_mover(key: str, m: dict) -> dict:
            current_income = m['current']
            baseline_income = m['baseline']
            series = [round(income_by_key_q[key].get(y * 4 + q, 0.0), 2) for y, q in window_quarters]
            registrant_names = [n for n, _ in registrants_current[frame_key][key].most_common(3)]
            examples = sorted(
                examples_current[frame_key].get(key, []),
                key=lambda x: x.get('income') or 0,
                reverse=True,
            )[:5]
            display_name = display_client_name(list(raw_names[key].elements()))
            examples = [dict(ex) for ex in examples]  # legs can share example dicts
            for ex in examples:
                ex['client'] = display_name
                if ex.get('registrant'):
                    ex['registrant'] = display_client_name([ex['registrant']])
            return {
                'key': key,
                'name': display_name,
                'current': round(current_income, 2),
                'baseline': round(baseline_income, 2),
                'delta': round(current_income - baseline_income, 2),
                'ratio': round(current_income / baseline_income, 3) if baseline_income > 0 else None,
                'filings_current': m['filings_current'],
                'filings_baseline': m['filings_baseline'],
                'topics': [t for t, _ in topics_by_frame_key[frame_key].get(key, Counter()).most_common(4)],
                'registrants': [display_client_name([n]) for n in registrant_names],
                'series': series,
                'examples': examples,
            }

        new_entrant_keys = {
            key for key, m in candidates.items()
            if m['baseline'] == 0 and m['pre_baseline_sum'] == 0 and m['current'] >= NEW_ENTRANT_FLOOR
        }

        riser_candidates = [
            (key, m) for key, m in candidates.items()
            if key not in new_entrant_keys
            and max(m['current'], m['baseline']) >= FLOOR
            and (m['current'] - m['baseline']) > 0
        ]
        faller_candidates = [
            (key, m) for key, m in candidates.items()
            if max(m['current'], m['baseline']) >= FLOOR
            and (m['current'] - m['baseline']) < 0
        ]
        new_entrant_candidates = [(key, candidates[key]) for key in new_entrant_keys]

        riser_candidates.sort(key=lambda kv: kv[1]['current'] - kv[1]['baseline'], reverse=True)
        faller_candidates.sort(key=lambda kv: kv[1]['current'] - kv[1]['baseline'])
        new_entrant_candidates.sort(key=lambda kv: kv[1]['current'], reverse=True)

        spec = specs[frame_key]
        curr_y, curr_q, _ = spec['current']
        base_y, base_q, _ = spec['baseline']
        frame = {
            'current_quarter': {
                'year': curr_y, 'quarter': curr_q,
                'label': f'Q{curr_q} {curr_y}',
            },
            'baseline_quarter': {
                'year': base_y, 'quarter': base_q,
                'label': f'Q{base_q} {base_y}',
            },
            'quarter_totals': quarter_totals,
            'risers': [build_mover(k, m) for k, m in riser_candidates[:25]],
            'fallers': [build_mover(k, m) for k, m in faller_candidates[:15]],
            'new_entrants': [build_mover(k, m) for k, m in new_entrant_candidates[:10]],
        }
        if not spec['complete']:
            frame['through'] = spec['through']
        return frame

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'frames': {fk: build_frame(fk) for fk in FRAME_KEYS},
        # Shared complete-quarter history for the per-org drawer chart —
        # identical under both frames.
        'quarters': [f'{y} Q{q}' for y, q in window_quarters],
    }


def _pct_change(current: float, baseline: float) -> float | None:
    if baseline is None or baseline == 0:
        return None
    return round((current - baseline) / baseline * 100, 1)


def compute_data_checks(trends: dict, stats: dict | None = None) -> dict:
    """Compute diagnostics to contextualize anomaly detection outputs.

    Mention/share diagnostics read from the complete-quarter frame (the
    dashboard's headline comparison). The rolling 90/180-day filing-date
    windows below are deliberately kept: they measure INGEST health (is the
    pipeline still receiving filings at the expected rate?), which is a
    filing-date question, not a report-quarter comparison.
    """
    today = datetime.now()
    current_end = today.strftime('%Y-%m-%d')
    current_start = (today - timedelta(days=90)).strftime('%Y-%m-%d')
    prev_start = (today - timedelta(days=180)).strftime('%Y-%m-%d')
    prev_end = (today - timedelta(days=90)).strftime('%Y-%m-%d')
    yoy_start = (today - timedelta(days=455)).strftime('%Y-%m-%d')
    yoy_end = (today - timedelta(days=365)).strftime('%Y-%m-%d')

    with get_db() as conn:
        # is_current = 1 throughout: a superseded original was still received
        # on its filing_date, but counting it alongside the amendment/
        # termination that replaced it would double-count one report period's
        # worth of real activity as two, inflating every volume metric below.
        current_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ? AND is_current = 1",
            (current_start, current_end),
        ).fetchone()[0]
        prev_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ? AND is_current = 1",
            (prev_start, prev_end),
        ).fetchone()[0]
        yoy_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ? AND is_current = 1",
            (yoy_start, yoy_end),
        ).fetchone()[0]

        annual_rows = query_to_dicts(
            conn,
            """
            SELECT CAST(strftime('%Y', filing_date) AS INTEGER) AS year, COUNT(*) AS filings
            FROM filings
            WHERE filing_date IS NOT NULL AND is_current = 1
            GROUP BY year
            ORDER BY year
            """,
            (),
        )
        monthly_rows = query_to_dicts(
            conn,
            """
            SELECT
                CAST(strftime('%Y', filing_date) AS INTEGER) AS year,
                CAST(strftime('%m', filing_date) AS INTEGER) AS month,
                COUNT(*) AS filings
            FROM filings
            WHERE filing_date IS NOT NULL
              AND filing_date >= date('now', '-4 years')
              AND is_current = 1
            GROUP BY year, month
            ORDER BY year, month
            """,
            (),
        )
        recent_coverage_row = query_to_dicts(
            conn,
            """
            SELECT
                COUNT(DISTINCT a.id) AS total_activities,
                COUNT(DISTINCT e.activity_id) AS extracted_activities
            FROM activities a
            JOIN filings f ON a.filing_id = f.id
            LEFT JOIN activity_extractions_rules e ON e.activity_id = a.id
            WHERE f.filing_date BETWEEN ? AND ? AND f.is_current = 1
            """,
            (current_start, current_end),
        )
        leg_rows = query_to_dicts(
            conn,
            """
            SELECT e.legislation
            FROM activity_extractions_rules e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            WHERE f.filing_date BETWEEN ? AND ?
              AND e.legislation IS NOT NULL
              AND f.is_current = 1
            """,
            (current_start, current_end),
        )

    quarter_frame = trends.get('frames', {}).get('quarter', {})
    frame_label = quarter_frame.get('label', 'latest complete quarter')
    baseline_label = quarter_frame.get('baseline_label', 'same quarter last year')
    current_mentions = quarter_frame.get('current_total_mentions', 0) or 0
    baseline_mentions = quarter_frame.get('baseline_total_mentions', 0) or 0

    monthly_by_month = defaultdict(list)
    for row in monthly_rows:
        month = int(row.get('month') or 0)
        filings = int(row.get('filings') or 0)
        if month:
            monthly_by_month[month].append(filings)

    month_avg = {}
    for month in range(1, 13):
        values = monthly_by_month.get(month, [])
        month_avg[month] = (sum(values) / len(values)) if values else 0

    due_months = {1, 4, 7, 10}
    total_month_avg = sum(month_avg.values())
    due_month_share_pct = round(
        (sum(month_avg[m] for m in due_months) / total_month_avg * 100), 1
    ) if total_month_avg else None
    due_values = [month_avg[m] for m in due_months if month_avg[m] > 0]
    non_due_values = [month_avg[m] for m in range(1, 13) if m not in due_months and month_avg[m] > 0]
    due_month_avg = (sum(due_values) / len(due_values)) if due_values else None
    non_due_month_avg = (sum(non_due_values) / len(non_due_values)) if non_due_values else None
    due_vs_non_due_ratio = round(due_month_avg / non_due_month_avg, 2) if due_month_avg and non_due_month_avg else None

    current_year = today.year
    complete_years = [
        (int(row['year']), int(row['filings']))
        for row in annual_rows
        if row.get('year') is not None and int(row['year']) < current_year
    ]
    secular_change_pct = None
    cagr_pct = None
    secular_start_year = None
    secular_end_year = None
    if len(complete_years) >= 2:
        secular_start_year, start_filings = complete_years[0]
        secular_end_year, end_filings = complete_years[-1]
        if start_filings > 0:
            secular_change_pct = round((end_filings - start_filings) / start_filings * 100, 1)
        span = secular_end_year - secular_start_year
        if start_filings > 0 and span > 0:
            cagr_pct = round((((end_filings / start_filings) ** (1 / span)) - 1) * 100, 2)

    coverage_recent_pct = None
    coverage_all_time_pct = stats.get('extracted_pct') if stats else None
    if recent_coverage_row:
        row = recent_coverage_row[0]
        total_activities = int(row.get('total_activities') or 0)
        extracted_activities = int(row.get('extracted_activities') or 0)
        coverage_recent_pct = round(extracted_activities / total_activities * 100, 1) if total_activities else None

    total_leg_tags = 0
    dropped_leg_tags = 0
    normalized_leg_tags = 0
    for row in leg_rows:
        values = json.loads(row.get('legislation') or '[]')
        for raw in values:
            source = normalize_tag(raw)
            if not source:
                continue
            total_leg_tags += 1
            normalized = normalize_legislation(source)
            if not normalized:
                dropped_leg_tags += 1
                continue
            if normalized != source:
                normalized_leg_tags += 1
    dropped_leg_pct = round(dropped_leg_tags / total_leg_tags * 100, 1) if total_leg_tags else None
    normalized_leg_pct = round(normalized_leg_tags / total_leg_tags * 100, 1) if total_leg_tags else None

    # Top-topic concentration in the complete-quarter frame. The exported
    # list is ranked by |score| (decliners surface too), so take the max
    # share rather than assuming the first item has it.
    quarter_topics = trends.get('topics', {}).get('quarter', [])
    top_topic_share = round(max((x.get('current_share_pct', 0) for x in quarter_topics), default=0), 2) if quarter_topics else None
    top5_topic_share = round(
        sum(sorted((x.get('current_share_pct', 0) for x in quarter_topics), reverse=True)[:5]), 2
    ) if quarter_topics else None

    flags = []

    mentions_yoy_change = _pct_change(current_mentions, baseline_mentions)
    if mentions_yoy_change is not None and abs(mentions_yoy_change) >= 30:
        severity = 'high' if abs(mentions_yoy_change) >= 50 else 'medium'
        flags.append({
            'severity': severity,
            'title': 'Large quarter-volume shift',
            'detail': f"{frame_label} extracted mentions are {mentions_yoy_change:+.1f}% vs {baseline_label} ({current_mentions:,} vs {baseline_mentions:,}).",
        })

    filings_yoy_change = _pct_change(current_filings, yoy_filings)
    if filings_yoy_change is not None and abs(filings_yoy_change) >= 20:
        severity = 'medium' if abs(filings_yoy_change) >= 35 else 'low'
        flags.append({
            'severity': severity,
            'title': 'Filing-volume baseline drift',
            'detail': f"90d filing count is {filings_yoy_change:+.1f}% vs year-ago ({current_filings:,} vs {yoy_filings:,}).",
        })

    if due_vs_non_due_ratio is not None and due_vs_non_due_ratio >= 1.8:
        severity = 'high' if due_vs_non_due_ratio >= 3 else 'medium'
        flags.append({
            'severity': severity,
            'title': 'Strong seasonal pattern',
            'detail': f"Due-cycle months average {due_vs_non_due_ratio:.2f}x filing volume vs other months.",
        })

    if due_month_share_pct is not None and due_month_share_pct >= 60:
        flags.append({
            'severity': 'medium',
            'title': 'Quarter-cycle concentration',
            'detail': f"{due_month_share_pct:.1f}% of average monthly volume falls in Jan/Apr/Jul/Oct.",
        })

    if (
        coverage_recent_pct is not None
        and coverage_all_time_pct is not None
        and coverage_recent_pct + 3 < coverage_all_time_pct
    ):
        flags.append({
            'severity': 'medium',
            'title': 'Extraction coverage dip',
            'detail': f"Recent 90d coverage is {coverage_recent_pct:.1f}% vs {coverage_all_time_pct:.1f}% all-time.",
        })

    if dropped_leg_pct is not None and dropped_leg_pct >= 5:
        severity = 'high' if dropped_leg_pct >= 10 else 'medium'
        flags.append({
            'severity': severity,
            'title': 'Legislation label noise',
            'detail': f"{dropped_leg_pct:.1f}% of recent legislation tags were dropped as malformed/noise.",
        })

    if top_topic_share is not None and top_topic_share >= 15:
        flags.append({
            'severity': 'low',
            'title': 'Top-topic concentration',
            'detail': f"Top topic accounts for {top_topic_share:.2f}% of {frame_label} extracted mentions (top 5 = {top5_topic_share:.2f}%).",
        })

    severity_counts = {
        'high': sum(1 for f in flags if f.get('severity') == 'high'),
        'medium': sum(1 for f in flags if f.get('severity') == 'medium'),
        'low': sum(1 for f in flags if f.get('severity') == 'low'),
    }
    if severity_counts['high'] > 0:
        status = 'needs_attention'
    elif severity_counts['medium'] > 0:
        status = 'review'
    else:
        status = 'ok'

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'frame': 'quarter',
        'frame_label': frame_label,
        'status': status,
        'severity_counts': severity_counts,
        'metrics': {
            'mentions_current_quarter': current_mentions,
            'mentions_baseline_quarter': baseline_mentions,
            'mentions_vs_yoy_pct': mentions_yoy_change,
            'filings_current_90d': current_filings,
            'filings_prev_90d': prev_filings,
            'filings_yoy_90d': yoy_filings,
            'filings_vs_prev_pct': _pct_change(current_filings, prev_filings),
            'filings_vs_yoy_pct': filings_yoy_change,
            'due_vs_non_due_ratio_4y': due_vs_non_due_ratio,
            'due_month_avg_filings': round(due_month_avg, 1) if due_month_avg is not None else None,
            'non_due_month_avg_filings': round(non_due_month_avg, 1) if non_due_month_avg is not None else None,
            'quarter_due_month_share_pct': due_month_share_pct,
            'coverage_recent_90d_pct': coverage_recent_pct,
            'coverage_all_time_pct': coverage_all_time_pct,
            'legislation_dropped_pct': dropped_leg_pct,
            'legislation_normalized_pct': normalized_leg_pct,
            'top_topic_share_pct': top_topic_share,
            'top5_topic_share_pct': top5_topic_share,
            'secular_change_pct': secular_change_pct,
            'secular_cagr_pct': cagr_pct,
            'secular_start_year': secular_start_year,
            'secular_end_year': secular_end_year,
        },
        'flags': flags,
    }


def export_json(output_dir: str = 'docs/data'):
    """Export all data as JSON files for the dashboard."""
    import os
    os.makedirs(output_dir, exist_ok=True)

    print("Computing trends...")
    trends = compute_trends()

    print("Generating alerts...")
    alerts = generate_alerts(trends)

    print("Getting stats...")
    stats = get_stats()

    print("Computing data checks...")
    checks = compute_data_checks(trends, stats)

    print("Getting recent filings...")
    recent = get_recent_filings(300)

    print("Getting time series...")
    def names_for(cat: str) -> set:
        return {
            item.get('name')
            for frame in ('quarter', 'qtd')
            for item in trends.get(cat, {}).get(frame, [])
            if item.get('name')
        }
    tracked_topics = names_for('topics')
    timeseries = get_time_series(20, topics_to_track=tracked_topics, track_names={
        'entities': names_for('entities'),
        'legislation': names_for('legislation'),
    })

    print("Computing organization spend movers...")
    clients_data = None
    try:
        clients_data = compute_client_movers()
    except Exception as e:
        # Non-fatal: the dashboard falls back to hiding the Organizations
        # view when clients.json is absent, so a bug here shouldn't take
        # down the rest of the daily refresh.
        print(f"  Warning: organization movers failed: {e}")

    # Write files
    with open(f'{output_dir}/trends.json', 'w') as f:
        json.dump(trends, f, indent=2)

    with open(f'{output_dir}/alerts.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'alerts': alerts
        }, f, indent=2)

    with open(f'{output_dir}/stats.json', 'w') as f:
        json.dump(stats, f, indent=2)

    with open(f'{output_dir}/checks.json', 'w') as f:
        json.dump(checks, f, indent=2)

    with open(f'{output_dir}/recent.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'filings': recent
        }, f, indent=2)

    with open(f'{output_dir}/timeseries.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            **timeseries
        }, f, indent=2)

    if clients_data is not None:
        with open(f'{output_dir}/clients.json', 'w') as f:
            json.dump(clients_data, f, indent=2)

    print(f"Exported JSON files to {output_dir}/")
    print(f"  - {len(alerts)} alerts")
    for frame_key in ('quarter', 'qtd'):
        meta = trends['frames'][frame_key]
        print(f"  - {frame_key}: {meta['label']} vs {meta['baseline_label']} — "
              f"{len(trends['topics'][frame_key])} topics, "
              f"{meta['current_filings']:,} filings vs {meta['baseline_filings']:,}")
    print(f"  - {len(recent)} recent filings")
    print(f"  - {len(timeseries['quarters'])} quarters of time series")
    if clients_data is not None:
        for frame_key in ('quarter', 'qtd'):
            frame = clients_data['frames'][frame_key]
            print(f"  - orgs [{frame_key}]: {len(frame['risers'])} risers, {len(frame['fallers'])} fallers, "
                  f"{len(frame['new_entrants'])} new entrants ({frame['current_quarter']['label']} "
                  f"vs {frame['baseline_quarter']['label']})")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "export":
            export_json()
        elif cmd == "alerts":
            trends = compute_trends()
            alerts = generate_alerts(trends)
            for a in alerts[:10]:
                print(f"[{a['type']}] {a['headline']}")
        elif cmd == "trends":
            trends = compute_trends()
            meta = trends['frames']['quarter']
            print(f"\nTop Moving Topics ({meta['label']} vs {meta['baseline_label']}):")
            for t in trends['topics']['quarter'][:15]:
                delta = t.get('share_delta_pp', 0)
                print(f"  {delta:+6.2f}pp yoy  {t['count']:5d} mentions  {t['client_count']:5d} orgs  {t['name']}")
    else:
        print("Usage:")
        print("  python 08_trends.py export  - Export JSON for dashboard")
        print("  python 08_trends.py alerts  - Show current alerts")
        print("  python 08_trends.py trends  - Show trending topics")
