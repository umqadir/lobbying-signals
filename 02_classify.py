"""LLM classification of lobbying activity descriptions into granular issues."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import ALL_ISSUE_LABELS
from db import get_db, insert_issue, query_to_dicts
from llm import get_llm


def get_unclassified_activities(limit: int = 1000) -> list[dict]:
    """Get activities that haven't been classified yet."""
    sql = """
        SELECT a.id, a.description, a.issue_code
        FROM activities a
        LEFT JOIN issues i ON a.id = i.activity_id
        WHERE i.id IS NULL
          AND a.description IS NOT NULL
          AND a.description != ''
        LIMIT ?
    """
    with get_db() as conn:
        return query_to_dicts(conn, sql, (limit,))


def classify_activity(llm, activity: dict) -> list[dict]:
    """Classify a single activity, returning list of issues."""
    description = activity["description"]

    # Skip very short descriptions
    if len(description) < 20:
        return []

    try:
        issues = llm.extract_issues(description)
        return [
            {"activity_id": activity["id"], **issue}
            for issue in issues
            if issue.get("label") in ALL_ISSUE_LABELS
        ]
    except Exception as e:
        print(f"Error classifying activity {activity['id']}: {e}")
        return []


def classify_batch(activities: list[dict], llm=None, max_workers: int = 5):
    """Classify a batch of activities with rate limiting."""
    if llm is None:
        llm = get_llm()

    classified = 0
    errors = 0

    with get_db() as conn:
        for activity in activities:
            try:
                issues = classify_activity(llm, activity)

                for issue in issues:
                    insert_issue(
                        conn,
                        issue["activity_id"],
                        issue["label"],
                        issue.get("confidence", 0.8)
                    )

                classified += 1

                # Rate limiting: ~10 requests per second
                time.sleep(0.1)

                if classified % 100 == 0:
                    print(f"Classified {classified}/{len(activities)}")

            except Exception as e:
                errors += 1
                print(f"Error: {e}")
                continue

    return classified, errors


def classify_all(batch_size: int = 1000):
    """Classify all unclassified activities."""
    llm = get_llm()
    total_classified = 0

    while True:
        activities = get_unclassified_activities(limit=batch_size)
        if not activities:
            break

        print(f"Processing batch of {len(activities)} activities...")
        classified, errors = classify_batch(activities, llm=llm)
        total_classified += classified

        print(f"Batch complete: {classified} classified, {errors} errors")

    print(f"Total classified: {total_classified}")
    return total_classified


def get_classification_stats() -> dict:
    """Get statistics on classifications."""
    with get_db() as conn:
        total_activities = query_to_dicts(
            conn, "SELECT COUNT(*) as count FROM activities WHERE description IS NOT NULL"
        )[0]["count"]

        classified_activities = query_to_dicts(
            conn, "SELECT COUNT(DISTINCT activity_id) as count FROM issues"
        )[0]["count"]

        issues_by_label = query_to_dicts(
            conn,
            """SELECT issue_label, COUNT(*) as count, AVG(confidence) as avg_confidence
               FROM issues GROUP BY issue_label ORDER BY count DESC"""
        )

    return {
        "total_activities": total_activities,
        "classified_activities": classified_activities,
        "classification_rate": classified_activities / total_activities if total_activities > 0 else 0,
        "issues_by_label": issues_by_label
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--stats":
        stats = get_classification_stats()
        print(f"Total activities: {stats['total_activities']}")
        print(f"Classified: {stats['classified_activities']} ({stats['classification_rate']:.1%})")
        print("\nTop issues:")
        for issue in stats["issues_by_label"][:20]:
            print(f"  {issue['issue_label']}: {issue['count']} (avg conf: {issue['avg_confidence']:.2f})")
    else:
        print("Classifying activities...")
        classify_all()
