"""Compute trends and generate alerts from lobbying data."""

import json
import re
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from db import get_db, query_to_dicts

RULES_PATH = Path("rules/topic_rules.json")

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
    client_counts: defaultdict
    income: defaultdict
    income_seen_filing_ids: defaultdict


def normalize_tag(value: str) -> str:
    """Normalize extracted tag spacing."""
    if value is None:
        return ''
    return ' '.join(str(value).split()).strip()


def normalize_legislation(value: str) -> str:
    """Normalize common legislation formatting variants."""
    tag = normalize_tag(value)
    if not tag:
        return ''

    tag = re.sub(r'^[`"\']+|[`"\']+$', '', tag).strip()
    tag = re.sub(r'\s+', ' ', tag).strip(' ,;:.()[]{}')
    tag = re.sub(r'^(?:issues?\s+related\s+to\s+)(?:the\s+)?', '', tag, flags=re.IGNORECASE).strip()
    tag = re.sub(r'^(?:related\s+to\s+)(?:the\s+)?', '', tag, flags=re.IGNORECASE).strip()
    tag = re.sub(r'^year\s+(continuing appropriations and extensions)$', r'\1', tag, flags=re.IGNORECASE)
    tag = tag.strip(' ,;:.()[]{}')
    if not tag:
        return ''

    hr_any = re.search(r'\bH\.?\s*R\.?\s*(\d{1,5})\b', tag, flags=re.IGNORECASE)
    if hr_any:
        return f"H.R. {hr_any.group(1)}"

    senate_any = re.search(r'(?<![A-Za-z])S\.?\s*(\d{1,5})\b', tag, flags=re.IGNORECASE)
    if senate_any:
        return f"S. {senate_any.group(1)}"

    pl_any = re.search(r'\bP\.?\s*L\.?\s*(\d{1,3}-\d{1,5})\b', tag, flags=re.IGNORECASE)
    if pl_any:
        return f"P.L. {pl_any.group(1)}"

    compact = re.sub(r'[^A-Za-z0-9-]', '', tag).upper()

    hr_match = re.match(r'^HR(\d+)$', compact)
    if hr_match:
        return f"H.R. {hr_match.group(1)}"

    senate_match = re.match(r'^S(\d+)$', compact)
    if senate_match:
        return f"S. {senate_match.group(1)}"

    pl_match = re.match(r'^PL(\d+-\d+)$', compact)
    if pl_match:
        return f"P.L. {pl_match.group(1)}"

    lower = tag.lower()
    if lower in LEGISLATION_NOISE_EXACT:
        return ''
    if lower.startswith('and ') or lower.startswith('an ') or lower.startswith('the '):
        return ''
    if lower.endswith(' and extensions') and 'appropriations' not in lower:
        return ''

    words = re.findall(r"[A-Za-z0-9']+", tag)
    if len(words) == 1 and len(words[0]) <= 2:
        return ''

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


def get_extraction_counts(days_back: int = None, start_date: str = None, end_date: str = None) -> dict:
    """Get counts of topics, entities, legislation from extractions."""
    with get_db() as conn:
        # Build date filter
        params: list = []
        if days_back:
            date_filter = "AND f.filing_date >= date('now', ?)"
            params.append(f"-{days_back} days")
        elif start_date and end_date:
            date_filter = "AND f.filing_date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        else:
            date_filter = ""

        sql = f'''
            SELECT f.id as filing_id, f.sopr_filing_id as filing_uuid,
                   e.coarse_topic as domain, e.topics, e.entities, e.legislation,
                   f.filing_date, c.name as client_name, r.name as registrant_name, f.income
            FROM activity_extractions_rules e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            JOIN clients c ON f.client_id = c.id
            JOIN registrants r ON f.registrant_id = r.id
            WHERE e.coarse_topic IS NOT NULL
            {date_filter}
        '''
        rows = query_to_dicts(conn, sql, tuple(params))

    def make_agg() -> _Agg:
        return _Agg(
            counts=Counter(),
            client_counts=defaultdict(Counter),
            income=defaultdict(float),
            income_seen_filing_ids=defaultdict(set),
        )

    # Count occurrences (counts are "mentions"/activity-level, not filings)
    domains = make_agg()
    topics = make_agg()
    entities = make_agg()
    legislation = make_agg()
    topic_examples = defaultdict(dict)
    entity_examples = defaultdict(dict)
    domain_examples = defaultdict(dict)
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

    for row in rows:
        filing_id = row.get('filing_id')
        filing_uuid = row.get('filing_uuid')
        client = row.get('client_name')
        registrant = row.get('registrant_name')
        income = row.get('income') or 0
        filing_date = row.get('filing_date')

        domain = display_domain(row.get('domain'))
        if domain:
            domains.counts[domain] += 1
            if client:
                domains.client_counts[domain][client] += 1
            if filing_id and filing_id not in domains.income_seen_filing_ids[domain]:
                domains.income[domain] += income
                domains.income_seen_filing_ids[domain].add(filing_id)
            add_example(domain_examples, domain, filing_id, filing_date, client, registrant, income, filing_uuid)

        for topic in json.loads(row['topics'] or '[]'):
            if is_general_topic(topic):
                continue
            topic = display_topic(topic)
            if not topic:
                continue
            topics.counts[topic] += 1
            if client:
                topics.client_counts[topic][client] += 1
            if filing_id and filing_id not in topics.income_seen_filing_ids[topic]:
                topics.income[topic] += income
                topics.income_seen_filing_ids[topic].add(filing_id)
            add_example(topic_examples, topic, filing_id, filing_date, client, registrant, income, filing_uuid)

        for entity in json.loads(row['entities'] or '[]'):
            entity = normalize_tag(entity)
            if not entity:
                continue
            entities.counts[entity] += 1
            if client:
                entities.client_counts[entity][client] += 1
            if filing_id and filing_id not in entities.income_seen_filing_ids[entity]:
                entities.income[entity] += income
                entities.income_seen_filing_ids[entity].add(filing_id)
            add_example(entity_examples, entity, filing_id, filing_date, client, registrant, income, filing_uuid)

        for leg in json.loads(row['legislation'] or '[]'):
            leg = normalize_legislation(leg)
            if not leg:
                continue
            legislation.counts[leg] += 1
            if client:
                legislation.client_counts[leg][client] += 1
            if filing_id and filing_id not in legislation.income_seen_filing_ids[leg]:
                legislation.income[leg] += income
                legislation.income_seen_filing_ids[leg].add(filing_id)
            add_example(legislation_examples, leg, filing_id, filing_date, client, registrant, income, filing_uuid)

    def top_clients(agg: _Agg, limit: int = 10) -> dict:
        return {k: [c for c, _ in v.most_common(limit)] for k, v in agg.client_counts.items()}

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
        'domains': domains.counts,
        'topics': topics.counts,
        'entities': entities.counts,
        'legislation': legislation.counts,
        'topic_clients': top_clients(topics),
        'topic_income': dict(topics.income),
        'entity_clients': top_clients(entities),
        'entity_income': dict(entities.income),
        'domain_clients': top_clients(domains),
        'domain_income': dict(domains.income),
        'legislation_clients': top_clients(legislation),
        'legislation_income': dict(legislation.income),
        'topic_examples': finalize_examples(topic_examples),
        'entity_examples': finalize_examples(entity_examples),
        'domain_examples': finalize_examples(domain_examples),
        'legislation_examples': finalize_examples(legislation_examples),
        'total_rows': len(rows)
    }


def compute_trends() -> dict:
    """Compute trend data with seasonality-aware and momentum-aware metrics."""
    # Anchor windows to the newest filing in the DB, not the wall clock. The
    # dashboard labels its windows by max(filing_date) (stats.date_range.end),
    # so anchoring here to date('now') makes the two disagree whenever
    # ingestion lags — and silently empties the dashboard if it stalls.
    with get_db() as conn:
        max_date = conn.execute(
            'SELECT MAX(filing_date) FROM filings WHERE filing_date IS NOT NULL'
        ).fetchone()[0]
    if max_date:
        # +1 day so lexicographic BETWEEN covers the whole as-of day
        today = datetime.strptime(max_date[:10], '%Y-%m-%d') + timedelta(days=1)
    else:
        today = datetime.now()
    window_days = {'30d': 30, '90d': 90}

    def range_for(days: int, offset_days: int = 0) -> tuple[str, str]:
        end = (today - timedelta(days=offset_days)).strftime('%Y-%m-%d')
        start = (today - timedelta(days=offset_days + days)).strftime('%Y-%m-%d')
        return start, end

    datasets = {}
    for window_key, days in window_days.items():
        current_start, current_end = range_for(days, 0)
        prev_start, prev_end = range_for(days, days)
        yoy_start, yoy_end = range_for(days, 365)
        datasets[window_key] = {
            'current': get_extraction_counts(start_date=current_start, end_date=current_end),
            'prev': get_extraction_counts(start_date=prev_start, end_date=prev_end),
            'yoy': get_extraction_counts(start_date=yoy_start, end_date=yoy_end),
        }

    def confidence_label(
        count: int,
        prev_count: int,
        yoy_count: int,
        delta_prev_pp: float,
        delta_yoy_pp: float
    ) -> str:
        if count >= 50 and prev_count >= 50 and yoy_count >= 50 and delta_prev_pp >= 0.20 and delta_yoy_pp >= 0.35:
            return 'high'
        if count >= 25 and (delta_prev_pp >= 0.15 or delta_yoy_pp >= 0.15):
            return 'medium'
        if count >= 10 and (delta_prev_pp > 0 or delta_yoy_pp > 0):
            return 'low'
        return 'low'

    def calc_change(
        current: dict,
        previous: dict,
        yoy: dict,
        key: str,
        min_count: int = 1,
        max_items: int = 500
    ) -> tuple[list, dict]:
        current_counts = current[key]
        prev_counts = previous[key]
        yoy_counts = yoy[key]
        current_total = current.get('total_rows', 0)
        prev_total = previous.get('total_rows', 0)
        yoy_total = yoy.get('total_rows', 0)

        results = []
        dropped_min = 0
        for item, count in current_counts.items():
            if count < min_count:
                dropped_min += 1
                continue

            prev_count = prev_counts.get(item, 0)
            yoy_count = yoy_counts.get(item, 0)

            current_share = (count / current_total * 100) if current_total else 0
            prev_share = (prev_count / prev_total * 100) if prev_total else 0
            yoy_share = (yoy_count / yoy_total * 100) if yoy_total else 0

            delta_prev_pp = current_share - prev_share
            delta_yoy_pp = current_share - yoy_share

            momentum_ratio = (count / prev_count) if prev_count > 0 else None
            seasonal_ratio = (count / yoy_count) if yoy_count > 0 else None
            confidence = confidence_label(count, prev_count, yoy_count, delta_prev_pp, delta_yoy_pp)

            # Single exhaustive ranking: all tags above min_count are ranked by change + scale.
            score = (delta_yoy_pp * 0.65) + (delta_prev_pp * 0.35) + min(count / 2000, 1) * 0.1

            results.append({
                'name': item,
                'count': count,
                'prev_count': prev_count,
                'yoy_count': yoy_count,
                'current_share_pct': round(current_share, 3),
                'prev_share_pct': round(prev_share, 3),
                'yoy_share_pct': round(yoy_share, 3),
                'share_delta_prev_pp': round(delta_prev_pp, 3),
                'share_delta_yoy_pp': round(delta_yoy_pp, 3),
                'momentum_ratio': round(momentum_ratio, 3) if momentum_ratio is not None else None,
                'seasonal_ratio': round(seasonal_ratio, 3) if seasonal_ratio is not None else None,
                'score': round(score, 4),
                'confidence': confidence
            })

        sorted_results = sorted(
            results,
            key=lambda x: (-x['score'], -x['count'], x['name'])
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
        'generated_at': datetime.now().isoformat(),
        'window_totals': {},
        'topics': {},
        'domains': {},
        'entities': {},
        'legislation': {},
        'topic_clients': {},
        'topic_income': {},
        'topic_examples': {},
        'entity_clients': {},
        'entity_income': {},
        'entity_examples': {},
        'domain_clients': {},
        'domain_income': {},
        'domain_examples': {},
        'legislation_clients': {},
        'legislation_income': {},
        'legislation_examples': {},
        'selection_meta': {},
    }

    for window_key, parts in datasets.items():
        current = parts['current']
        previous = parts['prev']
        yoy = parts['yoy']

        topics, topics_meta = calc_change(current, previous, yoy, 'topics', min_count=1, max_items=500)
        domains, domains_meta = calc_change(current, previous, yoy, 'domains')
        entities, entities_meta = calc_change(current, previous, yoy, 'entities', min_count=1, max_items=500)
        legislation, legislation_meta = calc_change(current, previous, yoy, 'legislation', min_count=5, max_items=500)

        result['window_totals'][window_key] = {
            'current_total_mentions': current.get('total_rows', 0),
            'prev_total_mentions': previous.get('total_rows', 0),
            'yoy_total_mentions': yoy.get('total_rows', 0),
        }

        result['topics'][window_key] = topics
        result['domains'][window_key] = domains
        result['entities'][window_key] = entities
        result['legislation'][window_key] = legislation
        result['selection_meta'][window_key] = {
            'topics': topics_meta,
            'domains': domains_meta,
            'entities': entities_meta,
            'legislation': legislation_meta,
        }

        result['topic_clients'][window_key] = keep_for(topics, current.get('topic_clients', {}))
        result['topic_income'][window_key] = keep_for(topics, current.get('topic_income', {}))
        result['topic_examples'][window_key] = keep_for(topics, current.get('topic_examples', {}))

        result['entity_clients'][window_key] = keep_for(entities, current.get('entity_clients', {}))
        result['entity_income'][window_key] = keep_for(entities, current.get('entity_income', {}))
        result['entity_examples'][window_key] = keep_for(entities, current.get('entity_examples', {}))

        result['domain_clients'][window_key] = keep_for(domains, current.get('domain_clients', {}))
        result['domain_income'][window_key] = keep_for(domains, current.get('domain_income', {}))
        result['domain_examples'][window_key] = keep_for(domains, current.get('domain_examples', {}))

        result['legislation_clients'][window_key] = keep_for(legislation, current.get('legislation_clients', {}))
        result['legislation_income'][window_key] = keep_for(legislation, current.get('legislation_income', {}))
        result['legislation_examples'][window_key] = keep_for(legislation, current.get('legislation_examples', {}))

    return result


def generate_alerts(
    trends: dict,
    window: str = '90d',
    min_share_delta_pp: float = 0.25,
    min_count: int = 25
) -> list:
    """Generate seasonality-aware alerts based on share-of-mentions changes."""
    alerts = []
    categories = [
        ('topics', 'topic', 'topic_clients', 'topic_income'),
        ('entities', 'entity', 'entity_clients', 'entity_income'),
        ('domains', 'domain', 'domain_clients', 'domain_income'),
        ('legislation', 'legislation', 'legislation_clients', 'legislation_income'),
    ]

    for trend_key, category, client_key, income_key in categories:
        for item in trends.get(trend_key, {}).get(window, []):
            count = item.get('count', 0)
            delta_yoy = item.get('share_delta_yoy_pp', 0) or 0
            delta_prev = item.get('share_delta_prev_pp', 0) or 0
            if count < min_count:
                continue
            if delta_yoy < min_share_delta_pp and delta_prev < min_share_delta_pp:
                continue

            clients = trends.get(client_key, {}).get(window, {}).get(item['name'], [])[:5]
            income = trends.get(income_key, {}).get(window, {}).get(item['name'], 0)
            alerts.append({
                'type': 'signal',
                'category': category,
                'name': item['name'],
                'current_count': count,
                'prev_count': item.get('prev_count', 0),
                'yoy_count': item.get('yoy_count', 0),
                'share_delta_yoy_pp': round(delta_yoy, 3),
                'share_delta_prev_pp': round(delta_prev, 3),
                'signal_confidence': item.get('confidence', 'low'),
                'top_clients': clients,
                'total_income': income,
                'headline': generate_headline(item, category),
            })

    confidence_rank = {'high': 0, 'medium': 1, 'low': 2}
    alerts.sort(
        key=lambda x: (
            confidence_rank.get(x.get('signal_confidence', 'low'), 2),
            -max(x.get('share_delta_yoy_pp', 0), x.get('share_delta_prev_pp', 0)),
            -x.get('current_count', 0),
        )
    )
    return alerts[:20]


def generate_headline(item: dict, category: str) -> str:
    """Generate a readable headline for a seasonality-aware alert."""
    name = item['name']
    delta_yoy = item.get('share_delta_yoy_pp', 0) or 0
    delta_prev = item.get('share_delta_prev_pp', 0) or 0
    use_yoy = abs(delta_yoy) >= abs(delta_prev)
    delta = delta_yoy if use_yoy else delta_prev
    baseline = 'year-ago period' if use_yoy else 'prior period'
    direction = 'up' if delta >= 0 else 'down'
    magnitude = abs(delta)
    count = item.get('count', 0)

    if category == 'topic':
        return f"'{name}' share {direction} {magnitude:.2f} pp vs {baseline} ({count} mentions)"
    if category == 'entity':
        return f"{name} attention {direction} {magnitude:.2f} pp vs {baseline} ({count} mentions)"
    if category == 'domain':
        return f"{name} domain share {direction} {magnitude:.2f} pp vs {baseline} ({count} mentions)"
    return f"{name} share {direction} {magnitude:.2f} pp vs {baseline} ({count} mentions)"


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

        # Get quarter breakdown
        quarters = conn.execute('''
            SELECT year, quarter, COUNT(*) as cnt, SUM(income) as total_income
            FROM filings
            GROUP BY year, quarter
            ORDER BY year DESC, quarter DESC
            LIMIT 8
        ''').fetchall()

    return {
        'generated_at': datetime.now().isoformat(),
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
        ]
    }


def get_recent_filings(limit: int = 300) -> list:
    """Get recent filings with extractions."""
    with get_db() as conn:
        sql = '''
            WITH recent AS (
                SELECT id, sopr_filing_id, filing_date, income, year, quarter, client_id, registrant_id
                FROM filings
                WHERE filing_date IS NOT NULL
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
            legislation = normalize_legislation(l)
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



def get_time_series(quarters_back: int = 20, topics_to_track: set[str] | None = None) -> dict:
    """Get report-quarter time series data for charts."""
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
                e.topics
            FROM activity_extractions_rules e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            WHERE f.year IS NOT NULL
              AND f.quarter BETWEEN 1 AND 4
              AND (f.year * 4 + f.quarter) BETWEEN ? AND ?
              AND e.topics IS NOT NULL
            ''',
            (min_q_index, max_q_index),
        )

    topic_by_quarter = defaultdict(Counter)
    for row in topic_rows:
        q_index = int(row.get('q_index') or 0)
        if not q_index:
            continue
        topics = json.loads(row['topics'] or '[]')
        for topic in topics:
            if is_general_topic(topic):
                continue
            topic = display_topic(topic)
            if topic:
                topic_by_quarter[q_index][topic] += 1

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

    quarterly_filings = [int(q.get('filings') or 0) for q in quarters]
    top_quarters = sorted(quarters, key=lambda x: x.get('filings', 0), reverse=True)[:3]

    latest_4q_filings = sum(quarterly_filings[-4:]) if quarterly_filings else 0
    prior_4q_filings = sum(quarterly_filings[-8:-4]) if len(quarterly_filings) >= 8 else 0

    return {
        'quarters': quarters,
        'top_topics': top_topics,
        'tracked_topics': tracked_topics,
        'topic_series': topic_series,
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
def _pct_change(current: float, baseline: float) -> float | None:
    if baseline is None or baseline == 0:
        return None
    return round((current - baseline) / baseline * 100, 1)


def compute_data_checks(trends: dict, stats: dict | None = None) -> dict:
    """Compute diagnostics to contextualize anomaly detection outputs."""
    today = datetime.now()
    current_end = today.strftime('%Y-%m-%d')
    current_start = (today - timedelta(days=90)).strftime('%Y-%m-%d')
    prev_start = (today - timedelta(days=180)).strftime('%Y-%m-%d')
    prev_end = (today - timedelta(days=90)).strftime('%Y-%m-%d')
    yoy_start = (today - timedelta(days=455)).strftime('%Y-%m-%d')
    yoy_end = (today - timedelta(days=365)).strftime('%Y-%m-%d')

    with get_db() as conn:
        current_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ?",
            (current_start, current_end),
        ).fetchone()[0]
        prev_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ?",
            (prev_start, prev_end),
        ).fetchone()[0]
        yoy_filings = conn.execute(
            "SELECT COUNT(*) FROM filings WHERE filing_date BETWEEN ? AND ?",
            (yoy_start, yoy_end),
        ).fetchone()[0]

        annual_rows = query_to_dicts(
            conn,
            """
            SELECT CAST(strftime('%Y', filing_date) AS INTEGER) AS year, COUNT(*) AS filings
            FROM filings
            WHERE filing_date IS NOT NULL
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
            WHERE f.filing_date BETWEEN ? AND ?
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
            """,
            (current_start, current_end),
        )

    mention_totals = trends.get('window_totals', {}).get('90d', {})
    current_mentions = mention_totals.get('current_total_mentions', 0) or 0
    prev_mentions = mention_totals.get('prev_total_mentions', 0) or 0
    yoy_mentions = mention_totals.get('yoy_total_mentions', 0) or 0

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

    topics_90d = trends.get('topics', {}).get('90d', [])
    top_topic_share = round(topics_90d[0].get('current_share_pct', 0), 2) if topics_90d else None
    top5_topic_share = round(sum(x.get('current_share_pct', 0) for x in topics_90d[:5]), 2) if topics_90d else None

    flags = []

    mentions_yoy_change = _pct_change(current_mentions, yoy_mentions)
    if mentions_yoy_change is not None and abs(mentions_yoy_change) >= 30:
        severity = 'high' if abs(mentions_yoy_change) >= 50 else 'medium'
        flags.append({
            'severity': severity,
            'title': 'Large window-volume shift',
            'detail': f"90d extracted mentions are {mentions_yoy_change:+.1f}% vs year-ago ({current_mentions:,} vs {yoy_mentions:,}).",
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
            'detail': f"Top topic accounts for {top_topic_share:.2f}% of 90d extracted mentions (top 5 = {top5_topic_share:.2f}%).",
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
        'generated_at': datetime.now().isoformat(),
        'window': '90d',
        'status': status,
        'severity_counts': severity_counts,
        'metrics': {
            'mentions_current_90d': current_mentions,
            'mentions_prev_90d': prev_mentions,
            'mentions_yoy_90d': yoy_mentions,
            'mentions_vs_prev_pct': _pct_change(current_mentions, prev_mentions),
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
    tracked_topics = {
        item.get('name')
        for window in ('30d', '90d')
        for item in trends.get('topics', {}).get(window, [])
        if item.get('name')
    }
    timeseries = get_time_series(20, topics_to_track=tracked_topics)

    # Write files
    with open(f'{output_dir}/trends.json', 'w') as f:
        json.dump(trends, f, indent=2)

    with open(f'{output_dir}/alerts.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'alerts': alerts
        }, f, indent=2)

    with open(f'{output_dir}/stats.json', 'w') as f:
        json.dump(stats, f, indent=2)

    with open(f'{output_dir}/checks.json', 'w') as f:
        json.dump(checks, f, indent=2)

    with open(f'{output_dir}/recent.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'filings': recent
        }, f, indent=2)

    with open(f'{output_dir}/timeseries.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            **timeseries
        }, f, indent=2)

    print(f"Exported JSON files to {output_dir}/")
    print(f"  - {len(alerts)} alerts")
    print(f"  - {len(trends['topics']['30d'])} trending topics")
    print(f"  - {len(recent)} recent filings")
    print(f"  - {len(timeseries['quarters'])} quarters of time series")


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
            print("\nTop Trending Topics (90d):")
            for t in trends['topics']['90d'][:15]:
                yoy = t.get('share_delta_yoy_pp', 0)
                prev = t.get('share_delta_prev_pp', 0)
                print(f"  {yoy:+6.2f}pp yoy  {prev:+6.2f}pp prev  {t['count']:4d}  {t['name']}")
    else:
        print("Usage:")
        print("  python 08_trends.py export  - Export JSON for dashboard")
        print("  python 08_trends.py alerts  - Show current alerts")
        print("  python 08_trends.py trends  - Show trending topics")
