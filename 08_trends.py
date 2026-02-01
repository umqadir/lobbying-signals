"""Compute trends and generate alerts from lobbying data."""

import json
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from db import get_db, query_to_dicts


def get_extraction_counts(days_back: int = None, start_date: str = None, end_date: str = None) -> dict:
    """Get counts of topics, entities, legislation from extractions."""
    with get_db() as conn:
        # Build date filter
        if days_back:
            date_filter = f"AND f.filing_date >= date('now', '-{days_back} days')"
        elif start_date and end_date:
            date_filter = f"AND f.filing_date BETWEEN '{start_date}' AND '{end_date}'"
        else:
            date_filter = ""

        sql = f'''
            SELECT e.domain, e.topics, e.entities, e.legislation,
                   f.filing_date, c.name as client_name, f.income
            FROM activity_extractions e
            JOIN activities a ON e.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            JOIN clients c ON f.client_id = c.id
            WHERE e.domain IS NOT NULL
            {date_filter}
        '''
        rows = query_to_dicts(conn, sql)

    # Count occurrences
    domains = Counter()
    topics = Counter()
    entities = Counter()
    legislation = Counter()
    topic_clients = defaultdict(set)
    topic_income = defaultdict(float)

    for row in rows:
        domains[row['domain']] += 1

        for topic in json.loads(row['topics'] or '[]'):
            topics[topic] += 1
            topic_clients[topic].add(row['client_name'])
            topic_income[topic] += row['income'] or 0

        for entity in json.loads(row['entities'] or '[]'):
            entities[entity] += 1

        for leg in json.loads(row['legislation'] or '[]'):
            legislation[leg] += 1

    return {
        'domains': domains,
        'topics': topics,
        'entities': entities,
        'legislation': legislation,
        'topic_clients': {k: list(v) for k, v in topic_clients.items()},
        'topic_income': dict(topic_income),
        'total_rows': len(rows)
    }


def compute_trends() -> dict:
    """Compute trend data comparing different time periods."""
    # Current periods
    current_7d = get_extraction_counts(days_back=7)
    current_30d = get_extraction_counts(days_back=30)

    # Previous periods for comparison
    today = datetime.now()
    prev_7d_end = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    prev_7d_start = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    prev_30d_end = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    prev_30d_start = (today - timedelta(days=60)).strftime('%Y-%m-%d')

    prev_7d = get_extraction_counts(start_date=prev_7d_start, end_date=prev_7d_end)
    prev_30d = get_extraction_counts(start_date=prev_30d_start, end_date=prev_30d_end)

    def calc_change(current: Counter, previous: Counter, min_count: int = 3) -> list:
        """Calculate percent change, filter by minimum count."""
        results = []
        for item, count in current.most_common(100):
            if count < min_count:
                continue
            prev_count = previous.get(item, 0)
            if prev_count > 0:
                change_pct = ((count - prev_count) / prev_count) * 100
            else:
                change_pct = 100 if count > 0 else 0  # New item

            results.append({
                'name': item,
                'count': count,
                'prev_count': prev_count,
                'change_pct': round(change_pct, 1)
            })
        return sorted(results, key=lambda x: (-x['change_pct'], -x['count']))[:50]

    return {
        'generated_at': datetime.now().isoformat(),
        'topics': {
            '7d': calc_change(current_7d['topics'], prev_7d['topics']),
            '30d': calc_change(current_30d['topics'], prev_30d['topics'])
        },
        'domains': {
            '7d': calc_change(current_7d['domains'], prev_7d['domains']),
            '30d': calc_change(current_30d['domains'], prev_30d['domains'])
        },
        'entities': {
            '7d': calc_change(current_7d['entities'], prev_7d['entities']),
            '30d': calc_change(current_30d['entities'], prev_30d['entities'])
        },
        'legislation': {
            '7d': calc_change(current_30d['legislation'], prev_30d['legislation']),
            '30d': calc_change(current_30d['legislation'], prev_30d['legislation'])
        },
        'topic_clients': current_30d.get('topic_clients', {}),
        'topic_income': current_30d.get('topic_income', {})
    }


def generate_alerts(trends: dict, min_change_pct: float = 50, min_count: int = 5) -> list:
    """Generate alerts for significant changes."""
    alerts = []

    # Check topics for spikes
    for item in trends['topics']['30d']:
        if item['change_pct'] >= min_change_pct and item['count'] >= min_count:
            clients = trends.get('topic_clients', {}).get(item['name'], [])[:5]
            income = trends.get('topic_income', {}).get(item['name'], 0)

            alert = {
                'type': 'spike',
                'category': 'topic',
                'name': item['name'],
                'current_count': item['count'],
                'prev_count': item['prev_count'],
                'change_pct': item['change_pct'],
                'top_clients': clients,
                'total_income': income,
                'headline': generate_headline(item, 'topic')
            }
            alerts.append(alert)

    # Check for new entrants (items with 0 previous count)
    for item in trends['topics']['30d']:
        if item['prev_count'] == 0 and item['count'] >= min_count:
            clients = trends.get('topic_clients', {}).get(item['name'], [])[:5]

            alert = {
                'type': 'new_entrant',
                'category': 'topic',
                'name': item['name'],
                'current_count': item['count'],
                'top_clients': clients,
                'headline': f"New lobbying topic emerges: {item['name']} ({item['count']} activities)"
            }
            # Avoid duplicate with spike alert
            if not any(a['name'] == item['name'] and a['type'] == 'spike' for a in alerts):
                alerts.append(alert)

    # Check entities for increased attention
    for item in trends['entities']['30d']:
        if item['change_pct'] >= min_change_pct and item['count'] >= min_count:
            alert = {
                'type': 'spike',
                'category': 'entity',
                'name': item['name'],
                'current_count': item['count'],
                'prev_count': item['prev_count'],
                'change_pct': item['change_pct'],
                'headline': generate_headline(item, 'entity')
            }
            alerts.append(alert)

    # Check legislation for increased attention
    for item in trends['legislation']['30d']:
        if item['change_pct'] >= min_change_pct and item['count'] >= min_count:
            alert = {
                'type': 'spike',
                'category': 'legislation',
                'name': item['name'],
                'current_count': item['count'],
                'prev_count': item['prev_count'],
                'change_pct': item['change_pct'],
                'headline': f"Lobbying on {item['name']} up {item['change_pct']:.0f}%"
            }
            alerts.append(alert)

    # Sort by significance (change_pct * count)
    alerts.sort(key=lambda x: -(x.get('change_pct', 0) * x.get('current_count', 0)))

    return alerts[:20]  # Top 20 alerts


def generate_headline(item: dict, category: str) -> str:
    """Generate a readable headline for an alert."""
    name = item['name']
    change = item['change_pct']
    count = item['count']

    if change >= 200:
        verb = "triples"
    elif change >= 100:
        verb = "doubles"
    elif change >= 50:
        verb = f"surges {change:.0f}%"
    else:
        verb = f"up {change:.0f}%"

    if category == 'topic':
        return f"Lobbying on '{name}' {verb} ({count} activities in 30 days)"
    elif category == 'entity':
        return f"Attention to {name} {verb} ({count} mentions)"
    else:
        return f"{name} lobbying {verb}"


def get_stats() -> dict:
    """Get summary statistics."""
    with get_db() as conn:
        total_filings = conn.execute('SELECT COUNT(*) FROM filings').fetchone()[0]
        total_activities = conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0]
        total_extracted = conn.execute('SELECT COUNT(*) FROM activity_extractions').fetchone()[0]

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
        'date_range': {
            'start': date_range[0] if date_range else None,
            'end': date_range[1] if date_range else None
        },
        'quarters': [
            {'year': q[0], 'quarter': q[1], 'filings': q[2], 'income': q[3]}
            for q in quarters
        ]
    }


def get_recent_filings(limit: int = 50) -> list:
    """Get recent filings with extractions."""
    with get_db() as conn:
        sql = '''
            SELECT f.id, f.filing_date, f.income, f.year, f.quarter,
                   c.name as client_name, r.name as registrant_name,
                   e.domain, e.topics
            FROM filings f
            JOIN clients c ON f.client_id = c.id
            JOIN registrants r ON f.registrant_id = r.id
            LEFT JOIN activities a ON a.filing_id = f.id
            LEFT JOIN activity_extractions e ON e.activity_id = a.id
            WHERE f.filing_date IS NOT NULL
            GROUP BY f.id
            ORDER BY f.filing_date DESC
            LIMIT ?
        '''
        rows = query_to_dicts(conn, sql, (limit,))

    return [{
        'id': r['id'],
        'date': r['filing_date'],
        'client': r['client_name'],
        'registrant': r['registrant_name'],
        'income': r['income'],
        'year': r['year'],
        'quarter': r['quarter'],
        'domain': r['domain'],
        'topics': json.loads(r['topics']) if r['topics'] else []
    } for r in rows]


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

    print("Getting recent filings...")
    recent = get_recent_filings(100)

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

    with open(f'{output_dir}/recent.json', 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'filings': recent
        }, f, indent=2)

    print(f"Exported JSON files to {output_dir}/")
    print(f"  - {len(alerts)} alerts")
    print(f"  - {len(trends['topics']['30d'])} trending topics")
    print(f"  - {len(recent)} recent filings")


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
            print("\nTop Trending Topics (30d):")
            for t in trends['topics']['30d'][:15]:
                print(f"  {t['change_pct']:+6.1f}%  {t['count']:4d}  {t['name']}")
    else:
        print("Usage:")
        print("  python 08_trends.py export  - Export JSON for dashboard")
        print("  python 08_trends.py alerts  - Show current alerts")
        print("  python 08_trends.py trends  - Show trending topics")
