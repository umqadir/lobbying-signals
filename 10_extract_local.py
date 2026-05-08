"""Fast local extraction of topics and entities without LLM calls.

Uses:
1. Trained TF-IDF classifier for broad category
2. Keyword matching for specific topics
3. Regex patterns for legislation references
4. Named entity recognition for government agencies
"""

import re
import json
from collections import defaultdict

from db import get_db, query_to_dicts

# Try to load trained classifier
try:
    from _09_train_classifier import LocalClassifier
    classifier = LocalClassifier()
    HAS_CLASSIFIER = True
except:
    # Fallback import for numeric prefix
    import importlib.util
    import sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("train", Path(__file__).parent / "09_train_classifier.py")
    train = importlib.util.module_from_spec(spec)
    sys.modules["train"] = train
    spec.loader.exec_module(train)
    classifier = train.LocalClassifier()
    HAS_CLASSIFIER = True

# Topic keywords mapped to canonical names
TOPIC_KEYWORDS = {
    # Trade
    "tariff": ["tariff", "tariffs", "import duties", "import tax"],
    "trade agreement": ["trade agreement", "free trade", "usmca", "nafta", "tpp", "bilateral trade"],
    "export control": ["export control", "export license", "export restriction"],
    "sanctions": ["sanction", "sanctions", "ofac", "embargo"],

    # Healthcare
    "drug pricing": ["drug pricing", "prescription drug", "pharmaceutical price", "340b"],
    "medicare": ["medicare", "cms", "part d", "part b", "medicare advantage"],
    "medicaid": ["medicaid", "chip", "managed care"],
    "fda approval": ["fda", "food and drug", "drug approval", "medical device approval"],
    "telehealth": ["telehealth", "telemedicine", "virtual care", "remote patient"],

    # Tech
    "artificial intelligence": ["artificial intelligence", " ai ", "machine learning", "algorithmic", "generative ai", "chatgpt"],
    "privacy": ["privacy", "data protection", "gdpr", "ccpa", "personal data"],
    "antitrust": ["antitrust", "competition policy", "monopoly", "merger review"],
    "section 230": ["section 230", "cda", "content moderation", "platform liability"],
    "cybersecurity": ["cybersecurity", "cyber security", "data breach", "ransomware", "cisa"],
    "digital asset": ["cryptocurrency", "crypto", "bitcoin", "digital asset", "blockchain", "stablecoin"],

    # Energy
    "renewable energy": ["renewable", "solar", "wind energy", "clean energy", "green energy"],
    "oil and gas": ["oil and gas", "petroleum", "natural gas", "drilling", "fracking"],
    "nuclear": ["nuclear energy", "nuclear power", "nuclear reactor"],
    "electric vehicle": ["electric vehicle", " ev ", "evs", "charging infrastructure", "battery"],
    "carbon": ["carbon", "emissions", "greenhouse gas", "climate change", "net zero"],

    # Finance
    "banking regulation": ["banking", "bank regulation", "dodd-frank", "volcker"],
    "tax credit": ["tax credit", "tax incentive", "tax deduction"],
    "cryptocurrency": ["cryptocurrency", "digital currency", "bitcoin", "stablecoin"],

    # Defense
    "appropriation": ["appropriation", "defense budget", "ndaa", "military spending"],
    "procurement": ["procurement", "defense contract", "dod acquisition"],

    # Agriculture
    "farm bill": ["farm bill", "agricultural act", "snap benefits"],
    "biofuel": ["biofuel", "ethanol", "biodiesel", "renewable fuel"],

    # Labor
    "workforce development": ["workforce development", "job training", "apprenticeship"],
    "minimum wage": ["minimum wage", "wage increase", "living wage"],
    "immigration labor": ["h-1b", "h-2a", "h-2b", "guest worker", "work visa"],

    # Environment
    "clean water": ["clean water", "water quality", "safe drinking water", "pfas"],
    "clean air": ["clean air", "air quality", "emissions standard"],

    # Other
    "infrastructure": ["infrastructure", "roads", "bridges", "broadband"],
    "grant": ["grant program", "federal grant", "funding opportunity"],
    "research and development": ["r&d", "research and development", "innovation", "nih funding", "nsf"],
}

# Flatten for fast lookup
KEYWORD_TO_TOPIC = {}
for topic, keywords in TOPIC_KEYWORDS.items():
    for kw in keywords:
        KEYWORD_TO_TOPIC[kw.lower()] = topic

# Government entities to look for
GOVT_ENTITIES = {
    # Congress
    "senate": "Senate",
    "house of representatives": "House of Representatives",
    "congress": "Congress",
    "house committee": "House",
    "senate committee": "Senate",

    # Executive
    "white house": "White House",
    "executive office": "Executive Office of the President",

    # Agencies (common abbreviations and full names)
    "epa": "EPA",
    "environmental protection agency": "EPA",
    "fda": "FDA",
    "food and drug administration": "FDA",
    "dod": "Department of Defense",
    "department of defense": "Department of Defense",
    "pentagon": "Department of Defense",
    "hhs": "HHS",
    "department of health": "HHS",
    "cms": "CMS",
    "centers for medicare": "CMS",
    "usda": "USDA",
    "department of agriculture": "USDA",
    "dot": "DOT",
    "department of transportation": "DOT",
    "faa": "FAA",
    "federal aviation": "FAA",
    "fcc": "FCC",
    "federal communications": "FCC",
    "ftc": "FTC",
    "federal trade commission": "FTC",
    "sec": "SEC",
    "securities and exchange": "SEC",
    "treasury": "Treasury",
    "department of treasury": "Treasury",
    "irs": "IRS",
    "internal revenue": "IRS",
    "dhs": "DHS",
    "homeland security": "DHS",
    "fema": "FEMA",
    "doe": "Department of Energy",
    "department of energy": "Department of Energy",
    "doj": "DOJ",
    "department of justice": "DOJ",
    "state department": "State Department",
    "department of state": "State Department",
    "interior": "Department of Interior",
    "department of interior": "Department of Interior",
    "commerce": "Department of Commerce",
    "department of commerce": "Department of Commerce",
    "labor": "Department of Labor",
    "department of labor": "Department of Labor",
    "education": "Department of Education",
    "department of education": "Department of Education",
    "va ": "VA",
    "veterans affairs": "VA",
    "hud": "HUD",
    "housing and urban": "HUD",
    "nih": "NIH",
    "national institutes of health": "NIH",
    "nsf": "NSF",
    "national science foundation": "NSF",
    "cdc": "CDC",
    "centers for disease": "CDC",
    "osha": "OSHA",
    "occupational safety": "OSHA",
    "nhtsa": "NHTSA",
    "sba": "SBA",
    "small business administration": "SBA",
}

# Legislation patterns
LEGISLATION_PATTERNS = [
    r"H\.?R\.?\s*\d+",  # H.R. 123
    r"S\.?\s*\d+",  # S. 123
    r"P\.?L\.?\s*\d+-\d+",  # P.L. 117-123
    r"(?:the\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+Act(?:\s+of\s+\d{4})?",  # Inflation Reduction Act
]


def extract_topics(text: str) -> list[str]:
    """Extract topic keywords from text."""
    text_lower = text.lower()
    found_topics = set()

    for keyword, topic in KEYWORD_TO_TOPIC.items():
        if keyword in text_lower:
            found_topics.add(topic)

    return list(found_topics)[:5]  # Max 5 topics


def extract_entities(text: str) -> list[str]:
    """Extract government entities from text."""
    text_lower = text.lower()
    found_entities = set()

    for pattern, entity in GOVT_ENTITIES.items():
        if pattern in text_lower:
            found_entities.add(entity)

    return list(found_entities)[:5]  # Max 5 entities


def extract_legislation(text: str) -> list[str]:
    """Extract legislation references from text."""
    found = set()

    for pattern in LEGISLATION_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            found.add(match.strip())

    return list(found)[:5]  # Max 5 refs


def extract_all(text: str) -> dict:
    """Extract topics, entities, and legislation from text."""
    return {
        "topics": extract_topics(text),
        "entities": extract_entities(text),
        "legislation": extract_legislation(text)
    }


def process_unextracted_activities(batch_size: int = 1000, verbose: bool = True):
    """Process activities that haven't been extracted yet."""

    sql = """
        SELECT a.id, a.description, a.issue_code
        FROM activities a
        LEFT JOIN activity_extractions e ON a.id = e.activity_id
        WHERE e.id IS NULL
          AND a.description IS NOT NULL
          AND LENGTH(a.description) > 20
        LIMIT ?
    """

    with get_db() as conn:
        # Ensure table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_extractions (
                id INTEGER PRIMARY KEY,
                activity_id INTEGER UNIQUE REFERENCES activities(id),
                domain TEXT,
                topics TEXT,
                entities TEXT,
                legislation TEXT,
                raw_response TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        activities = query_to_dicts(conn, sql, (batch_size,))

    if not activities:
        if verbose:
            print("No unextracted activities")
        return 0

    if verbose:
        print(f"Processing {len(activities)} activities...")

    extracted_count = 0
    topic_counts = defaultdict(int)
    entity_counts = defaultdict(int)

    with get_db() as conn:
        for i, activity in enumerate(activities):
            desc = activity["description"]

            # Extract using rules
            result = extract_all(desc)

            # Get domain from classifier
            if HAS_CLASSIFIER:
                code, conf, domain = classifier.predict(desc)
            else:
                domain = activity.get("issue_code", "")

            # Track counts
            for t in result["topics"]:
                topic_counts[t] += 1
            for e in result["entities"]:
                entity_counts[e] += 1

            # Insert extraction
            conn.execute("""
                INSERT OR REPLACE INTO activity_extractions
                (activity_id, domain, topics, entities, legislation)
                VALUES (?, ?, ?, ?, ?)
            """, (
                activity["id"],
                domain,
                json.dumps(result["topics"]),
                json.dumps(result["entities"]),
                json.dumps(result["legislation"])
            ))

            if result["topics"] or result["entities"]:
                extracted_count += 1

            if verbose and (i + 1) % 500 == 0:
                conn.commit()
                print(f"  {i + 1}/{len(activities)} processed, {extracted_count} with extractions")

        conn.commit()

    if verbose:
        print(f"\nExtracted from {extracted_count}/{len(activities)} activities")
        print(f"\nTop topics: {dict(sorted(topic_counts.items(), key=lambda x: -x[1])[:10])}")
        print(f"Top entities: {dict(sorted(entity_counts.items(), key=lambda x: -x[1])[:10])}")

    return extracted_count


def get_extraction_stats():
    """Get stats on extractions."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM activities WHERE description IS NOT NULL").fetchone()[0]
        extracted = conn.execute("SELECT COUNT(*) FROM activity_extractions").fetchone()[0]

        # Count topics
        topic_counts = {}
        rows = conn.execute("SELECT topics FROM activity_extractions WHERE topics != '[]'").fetchall()
        for row in rows:
            for topic in json.loads(row[0]):
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

        # Count entities
        entity_counts = {}
        rows = conn.execute("SELECT entities FROM activity_extractions WHERE entities != '[]'").fetchall()
        for row in rows:
            for entity in json.loads(row[0]):
                entity_counts[entity] = entity_counts.get(entity, 0) + 1

    return {
        "total_activities": total,
        "extracted": extracted,
        "pct": round(extracted / total * 100, 1) if total > 0 else 0,
        "top_topics": dict(sorted(topic_counts.items(), key=lambda x: -x[1])[:20]),
        "top_entities": dict(sorted(entity_counts.items(), key=lambda x: -x[1])[:20])
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = get_extraction_stats()
        print(f"Extracted: {stats['extracted']}/{stats['total_activities']} ({stats['pct']}%)")
        print(f"\nTop topics:")
        for topic, count in list(stats["top_topics"].items())[:15]:
            print(f"  {topic}: {count}")
        print(f"\nTop entities:")
        for entity, count in list(stats["top_entities"].items())[:10]:
            print(f"  {entity}: {count}")
    else:
        batch = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
        print(f"Processing up to {batch} activities...")
        process_unextracted_activities(batch_size=batch)
