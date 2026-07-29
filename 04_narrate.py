"""Narrative generation for publishable lobbying signal briefs."""

from db import get_db, query_to_dicts
from llm import get_llm


def get_signal_context(signal: dict) -> dict:
    """Gather additional context for narrative generation."""
    context = {}

    with get_db() as conn:
        # Get top players for this entity
        if signal["entity_type"] == "issue":
            # Top registrants for this issue
            top_registrants = query_to_dicts(conn, """
                SELECT r.name, SUM(f.income) as total_income
                FROM issues i
                JOIN activities a ON i.activity_id = a.id
                JOIN filings f ON a.filing_id = f.id
                JOIN registrants r ON f.registrant_id = r.id
                WHERE i.issue_label = ?
                  AND f.year = ? AND f.quarter = ?
                GROUP BY r.id
                ORDER BY total_income DESC
                LIMIT 5
            """, (signal["entity_id"], signal["year"], signal["quarter"]))
            context["top_registrants"] = top_registrants

            # Top clients for this issue
            top_clients = query_to_dicts(conn, """
                SELECT c.name, SUM(f.income) as total_income
                FROM issues i
                JOIN activities a ON i.activity_id = a.id
                JOIN filings f ON a.filing_id = f.id
                JOIN clients c ON f.client_id = c.id
                WHERE i.issue_label = ?
                  AND f.year = ? AND f.quarter = ?
                GROUP BY c.id
                ORDER BY total_income DESC
                LIMIT 5
            """, (signal["entity_id"], signal["year"], signal["quarter"]))
            context["top_clients"] = top_clients

            # Historical trend
            trend = query_to_dicts(conn, """
                SELECT f.year, f.quarter,
                       SUM(f.income / (SELECT COUNT(*) FROM activities WHERE filing_id = f.id)) as allocated_income
                FROM issues i
                JOIN activities a ON i.activity_id = a.id
                JOIN filings f ON a.filing_id = f.id
                WHERE i.issue_label = ?
                GROUP BY f.year, f.quarter
                ORDER BY f.year, f.quarter
            """, (signal["entity_id"],))
            context["historical_trend"] = trend[-8:]  # Last 2 years

        elif signal["entity_type"] == "registrant":
            # Top clients for this registrant
            top_clients = query_to_dicts(conn, """
                SELECT c.name, SUM(f.income) as total_income
                FROM filings f
                JOIN clients c ON f.client_id = c.id
                WHERE f.registrant_id = ?
                  AND f.year = ? AND f.quarter = ?
                GROUP BY c.id
                ORDER BY total_income DESC
                LIMIT 5
            """, (int(signal["entity_id"]), signal["year"], signal["quarter"]))
            context["top_clients"] = top_clients

            # Top issues for this registrant
            top_issues = query_to_dicts(conn, """
                SELECT i.issue_label, COUNT(*) as activity_count
                FROM filings f
                JOIN activities a ON a.filing_id = f.id
                JOIN issues i ON i.activity_id = a.id
                WHERE f.registrant_id = ?
                  AND f.year = ? AND f.quarter = ?
                GROUP BY i.issue_label
                ORDER BY activity_count DESC
                LIMIT 5
            """, (int(signal["entity_id"]), signal["year"], signal["quarter"]))
            context["top_issues"] = top_issues

        elif signal["entity_type"] == "client":
            # Registrants hired by this client
            registrants = query_to_dicts(conn, """
                SELECT r.name, SUM(f.income) as total_income
                FROM filings f
                JOIN registrants r ON f.registrant_id = r.id
                WHERE f.client_id = ?
                  AND f.year = ? AND f.quarter = ?
                GROUP BY r.id
                ORDER BY total_income DESC
                LIMIT 5
            """, (int(signal["entity_id"]), signal["year"], signal["quarter"]))
            context["registrants_hired"] = registrants

    return context


def generate_narrative(signal: dict) -> str:
    """Generate a publishable narrative for a signal."""
    context = get_signal_context(signal)
    llm = get_llm()
    return llm.generate_narrative(signal, context)


def update_signal_narrative(signal_id: int, narrative: str):
    """Update signal with generated narrative."""
    with get_db() as conn:
        conn.execute(
            "UPDATE signals SET narrative = ? WHERE id = ?",
            (narrative, signal_id)
        )
        conn.commit()


def generate_all_narratives(limit: int = None):
    """Generate narratives for signals that don't have one."""
    sql = """
        SELECT * FROM signals
        WHERE narrative IS NULL
        ORDER BY magnitude_score DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    with get_db() as conn:
        signals = query_to_dicts(conn, sql)

    print(f"Generating narratives for {len(signals)} signals...")

    for i, signal in enumerate(signals):
        try:
            narrative = generate_narrative(signal)
            update_signal_narrative(signal["id"], narrative)
            print(f"[{i+1}/{len(signals)}] Generated narrative for {signal['entity_name']}")
        except Exception as e:
            print(f"Error generating narrative for signal {signal['id']}: {e}")

    print("Done")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
        generate_all_narratives(limit=limit)
    else:
        generate_all_narratives()
