"""Deterministic topic extraction and dictionary mining (no LLM).

This pipeline is designed for high-coverage, auditable extraction:
- L0: raw Senate LDA issue_code (already present on all activities)
- L1: coarse topic from issue_code mapping
- L2: specific topics from rule-based evidence (keywords/programs/acts)

It writes to `activity_extractions_rules` and does not modify legacy LLM tables.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DB_PATH


EXTRACTOR_VERSION = "rules-v3-iter2"
RULES_PATH = Path("rules/topic_rules.json")


COARSE_TOPIC_GROUPS = {
    "trade": {"TAR", "TRD", "TRA", "FOR"},
    "healthcare": {"HCR", "MMM", "PHA", "MED"},
    "technology": {"TEC", "CPI", "SCI", "CPT", "COM", "MIA"},
    "energy_environment": {"ENG", "ENV", "FUE", "CAW", "WAS", "NAT", "CHM"},
    "defense_security": {"DEF", "INT", "HOM", "LAW", "VET"},
    "agriculture_food": {"AGR", "FOO", "ANI"},
    "labor_immigration": {"LBR", "UNM", "IMM"},
    "finance_tax": {"FIN", "BNK", "INS", "TAX", "ECN"},
    "transportation": {"TRA", "AVI", "AUT", "ROD", "RRR", "TRU", "MAR"},
    "education_social": {"EDU", "WEL", "FAM", "CSP"},
    "housing_urban": {"HOU", "RES", "URB"},
    "government_budget": {"BUD", "GOV", "CON", "POS", "DOC"},
    "industry_business": {"SMB", "MAN", "IND", "APP", "RET", "ADV", "AER", "CDT", "BEV", "TOB", "ART", "SPO", "TOR", "TOU", "MON", "ALC", "CIV", "DIS", "FIR", "GAM", "UTI"},
}


COARSE_BY_CODE: dict[str, str] = {}
for coarse, codes in COARSE_TOPIC_GROUPS.items():
    for code in codes:
        COARSE_BY_CODE[code] = coarse


LDA_CODE_LABELS = {
    "ACC": "Accounting", "ADV": "Advertising", "AER": "Aerospace",
    "AGR": "Agriculture", "ALC": "Alcohol & Drug Abuse", "ANI": "Animals",
    "APP": "Apparel/Textiles", "ART": "Arts/Entertainment", "AUT": "Automotive",
    "AVI": "Aviation", "BAN": "Banking", "BEV": "Beverage Industry",
    "BNK": "Bankruptcy", "BUD": "Budget/Appropriations", "CAW": "Clean Air & Water",
    "CDT": "Commodities", "CHM": "Chemicals/Toxics", "CIV": "Civil Rights",
    "COM": "Communications/Broadcasting", "CON": "Constitution", "CPI": "Computer Industry",
    "CPT": "Copyright/Patent/Trademark", "CSP": "Consumer Issues/Safety", "DEF": "Defense",
    "DIS": "Disaster Planning", "DOC": "District of Columbia", "ECN": "Economics",
    "EDU": "Education", "ENG": "Energy/Nuclear", "ENV": "Environment",
    "FAM": "Family/Abortion", "FIN": "Financial Institutions", "FIR": "Firearms",
    "FOO": "Food Industry", "FOR": "Foreign Relations", "FUE": "Fuel/Gas/Oil",
    "GAM": "Gaming/Gambling", "GOV": "Government Issues", "HCR": "Health Issues",
    "HOM": "Homeland Security", "HOU": "Housing", "IMM": "Immigration",
    "IND": "Indian/Native American", "INS": "Insurance", "INT": "Intelligence",
    "LAW": "Law Enforcement", "LBR": "Labor Issues", "MAN": "Manufacturing",
    "MAR": "Marine/Fishing", "MED": "Media/Publishing", "MIA": "Medical/Disease Research",
    "MMM": "Medicare/Medicaid", "MON": "Minting/Money", "NAT": "Natural Resources",
    "PHA": "Pharmacy", "POS": "Postal", "RES": "Real Estate",
    "RET": "Retirement", "ROD": "Roads/Highway", "RRR": "Railroads",
    "SCI": "Science/Technology", "SMB": "Small Business", "SPO": "Sports/Athletics",
    "TAR": "Tariff/Imports", "TAX": "Taxation", "TEC": "Telecommunications",
    "TOB": "Tobacco", "TOR": "Torts", "TOU": "Travel/Tourism",
    "TRA": "Transportation", "TRD": "Trade", "TRU": "Trucking/Shipping",
    "URB": "Urban Development", "UNM": "Unemployment", "UTI": "Utilities",
    "VET": "Veterans", "WAS": "Waste/Hazardous", "WEL": "Welfare",
}


FALLBACK_TOPIC_PREFIX = "general_"
HIGH_SIGNAL_SINGLE_TOKENS = {
    "cybersecurity",
    "privacy",
    "methane",
    "tariff",
    "sanctions",
    "immigration",
    "medicare",
    "medicaid",
    "telehealth",
    "blockchain",
    "cryptocurrency",
    "semiconductor",
    "fiduciary",
    "telemedicine",
    "ransomware",
    "usmca",
}


ENTITY_PATTERNS = [
    ("senate", "Senate"),
    ("house of representatives", "House of Representatives"),
    ("congress", "Congress"),
    ("white house", "White House"),
    ("office of management and budget", "OMB"),
    ("omb", "OMB"),
    ("office of science and technology policy", "OSTP"),
    ("ostp", "OSTP"),
    ("department of defense", "Department of Defense"),
    ("dod", "Department of Defense"),
    ("pentagon", "Department of Defense"),
    ("department of state", "State Department"),
    ("state department", "State Department"),
    ("department of commerce", "Department of Commerce"),
    ("commerce, dept of", "Department of Commerce"),
    ("doc", "Department of Commerce"),
    ("department of justice", "DOJ"),
    ("doj", "DOJ"),
    ("department of treasury", "Treasury"),
    ("treasury, dept of", "Treasury"),
    ("treasury", "Treasury"),
    ("internal revenue service", "IRS"),
    ("irs", "IRS"),
    ("department of health and human services", "HHS"),
    ("health & human services", "HHS"),
    ("hhs", "HHS"),
    ("centers for medicare and medicaid services", "CMS"),
    ("cms", "CMS"),
    ("food and drug administration", "FDA"),
    ("fda", "FDA"),
    ("department of agriculture", "USDA"),
    ("agriculture, dept of", "USDA"),
    ("usda", "USDA"),
    ("department of energy", "Department of Energy"),
    ("energy, dept of", "Department of Energy"),
    ("doe", "Department of Energy"),
    ("environmental protection agency", "EPA"),
    ("epa", "EPA"),
    ("federal communications commission", "FCC"),
    ("fcc", "FCC"),
    ("federal trade commission", "FTC"),
    ("ftc", "FTC"),
    ("securities and exchange commission", "SEC"),
    ("sec", "SEC"),
    ("federal reserve", "Federal Reserve"),
    ("federal deposit insurance corporation", "FDIC"),
    ("fdic", "FDIC"),
    ("office of the comptroller of the currency", "OCC"),
    ("occ", "OCC"),
    ("consumer financial protection bureau", "CFPB"),
    ("cfpb", "CFPB"),
    ("federal aviation administration", "FAA"),
    ("faa", "FAA"),
    ("transportation, dept of", "Department of Transportation"),
    ("department of transportation", "Department of Transportation"),
    ("dot", "Department of Transportation"),
    ("federal railroad administration", "FRA"),
    ("department of labor", "Department of Labor"),
    ("labor, dept of", "Department of Labor"),
    ("dol", "Department of Labor"),
    ("occupational safety and health administration", "OSHA"),
    ("osha", "OSHA"),
    ("department of homeland security", "DHS"),
    ("dhs", "DHS"),
    ("customs and border protection", "CBP"),
    ("cbp", "CBP"),
    ("citizenship and immigration services", "USCIS"),
    ("federal energy regulatory commission", "FERC"),
    ("ferc", "FERC"),
    ("nuclear regulatory commission", "NRC"),
    ("nrc", "NRC"),
    ("executive office of the president", "Executive Office of the President"),
    ("eop", "Executive Office of the President"),
    ("interior, dept of", "Department of the Interior"),
    ("department of the interior", "Department of the Interior"),
    ("doi", "Department of the Interior"),
    ("army, dept of", "Army Corps of Engineers"),
    ("corps of engineers", "Army Corps of Engineers"),
    ("u.s. trade representative", "USTR"),
    ("ustr", "USTR"),
    ("veterans affairs, dept of", "Department of Veterans Affairs"),
    ("department of veterans affairs", "Department of Veterans Affairs"),
    ("va", "Department of Veterans Affairs"),
    ("national aeronautics and space administration", "NASA"),
    ("nasa", "NASA"),
]


LEGISLATION_REGEXES = [
    re.compile(r"\bH\.?\s*R\.?\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bS\.?\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bP\.?\s*L\.?\s*\d+\-\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+U\.?S\.?C\.?\s+§?\s*[0-9A-Za-z\-\.\(\)]+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+C\.?F\.?R\.?\s+§?\s*[0-9A-Za-z\-\.\(\)]+\b", re.IGNORECASE),
    re.compile(r"\bSection\s+[0-9A-Za-z\-\(\)]+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Za-z0-9'&,\-]+(?:\s+[A-Z][A-Za-z0-9'&,\-]+){0,7}\s+Act(?:\s+of\s+\d{4})?\b"),
]


STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "are", "was", "were", "be",
    "been", "being", "will", "would", "could", "should", "can", "about", "into", "over", "under", "after",
    "before", "between", "through", "during", "including", "relating", "related", "issues", "issue", "federal",
    "program", "policy", "policies", "legislation", "rule", "rules", "regulation", "regulations", "quarter",
    "specific", "general", "monitoring", "outreach", "support", "efforts", "regarding", "activity", "activities"
    , "act", "acts", "bill", "bills", "section", "title", "subtitle"
}


GENERIC_TERMS = {
    "federal funding", "federal policy", "general issues", "specific legislation", "appropriations bill",
    "policy issues", "regulatory issues", "agency policy", "congressional outreach"
}


TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def normalize_phrase(text: str) -> str:
    return normalize_space(text.lower())


def contains_phrase(text_lower: str, phrase_lower: str) -> bool:
    if not phrase_lower:
        return False
    if " " in phrase_lower or len(phrase_lower) > 4:
        return phrase_lower in text_lower
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase_lower)}(?![a-z0-9])", text_lower))


def make_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "other"


def fallback_topic_for_issue(issue_code: str | None) -> tuple[str | None, str | None]:
    if not issue_code:
        return None, None
    label = LDA_CODE_LABELS.get(issue_code.upper())
    if not label:
        return f"{FALLBACK_TOPIC_PREFIX}{issue_code.lower()}", issue_code.upper()
    return f"{FALLBACK_TOPIC_PREFIX}{make_slug(label)}", label


def classify_keyword_hits(keyword_hits: list[str]) -> tuple[list[str], list[str], list[str]]:
    phrase_hits: list[str] = []
    signal_hits: list[str] = []
    support_hits: list[str] = []

    for hit in keyword_hits:
        token_count = len(hit.split())
        has_special_chars = bool(re.search(r"[-/&()]", hit))
        has_digits = any(ch.isdigit() for ch in hit)

        if token_count >= 2 or has_special_chars or has_digits:
            phrase_hits.append(hit)
            continue
        if hit in HIGH_SIGNAL_SINGLE_TOKENS:
            signal_hits.append(hit)
            continue
        support_hits.append(hit)

    return phrase_hits, signal_hits, support_hits


def load_rules(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    topics = data.get("topics", [])
    if not isinstance(topics, list) or not topics:
        raise ValueError(f"No topics found in {path}")
    normalized: list[dict[str, Any]] = []
    for topic in topics:
        normalized.append(
            {
                "id": topic["id"],
                "label": topic.get("label", topic["id"]),
                "issue_codes": set(topic.get("issue_codes", [])),
                "keywords": [normalize_phrase(x) for x in topic.get("keywords", []) if x],
                "programs": [normalize_phrase(x) for x in topic.get("programs", []) if x],
                "acts": [normalize_phrase(x) for x in topic.get("acts", []) if x],
                "exclude": [normalize_phrase(x) for x in topic.get("exclude", []) if x],
                "allow_single_support_relaxed": bool(topic.get("allow_single_support_relaxed", False)),
            }
        )
    return normalized


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity_extractions_rules (
            id INTEGER PRIMARY KEY,
            activity_id INTEGER UNIQUE REFERENCES activities(id),
            l0_issue_code TEXT,
            coarse_topic TEXT,
            topics TEXT,
            topic_scores TEXT,
            topic_evidence TEXT,
            entities TEXT,
            legislation TEXT,
            extractor_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_activity_extractions_rules_activity
            ON activity_extractions_rules(activity_id);
        CREATE INDEX IF NOT EXISTS idx_activity_extractions_rules_issue_code
            ON activity_extractions_rules(l0_issue_code);
        CREATE INDEX IF NOT EXISTS idx_activity_extractions_rules_coarse
            ON activity_extractions_rules(coarse_topic);

        CREATE TABLE IF NOT EXISTS topic_candidate_terms (
            id INTEGER PRIMARY KEY,
            issue_code TEXT NOT NULL,
            term TEXT NOT NULL,
            ngram INTEGER NOT NULL,
            doc_freq INTEGER NOT NULL,
            issue_doc_count INTEGER NOT NULL,
            corpus_doc_count INTEGER NOT NULL,
            lift REAL NOT NULL,
            score REAL NOT NULL,
            extractor_version TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_code, term, extractor_version)
        );
        """
    )
    conn.commit()


def extract_legislation(text: str) -> list[str]:
    seen: set[str] = set()
    hits: list[str] = []
    for pattern in LEGISLATION_REGEXES:
        for match in pattern.findall(text):
            value = normalize_space(match if isinstance(match, str) else str(match))
            if len(value) < 4:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(value)
            if len(hits) >= 12:
                return hits
    return hits


def extract_entities(text_lower: str, agencies: str = "", houses_lobbied: str = "") -> list[str]:
    source = " ".join(
        part for part in (text_lower, agencies.lower(), houses_lobbied.lower())
        if part
    )
    found: list[str] = []
    seen: set[str] = set()
    for pattern, label in ENTITY_PATTERNS:
        if contains_phrase(source, pattern) and label not in seen:
            seen.add(label)
            found.append(label)
            if len(found) >= 8:
                break
    return found


def match_hits(text_lower: str, phrases: list[str], max_hits: int = 6) -> list[str]:
    hits: list[str] = []
    for phrase in phrases:
        if contains_phrase(text_lower, phrase):
            hits.append(phrase)
            if len(hits) >= max_hits:
                break
    return hits


def detect_topics(text: str, issue_code: str, rules: list[dict[str, Any]]) -> tuple[list[str], dict[str, float], dict[str, Any]]:
    text_lower = normalize_phrase(text)
    strict_results: list[tuple[str, float, dict[str, Any]]] = []
    relaxed_results: list[tuple[str, float, dict[str, Any]]] = []

    for rule in rules:
        if rule["exclude"]:
            excluded = match_hits(text_lower, rule["exclude"], max_hits=1)
            if excluded:
                continue

        keyword_hits = match_hits(text_lower, rule["keywords"], max_hits=8)
        program_hits = match_hits(text_lower, rule["programs"], max_hits=5)
        act_hits = match_hits(text_lower, rule["acts"], max_hits=4)

        if not keyword_hits and not program_hits and not act_hits:
            continue

        phrase_keyword_hits, signal_keyword_hits, support_keyword_hits = classify_keyword_hits(keyword_hits)
        code_match = issue_code in rule["issue_codes"]
        high_precision = bool(program_hits or act_hits)

        if not code_match and not high_precision and len(phrase_keyword_hits) < 2:
            continue

        strict_score = (
            len(act_hits) * 2.8
            + len(program_hits) * 2.2
            + len(phrase_keyword_hits) * 1.6
            + len(signal_keyword_hits) * 1.1
            + len(support_keyword_hits) * 0.45
            + (0.6 if code_match else 0.0)
        )
        relaxed_score = (
            len(act_hits) * 2.4
            + len(program_hits) * 1.9
            + len(phrase_keyword_hits) * 1.3
            + len(signal_keyword_hits) * 0.9
            + len(support_keyword_hits) * 0.3
            + (0.4 if code_match else 0.0)
        )

        evidence = {
            "keyword_hits": keyword_hits,
            "phrase_keyword_hits": phrase_keyword_hits,
            "signal_keyword_hits": signal_keyword_hits,
            "support_keyword_hits": support_keyword_hits,
            "program_hits": program_hits,
            "act_hits": act_hits,
            "issue_code_match": code_match,
        }

        strict_assign = False
        if high_precision and strict_score >= 2.2 and (code_match or program_hits or phrase_keyword_hits):
            strict_assign = True
        elif code_match and (phrase_keyword_hits or signal_keyword_hits) and strict_score >= 2.0:
            strict_assign = True
        elif len(phrase_keyword_hits) >= 2 and strict_score >= 2.6:
            strict_assign = True
        elif code_match and len(support_keyword_hits) >= 2 and strict_score >= 2.2:
            strict_assign = True

        if strict_assign:
            strict_evidence = dict(evidence)
            strict_evidence["assignment_tier"] = "strict"
            strict_results.append((rule["id"], strict_score, strict_evidence))
            continue

        relaxed_assign = False
        if high_precision and relaxed_score >= 2.0 and (code_match or program_hits or phrase_keyword_hits):
            relaxed_assign = True
        elif code_match and keyword_hits:
            allow_single_support = bool(rule.get("allow_single_support_relaxed", False))
            if signal_keyword_hits or phrase_keyword_hits:
                relaxed_assign = True
            elif len(support_keyword_hits) >= 2:
                relaxed_assign = True
            elif allow_single_support and len(support_keyword_hits) == 1:
                relaxed_assign = True
        elif len(phrase_keyword_hits) >= 2 and relaxed_score >= 2.2:
            relaxed_assign = True

        if relaxed_assign:
            relaxed_evidence = dict(evidence)
            relaxed_evidence["assignment_tier"] = "relaxed"
            relaxed_results.append((rule["id"], relaxed_score, relaxed_evidence))

    strict_ids = {row[0] for row in strict_results}
    topic_results = strict_results + [row for row in relaxed_results if row[0] not in strict_ids]
    topic_results.sort(key=lambda row: (-row[1], row[0]))
    topic_results = topic_results[:8]
    topic_ids = [row[0] for row in topic_results]
    topic_scores = {row[0]: round(row[1], 3) for row in topic_results}
    topic_evidence = {row[0]: row[2] for row in topic_results}

    if topic_ids:
        return topic_ids, topic_scores, topic_evidence

    fallback_topic, fallback_label = fallback_topic_for_issue(issue_code)
    if fallback_topic:
        topic_ids = [fallback_topic]
        topic_scores = {fallback_topic: 0.2}
        topic_evidence = {
            fallback_topic: {
                "assignment_tier": "fallback",
                "fallback_issue_code": issue_code,
                "fallback_label": fallback_label,
                "fallback_coarse_topic": get_coarse_topic(issue_code),
            }
        }
    return topic_ids, topic_scores, topic_evidence


def get_coarse_topic(issue_code: str | None) -> str:
    if not issue_code:
        return "unknown"
    return COARSE_BY_CODE.get(issue_code, "other")


def process_batch(
    conn: sqlite3.Connection,
    rules: list[dict[str, Any]],
    batch_size: int,
    min_description_len: int,
    refresh_existing: bool,
    issue_codes: set[str] | None,
) -> int:
    code_filter = sorted(issue_codes) if issue_codes else []
    code_clause = ""
    if code_filter:
        placeholders = ",".join("?" for _ in code_filter)
        code_clause = f" AND a.issue_code IN ({placeholders})"

    if refresh_existing:
        sql = f"""
            SELECT a.id, a.issue_code, a.description, a.agencies, a.houses_lobbied
            FROM activities a
            WHERE a.description IS NOT NULL
              AND LENGTH(a.description) > ?
              {code_clause}
            ORDER BY a.id
            LIMIT ?
        """
        params: list[Any] = [min_description_len]
        params.extend(code_filter)
        params.append(batch_size)
    else:
        sql = f"""
            SELECT a.id, a.issue_code, a.description, a.agencies, a.houses_lobbied
            FROM activities a
            LEFT JOIN activity_extractions_rules r ON r.activity_id = a.id
            WHERE r.activity_id IS NULL
              AND a.description IS NOT NULL
              AND LENGTH(a.description) > ?
              {code_clause}
            ORDER BY a.id
            LIMIT ?
        """
        params = [min_description_len]
        params.extend(code_filter)
        params.append(batch_size)

    rows = conn.execute(sql, tuple(params)).fetchall()
    if not rows:
        print("No activities to process.")
        return 0

    processed = 0
    assigned_topics = 0
    strict_or_relaxed_topics = 0
    coarse_counter: Counter[str] = Counter()
    specific_counter: Counter[str] = Counter()

    for idx, row in enumerate(rows, start=1):
        activity_id = int(row["id"])
        issue_code = (row["issue_code"] or "").strip()
        description = row["description"] or ""
        agencies = row["agencies"] or ""
        houses_lobbied = row["houses_lobbied"] or ""

        topics, topic_scores, topic_evidence = detect_topics(description, issue_code, rules)
        legislation = extract_legislation(description)
        entities = extract_entities(description.lower(), agencies, houses_lobbied)
        coarse_topic = get_coarse_topic(issue_code)

        conn.execute(
            """
            INSERT OR REPLACE INTO activity_extractions_rules
                (activity_id, l0_issue_code, coarse_topic, topics, topic_scores,
                 topic_evidence, entities, legislation, extractor_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                issue_code,
                coarse_topic,
                json.dumps(topics, ensure_ascii=True),
                json.dumps(topic_scores, ensure_ascii=True),
                json.dumps(topic_evidence, ensure_ascii=True),
                json.dumps(entities, ensure_ascii=True),
                json.dumps(legislation, ensure_ascii=True),
                EXTRACTOR_VERSION,
            ),
        )

        processed += 1
        coarse_counter[coarse_topic] += 1
        if topics:
            assigned_topics += 1
            primary_topic = topics[0]
            primary_evidence = topic_evidence.get(primary_topic, {})
            if primary_evidence.get("assignment_tier") in {"strict", "relaxed"}:
                strict_or_relaxed_topics += 1
            specific_counter.update(topics)

        if idx % 2000 == 0:
            conn.commit()
            print(
                f"  processed {idx}/{len(rows)} | assigned topics on {assigned_topics} | "
                f"strict/relaxed on {strict_or_relaxed_topics}"
            )

    conn.commit()
    print(
        f"Processed {processed} activities; assigned topics in {assigned_topics}; "
        f"strict/relaxed in {strict_or_relaxed_topics}."
    )
    if coarse_counter:
        print("Top coarse topics:", dict(coarse_counter.most_common(8)))
    if specific_counter:
        print("Top specific topics:", dict(specific_counter.most_common(12)))
    return processed


def print_stats(conn: sqlite3.Connection, min_description_len: int) -> None:
    tier_expr = "json_extract(topic_evidence, '$.' || json_extract(topics, '$[0]') || '.assignment_tier')"

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM activities
        WHERE description IS NOT NULL
          AND LENGTH(description) > ?
        """,
        (min_description_len,),
    ).fetchone()[0]
    extracted = conn.execute("SELECT COUNT(*) FROM activity_extractions_rules").fetchone()[0]
    any_topic = conn.execute(
        """
        SELECT COUNT(*)
        FROM activity_extractions_rules
        WHERE topics IS NOT NULL
          AND topics != '[]'
        """
    ).fetchone()[0]
    strict_relaxed = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM activity_extractions_rules
        WHERE ({tier_expr} = 'strict' OR {tier_expr} = 'relaxed')
        """
    ).fetchone()[0]
    fallback = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM activity_extractions_rules
        WHERE {tier_expr} = 'fallback'
        """
    ).fetchone()[0]
    processed_no_topic = conn.execute(
        """
        SELECT COUNT(*)
        FROM activity_extractions_rules
        WHERE topics IS NULL
           OR topics = '[]'
        """
    ).fetchone()[0]
    print(f"Addressable activities: {total}")
    print(f"Rows extracted (rules table): {extracted} ({(100.0 * extracted / total) if total else 0:.2f}%)")
    print(f"Rows with any L2 topic: {any_topic} ({(100.0 * any_topic / total) if total else 0:.2f}%)")
    print(
        "Rows with strict/relaxed L2 topic: "
        f"{strict_relaxed} ({(100.0 * strict_relaxed / total) if total else 0:.2f}%)"
    )
    print(f"Rows with fallback L2 topic: {fallback} ({(100.0 * fallback / total) if total else 0:.2f}%)")
    print(
        "Processed rows with no L2 topic: "
        f"{processed_no_topic} ({(100.0 * processed_no_topic / extracted) if extracted else 0:.2f}% of processed)"
    )

    coarse_rows = conn.execute(
        """
        SELECT coarse_topic, COUNT(*) AS n
        FROM activity_extractions_rules
        GROUP BY coarse_topic
        ORDER BY n DESC
        LIMIT 12
        """
    ).fetchall()
    print("Top coarse topics:")
    for row in coarse_rows:
        print(f"  {row['coarse_topic']}: {row['n']}")

    topic_rows = conn.execute(
        """
        SELECT json_each.value AS topic, COUNT(*) AS n
        FROM activity_extractions_rules
        JOIN json_each(activity_extractions_rules.topics)
        GROUP BY topic
        ORDER BY n DESC
        LIMIT 20
        """
    ).fetchall()
    print("Top specific topics:")
    for row in topic_rows:
        print(f"  {row['topic']}: {row['n']}")


def tokenize_for_mining(text: str) -> list[str]:
    tokens = TOKEN_RE.findall(text.lower())
    clean = [token for token in tokens if token not in STOPWORDS and len(token) <= 32]
    if not clean:
        return []
    terms: list[str] = []
    terms.extend(clean)
    for i in range(len(clean) - 1):
        bigram = f"{clean[i]} {clean[i + 1]}"
        if bigram not in GENERIC_TERMS:
            terms.append(bigram)
    return terms


def mine_candidates(
    conn: sqlite3.Connection,
    min_description_len: int,
    per_code_cap: int,
    min_doc_freq: int,
    min_lift: float,
    top_k: int,
    scope: str,
    replace_existing: bool,
) -> str:
    sql = """
        SELECT a.issue_code, a.description
        FROM activities a
        LEFT JOIN activity_extractions_rules r ON r.activity_id = a.id
        WHERE a.description IS NOT NULL
          AND LENGTH(a.description) > ?
          AND a.issue_code IS NOT NULL
    """
    params: list[Any] = [min_description_len]
    if scope == "processed_unmapped":
        sql += " AND r.activity_id IS NOT NULL AND r.topics = '[]'"
    elif scope == "unprocessed":
        sql += " AND r.activity_id IS NULL"
    elif scope == "any_unmapped":
        sql += " AND (r.activity_id IS NULL OR r.topics = '[]')"
    sql += " ORDER BY a.id"

    df_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    docs_by_code: Counter[str] = Counter()
    global_df: Counter[str] = Counter()
    total_docs = 0

    cursor = conn.execute(sql, tuple(params))
    for row in cursor:
        issue_code = (row["issue_code"] or "").strip()
        if not issue_code:
            continue
        if per_code_cap > 0 and docs_by_code[issue_code] >= per_code_cap:
            continue

        terms = tokenize_for_mining(row["description"] or "")
        if not terms:
            continue

        unique_terms = set(terms)
        docs_by_code[issue_code] += 1
        total_docs += 1
        for term in unique_terms:
            global_df[term] += 1
            df_by_code[issue_code][term] += 1

        if total_docs and total_docs % 50000 == 0:
            print(f"  mined {total_docs} docs...")

    if total_docs == 0:
        raise RuntimeError("No documents processed for mining.")

    if replace_existing:
        conn.execute(
            "DELETE FROM topic_candidate_terms WHERE extractor_version = ?",
            (EXTRACTOR_VERSION,),
        )
        conn.commit()

    inserted = 0
    output: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now().isoformat(timespec="seconds")

    for issue_code, df_counter in df_by_code.items():
        issue_docs = docs_by_code[issue_code]
        rest_docs = max(total_docs - issue_docs, 1)
        ranked: list[dict[str, Any]] = []

        for term, doc_freq in df_counter.items():
            if doc_freq < min_doc_freq:
                continue
            if term in GENERIC_TERMS:
                continue
            if term.startswith("section "):
                continue

            rest_df = max(global_df[term] - doc_freq, 0)
            p_issue = (doc_freq + 1.0) / (issue_docs + 2.0)
            p_rest = (rest_df + 1.0) / (rest_docs + 2.0)
            lift = p_issue / p_rest
            if lift < min_lift:
                continue
            bounded_lift = min(lift, 250.0)
            score = math.log1p(bounded_lift) * math.log1p(doc_freq)
            ngram = 2 if " " in term else 1
            ranked.append(
                {
                    "term": term,
                    "ngram": ngram,
                    "doc_freq": int(doc_freq),
                    "issue_doc_count": int(issue_docs),
                    "corpus_doc_count": int(total_docs),
                    "lift": round(lift, 4),
                    "score": round(score, 4),
                }
            )

        ranked.sort(key=lambda item: (-item["score"], -item["doc_freq"], item["term"]))
        ranked = ranked[:top_k]
        output[issue_code] = ranked

        for item in ranked:
            conn.execute(
                """
                INSERT OR REPLACE INTO topic_candidate_terms
                    (issue_code, term, ngram, doc_freq, issue_doc_count, corpus_doc_count,
                     lift, score, extractor_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue_code,
                    item["term"],
                    item["ngram"],
                    item["doc_freq"],
                    item["issue_doc_count"],
                    item["corpus_doc_count"],
                    item["lift"],
                    item["score"],
                    EXTRACTOR_VERSION,
                    now,
                ),
            )
            inserted += 1
    conn.commit()

    out_path = Path("data") / f"topic_candidate_terms_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Mined {inserted} candidate terms across {len(output)} issue codes.")
    print(f"Wrote candidate dictionary file: {out_path}")
    return str(out_path)


def sample_unmapped(conn: sqlite3.Connection, limit: int, min_description_len: int, mode: str) -> None:
    where_mode = ""
    if mode == "processed_unmapped":
        where_mode = "AND r.activity_id IS NOT NULL AND r.topics = '[]'"
    elif mode == "unprocessed":
        where_mode = "AND r.activity_id IS NULL"
    elif mode == "any_unmapped":
        where_mode = "AND (r.activity_id IS NULL OR r.topics = '[]')"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    rows = conn.execute(
        f"""
        SELECT a.id, a.issue_code, SUBSTR(REPLACE(a.description, char(10), ' '), 1, 220) AS snippet
        FROM activities a
        LEFT JOIN activity_extractions_rules r ON r.activity_id = a.id
        WHERE a.description IS NOT NULL
          AND LENGTH(a.description) > ?
          {where_mode}
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (min_description_len, limit),
    ).fetchall()
    for row in rows:
        print(f"{row['id']}|{row['issue_code']}|{row['snippet']}")


def build_gap_report(
    conn: sqlite3.Connection,
    min_rows: int,
    max_codes: int,
    terms_per_code: int,
    snippets_per_code: int,
    min_description_len: int,
) -> str:
    per_code_rows = conn.execute(
        """
        WITH per_code AS (
            SELECT
                l0_issue_code AS issue_code,
                COUNT(*) AS n,
                SUM(CASE WHEN topics != '[]' THEN 1 ELSE 0 END) AS with_specific
            FROM activity_extractions_rules
            WHERE l0_issue_code IS NOT NULL AND l0_issue_code != ''
            GROUP BY l0_issue_code
        )
        SELECT issue_code, n, with_specific,
               ROUND(100.0 * with_specific / n, 3) AS pct_specific
        FROM per_code
        WHERE n >= ?
        ORDER BY pct_specific ASC, n DESC
        LIMIT ?
        """,
        (min_rows, max_codes),
    ).fetchall()

    report: dict[str, Any] = {
        "extractor_version": EXTRACTOR_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "min_rows": min_rows,
        "codes": [],
    }

    for row in per_code_rows:
        issue_code = row["issue_code"]
        candidates = conn.execute(
            """
            SELECT term, doc_freq, lift, score
            FROM topic_candidate_terms
            WHERE extractor_version = ?
              AND issue_code = ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (EXTRACTOR_VERSION, issue_code, terms_per_code),
        ).fetchall()

        snippets = conn.execute(
            """
            SELECT SUBSTR(REPLACE(a.description, char(10), ' '), 1, 220) AS snippet
            FROM activities a
            JOIN activity_extractions_rules r ON r.activity_id = a.id
            WHERE r.l0_issue_code = ?
              AND r.topics = '[]'
              AND a.description IS NOT NULL
              AND LENGTH(a.description) > ?
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (issue_code, min_description_len, snippets_per_code),
        ).fetchall()

        report["codes"].append(
            {
                "issue_code": issue_code,
                "activity_count": int(row["n"]),
                "with_specific_topic": int(row["with_specific"]),
                "specific_topic_pct": float(row["pct_specific"]),
                "candidate_terms": [
                    {
                        "term": candidate["term"],
                        "doc_freq": int(candidate["doc_freq"]),
                        "lift": round(float(candidate["lift"]), 4),
                        "score": round(float(candidate["score"]), 4),
                    }
                    for candidate in candidates
                ],
                "sample_unmapped_snippets": [snippet["snippet"] for snippet in snippets],
            }
        )

    out_path = Path("data") / f"rule_gap_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Wrote gap report: {out_path}")
    return str(out_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic rule-based topic extraction.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    extract = sub.add_parser("extract", help="Run rule-based extraction for a batch.")
    extract.add_argument("--rules-path", default=str(RULES_PATH))
    extract.add_argument("--batch-size", type=int, default=50000)
    extract.add_argument("--min-description-len", type=int, default=20)
    extract.add_argument("--refresh-existing", action="store_true")
    extract.add_argument(
        "--issue-codes",
        default="",
        help="Comma-separated issue codes to restrict extraction (e.g. CON,GAM,ART).",
    )

    stats = sub.add_parser("stats", help="Show extraction coverage and top topics.")
    stats.add_argument("--min-description-len", type=int, default=20)

    mine = sub.add_parser("mine-candidates", help="Mine candidate terms by issue_code.")
    mine.add_argument("--min-description-len", type=int, default=20)
    mine.add_argument("--per-code-cap", type=int, default=15000)
    mine.add_argument("--min-doc-freq", type=int, default=60)
    mine.add_argument("--min-lift", type=float, default=3.0)
    mine.add_argument("--top-k", type=int, default=40)
    mine.add_argument(
        "--scope",
        choices=["processed_unmapped", "unprocessed", "any_unmapped"],
        default="processed_unmapped",
        help="Which rows to mine from.",
    )
    mine.add_argument("--append", action="store_true")

    sample = sub.add_parser("sample-unmapped", help="Print random unmapped examples.")
    sample.add_argument("--limit", type=int, default=25)
    sample.add_argument("--min-description-len", type=int, default=20)
    sample.add_argument(
        "--mode",
        choices=["processed_unmapped", "unprocessed", "any_unmapped"],
        default="processed_unmapped",
    )

    gap = sub.add_parser("gap-report", help="Generate low-coverage issue-code report with candidate terms.")
    gap.add_argument("--min-rows", type=int, default=1000)
    gap.add_argument("--max-codes", type=int, default=25)
    gap.add_argument("--terms-per-code", type=int, default=15)
    gap.add_argument("--snippets-per-code", type=int, default=5)
    gap.add_argument("--min-description-len", type=int, default=20)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    conn = connect()
    init_tables(conn)

    if args.cmd == "extract":
        rules = load_rules(Path(args.rules_path))
        print(f"Loaded {len(rules)} topic rules from {args.rules_path}")
        issue_codes = {code.strip().upper() for code in args.issue_codes.split(",") if code.strip()}
        process_batch(
            conn=conn,
            rules=rules,
            batch_size=args.batch_size,
            min_description_len=args.min_description_len,
            refresh_existing=args.refresh_existing,
            issue_codes=issue_codes or None,
        )
        return

    if args.cmd == "stats":
        print_stats(conn, min_description_len=args.min_description_len)
        return

    if args.cmd == "mine-candidates":
        mine_candidates(
            conn=conn,
            min_description_len=args.min_description_len,
            per_code_cap=args.per_code_cap,
            min_doc_freq=args.min_doc_freq,
            min_lift=args.min_lift,
            top_k=args.top_k,
            scope=args.scope,
            replace_existing=not args.append,
        )
        return

    if args.cmd == "sample-unmapped":
        sample_unmapped(
            conn,
            limit=args.limit,
            min_description_len=args.min_description_len,
            mode=args.mode,
        )
        return

    if args.cmd == "gap-report":
        build_gap_report(
            conn,
            min_rows=args.min_rows,
            max_codes=args.max_codes,
            terms_per_code=args.terms_per_code,
            snippets_per_code=args.snippets_per_code,
            min_description_len=args.min_description_len,
        )
        return

    raise ValueError(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
