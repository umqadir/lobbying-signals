"""Extract structured classifications from lobbying activity descriptions.

Schema:
- domain: broad policy area
- topics: specific policy issues
- entities: government bodies/agencies/committees
- legislation: bills, laws, regulations, code sections
"""

import os
import json
import time
from google import genai
from google.genai import types

from db import get_db, query_to_dicts

# Extraction prompt with naming conventions
EXTRACT_PROMPT = '''Extract structured information from this lobbying activity description.

Return a JSON object with:
- domain: the broad policy area
- topics: specific policy issues mentioned
- entities: government bodies, agencies, or committees referenced
- legislation: bills, laws, regulations, or code sections mentioned

NAMING CONVENTIONS (follow strictly):

For topics:
- Use lowercase, singular form: "tariff" not "Tariffs" or "tariff policy"
- Be specific but not redundant: "drug pricing" not "pharmaceutical drug pricing policy"
- No articles or filler words: "medicare reimbursement" not "the Medicare reimbursement issue"
- Prefer common terms: "tax credit" not "taxation credit mechanism"

For entities:
- Use official short names: "FDA", "EPA", "Senate Finance Committee"
- Spell out if not universally abbreviated: "Army Corps of Engineers" not "USACE"
- Include chamber for congressional committees: "House Ways and Means Committee"

For legislation:
- Bills: use format "H.R. 1234" or "S. 1234" (with spaces and periods)
- Include common name if well-known: "H.R. 1 (One Big Beautiful Bill Act)"
- Regulations: "Section 301", "26 U.S.C. 45Q"
- Named laws: use common name: "CHIPS Act", "Inflation Reduction Act"

Only include what is clearly present. Empty arrays are fine.

Description: {description}'''


def init_extraction_tables():
    """Create tables for extracted classifications."""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS activity_extractions (
                id INTEGER PRIMARY KEY,
                activity_id INTEGER UNIQUE REFERENCES activities(id),
                domain TEXT,
                topics TEXT,  -- JSON array
                entities TEXT,  -- JSON array
                legislation TEXT,  -- JSON array
                raw_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_extractions_activity ON activity_extractions(activity_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_extractions_domain ON activity_extractions(domain)')

        # Normalization dictionary
        conn.execute('''
            CREATE TABLE IF NOT EXISTS normalization_dict (
                id INTEGER PRIMARY KEY,
                field TEXT NOT NULL,  -- 'topics', 'entities', 'legislation'
                variant TEXT NOT NULL,
                canonical TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(field, variant)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_norm_lookup ON normalization_dict(field, variant)')
        conn.commit()


def extract_batch(batch_size: int = 100, model: str = "gemini-3-flash-preview"):
    """Extract classifications for a batch of activities."""
    init_extraction_tables()

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type='application/json',
        thinking_config=types.ThinkingConfig(thinking_level='minimal')
    )

    # Get activities without extractions, prioritizing recent filings
    sql = '''
        SELECT a.id, a.description
        FROM activities a
        JOIN filings f ON a.filing_id = f.id
        LEFT JOIN activity_extractions e ON a.id = e.activity_id
        WHERE e.id IS NULL
          AND a.description IS NOT NULL
          AND LENGTH(a.description) > 50
        ORDER BY f.filing_date DESC
        LIMIT ?
    '''

    with get_db() as conn:
        activities = query_to_dicts(conn, sql, (batch_size,))

    if not activities:
        print("No activities to process")
        return 0

    print(f"Extracting from {len(activities)} activities...")
    extracted = 0

    with get_db() as conn:
        for i, activity in enumerate(activities):
            try:
                prompt = EXTRACT_PROMPT.format(description=activity['description'][:1500])

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config
                )

                text = response.text
                if not text:
                    continue

                data = json.loads(text)

                # Apply normalization dictionary
                data = apply_normalization(conn, data)

                conn.execute('''
                    INSERT OR REPLACE INTO activity_extractions
                    (activity_id, domain, topics, entities, legislation, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    activity['id'],
                    data.get('domain', ''),
                    json.dumps(data.get('topics', [])),
                    json.dumps(data.get('entities', [])),
                    json.dumps(data.get('legislation', [])),
                    text
                ))
                extracted += 1

                if (i + 1) % 20 == 0:
                    conn.commit()
                    print(f"  {i + 1}/{len(activities)} processed")

                time.sleep(0.05)

            except Exception as e:
                print(f"  Error on activity {activity['id']}: {e}")
                continue

        conn.commit()

    print(f"Extracted {extracted} activities")
    return extracted


def apply_normalization(conn, data: dict) -> dict:
    """Apply normalization dictionary to extracted data."""
    norm_cache = {}

    def normalize(field: str, values: list) -> list:
        result = []
        for v in values:
            cache_key = (field, v)
            if cache_key not in norm_cache:
                row = conn.execute(
                    'SELECT canonical FROM normalization_dict WHERE field = ? AND variant = ?',
                    (field, v)
                ).fetchone()
                norm_cache[cache_key] = row[0] if row else v
            result.append(norm_cache[cache_key])
        return list(dict.fromkeys(result))  # Dedupe while preserving order

    if 'topics' in data:
        data['topics'] = normalize('topics', data['topics'])
    if 'entities' in data:
        data['entities'] = normalize('entities', data['entities'])
    if 'legislation' in data:
        data['legislation'] = normalize('legislation', data['legislation'])

    return data


def find_variants(field: str, min_count: int = 2) -> list[tuple]:
    """Find potential duplicate variants in a field using fuzzy matching."""
    from difflib import SequenceMatcher

    with get_db() as conn:
        # Get all unique values for this field
        if field == 'domain':
            rows = conn.execute('''
                SELECT domain, COUNT(*) as cnt FROM activity_extractions
                WHERE domain IS NOT NULL AND domain != ''
                GROUP BY domain ORDER BY cnt DESC
            ''').fetchall()
            values = [(r[0], r[1]) for r in rows]
        else:
            rows = conn.execute(f'''
                SELECT {field} FROM activity_extractions
                WHERE {field} IS NOT NULL AND {field} != '[]'
            ''').fetchall()
            # Flatten and count
            from collections import Counter
            counter = Counter()
            for row in rows:
                for item in json.loads(row[0]):
                    counter[item] += 1
            values = [(k, v) for k, v in counter.items() if v >= min_count]

    # Find similar pairs
    similar_pairs = []
    values_list = [v[0] for v in values]

    for i, v1 in enumerate(values_list):
        for v2 in values_list[i+1:]:
            ratio = SequenceMatcher(None, v1.lower(), v2.lower()).ratio()
            if ratio > 0.8 and ratio < 1.0:  # Similar but not identical
                similar_pairs.append((v1, v2, ratio))

    return sorted(similar_pairs, key=lambda x: -x[2])


def build_normalization_batch(field: str, batch_size: int = 50):
    """Use LLM to review variants and suggest canonical forms."""
    variants = find_variants(field, min_count=2)

    if not variants:
        print(f"No variants found for {field}")
        return

    print(f"Found {len(variants)} potential variant pairs for {field}")

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))

    prompt = f'''Review these pairs of terms from lobbying data "{field}" field.
For each pair, decide if they refer to the same thing.
If yes, choose the better canonical form (more standard, clearer).
If no, mark as "different".

Return JSON array of objects with:
- variant1, variant2: the original terms
- same: true/false
- canonical: the preferred form (if same=true)

Be conservative - only merge if clearly the same concept.

Pairs to review:
{json.dumps(variants[:batch_size], indent=2)}'''

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type='application/json',
        thinking_config=types.ThinkingConfig(thinking_level='low')
    )

    try:
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt,
            config=config
        )

        decisions = json.loads(response.text)

        with get_db() as conn:
            added = 0
            for d in decisions:
                if d.get('same') and d.get('canonical'):
                    canonical = d['canonical']
                    for variant in [d['variant1'], d['variant2']]:
                        if variant != canonical:
                            try:
                                conn.execute('''
                                    INSERT OR IGNORE INTO normalization_dict (field, variant, canonical)
                                    VALUES (?, ?, ?)
                                ''', (field, variant, canonical))
                                added += 1
                            except:
                                pass
            conn.commit()
            print(f"Added {added} normalization mappings for {field}")

    except Exception as e:
        print(f"Error building normalization: {e}")


def show_extraction_stats():
    """Show statistics about extractions."""
    with get_db() as conn:
        total = conn.execute('SELECT COUNT(*) FROM activity_extractions').fetchone()[0]

        print(f"\nExtraction Statistics ({total} total)")
        print("=" * 50)

        # Domain distribution
        print("\nTop Domains:")
        rows = conn.execute('''
            SELECT domain, COUNT(*) as cnt FROM activity_extractions
            WHERE domain IS NOT NULL
            GROUP BY domain ORDER BY cnt DESC LIMIT 15
        ''').fetchall()
        for row in rows:
            print(f"  {row[1]:5d}  {row[0]}")

        # Topic counts
        print("\nTop Topics:")
        from collections import Counter
        topic_counter = Counter()
        rows = conn.execute('SELECT topics FROM activity_extractions WHERE topics != "[]"').fetchall()
        for row in rows:
            for topic in json.loads(row[0]):
                topic_counter[topic] += 1
        for topic, cnt in topic_counter.most_common(20):
            print(f"  {cnt:5d}  {topic}")

        # Entity counts
        print("\nTop Entities:")
        entity_counter = Counter()
        rows = conn.execute('SELECT entities FROM activity_extractions WHERE entities != "[]"').fetchall()
        for row in rows:
            for entity in json.loads(row[0]):
                entity_counter[entity] += 1
        for entity, cnt in entity_counter.most_common(15):
            print(f"  {cnt:5d}  {entity}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "extract":
            batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 100
            extract_batch(batch_size)

        elif cmd == "normalize":
            field = sys.argv[2] if len(sys.argv) > 2 else "topics"
            build_normalization_batch(field)

        elif cmd == "variants":
            field = sys.argv[2] if len(sys.argv) > 2 else "topics"
            variants = find_variants(field)
            for v1, v2, score in variants[:30]:
                print(f"{score:.2f}  '{v1}' <-> '{v2}'")

        elif cmd == "stats":
            show_extraction_stats()

        else:
            print(f"Unknown command: {cmd}")
    else:
        print("Usage:")
        print("  python 06_extract.py extract [batch_size]  - Extract classifications")
        print("  python 06_extract.py normalize [field]     - Build normalization dict")
        print("  python 06_extract.py variants [field]      - Show potential duplicates")
        print("  python 06_extract.py stats                 - Show extraction statistics")
