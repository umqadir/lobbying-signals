"""Enrich entities with metadata from the LDA API and LLM classification."""

import os
import time
import json
import httpx

from db import get_db, query_to_dicts
from llm import get_llm

API_BASE = "https://lda.senate.gov/api/v1"
LDA_API_KEY = os.getenv("LDA_API_KEY", "")
RATE_LIMIT_DELAY = 0.5 if LDA_API_KEY else 4.0

def get_headers():
    headers = {}
    if LDA_API_KEY:
        headers["Authorization"] = f"Token {LDA_API_KEY}"
    return headers


def fetch_client_details(client_sopr_id: str) -> dict | None:
    """Fetch client details from API."""
    try:
        resp = httpx.get(
            f"{API_BASE}/clients/{client_sopr_id}/",
            headers=get_headers(),
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Error fetching client {client_sopr_id}: {e}")
    return None


def enrich_clients_from_api(limit: int = None):
    """Pull general_description from API for all clients."""
    sql = """
        SELECT id, sopr_id, name FROM clients
        WHERE general_description IS NULL AND sopr_id IS NOT NULL
    """
    if limit:
        sql += f" LIMIT {limit}"

    with get_db() as conn:
        clients = query_to_dicts(conn, sql)

    print(f"Enriching {len(clients)} clients from API...")
    enriched = 0

    with get_db() as conn:
        for i, client in enumerate(clients):
            details = fetch_client_details(client["sopr_id"])
            if details and details.get("general_description"):
                conn.execute(
                    "UPDATE clients SET general_description = ?, state = ?, country = ? WHERE id = ?",
                    (details.get("general_description"), details.get("state"), details.get("country"), client["id"])
                )
                enriched += 1

            if (i + 1) % 100 == 0:
                conn.commit()
                print(f"  {i + 1}/{len(clients)} processed, {enriched} enriched")

            time.sleep(RATE_LIMIT_DELAY)

        conn.commit()

    print(f"Enriched {enriched} clients with descriptions")
    return enriched


def extract_topics_from_descriptions(batch_size: int = 50):
    """Use LLM to extract specific topics from activity descriptions."""
    llm = get_llm()

    # Get activities without extracted topics
    sql = """
        SELECT a.id, a.description, a.issue_code
        FROM activities a
        LEFT JOIN activity_topics t ON a.id = t.activity_id
        WHERE t.id IS NULL
          AND a.description IS NOT NULL
          AND LENGTH(a.description) > 20
        LIMIT ?
    """

    with get_db() as conn:
        # Create topics table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_topics (
                id INTEGER PRIMARY KEY,
                activity_id INTEGER REFERENCES activities(id),
                topic TEXT NOT NULL,
                confidence REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_topics_activity ON activity_topics(activity_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_topics_topic ON activity_topics(topic)")
        conn.commit()

        activities = query_to_dicts(conn, sql, (batch_size,))

    if not activities:
        print("No activities to process")
        return 0

    print(f"Extracting topics from {len(activities)} activities...")

    prompt_template = """Extract 1-3 specific policy topics from this lobbying activity description.
Return ONLY a JSON array of topic strings. Topics should be specific and searchable (e.g., "tariffs", "drug pricing", "AI regulation", "electric vehicles", "Section 230").
If no specific topics can be identified, return an empty array.

Description: {description}

JSON array of topics:"""

    extracted = 0
    with get_db() as conn:
        for i, activity in enumerate(activities):
            try:
                prompt = prompt_template.format(description=activity["description"][:1000])
                response = llm.model.generate_content(prompt)
                text = response.text.strip()

                # Parse JSON
                text = text.removeprefix("```json").removesuffix("```").strip()
                topics = json.loads(text)

                if isinstance(topics, list):
                    for topic in topics[:3]:  # Max 3 topics
                        if isinstance(topic, str) and len(topic) > 2:
                            conn.execute(
                                "INSERT INTO activity_topics (activity_id, topic, confidence) VALUES (?, ?, ?)",
                                (activity["id"], topic.lower().strip(), 0.8)
                            )
                            extracted += 1

                if (i + 1) % 10 == 0:
                    conn.commit()
                    print(f"  {i + 1}/{len(activities)} processed")

                time.sleep(0.1)  # Rate limit for Gemini

            except Exception as e:
                print(f"Error processing activity {activity['id']}: {e}")
                continue

        conn.commit()

    print(f"Extracted {extracted} topics")
    return extracted


def classify_unclassified_clients(batch_size: int = 50):
    """Use LLM to classify clients without descriptions."""
    llm = get_llm()

    sql = """
        SELECT id, name FROM clients
        WHERE (general_description IS NULL OR general_description = '')
          AND industry IS NULL
        LIMIT ?
    """

    with get_db() as conn:
        clients = query_to_dicts(conn, sql, (batch_size,))

    if not clients:
        print("No clients to classify")
        return 0

    print(f"Classifying {len(clients)} clients...")

    prompt_template = """Classify this organization into an industry sector based on its name.
Return ONLY a JSON object with "industry" (broad category) and "description" (1 sentence).

Organization name: {name}

JSON:"""

    classified = 0
    with get_db() as conn:
        for i, client in enumerate(clients):
            try:
                prompt = prompt_template.format(name=client["name"])
                response = llm.model.generate_content(prompt)
                text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
                result = json.loads(text)

                if result.get("industry"):
                    conn.execute(
                        "UPDATE clients SET industry = ?, general_description = ? WHERE id = ?",
                        (result.get("industry"), result.get("description"), client["id"])
                    )
                    classified += 1

                if (i + 1) % 10 == 0:
                    conn.commit()
                    print(f"  {i + 1}/{len(clients)} processed")

                time.sleep(0.1)

            except Exception as e:
                print(f"Error classifying client {client['id']}: {e}")
                continue

        conn.commit()

    print(f"Classified {classified} clients")
    return classified


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

        if cmd == "clients-api":
            enrich_clients_from_api(limit)
        elif cmd == "topics":
            extract_topics_from_descriptions(limit or 50)
        elif cmd == "classify-clients":
            classify_unclassified_clients(limit or 50)
        else:
            print(f"Unknown command: {cmd}")
    else:
        print("Usage:")
        print("  python 05_enrich.py clients-api [limit]  - Pull client descriptions from API")
        print("  python 05_enrich.py topics [batch]       - Extract topics from descriptions via LLM")
        print("  python 05_enrich.py classify-clients [batch] - Classify unnamed clients via LLM")
