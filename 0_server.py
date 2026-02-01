"""FastAPI server for lobbying signals API."""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import API_HOST, API_PORT, PROJECT_ROOT
from db import get_db, query_to_dicts, init_db

app = FastAPI(
    title="Lobbying Signals API",
    description="Detect and analyze newsworthy patterns in lobbying disclosure data",
    version="1.0.0"
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Routes

@app.get("/api/signals")
def list_signals(
    signal_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    year: Optional[int] = None,
    quarter: Optional[int] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0
):
    """List detected signals with optional filtering."""
    sql = "SELECT * FROM signals WHERE 1=1"
    params = []

    if signal_type:
        sql += " AND signal_type = ?"
        params.append(signal_type)
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    if year:
        sql += " AND year = ?"
        params.append(year)
    if quarter:
        sql += " AND quarter = ?"
        params.append(quarter)

    sql += " ORDER BY magnitude_score DESC, created_at DESC"
    sql += f" LIMIT {limit} OFFSET {offset}"

    with get_db() as conn:
        signals = query_to_dicts(conn, sql, tuple(params))

    return {"signals": signals, "count": len(signals)}


@app.get("/api/signals/{signal_id}")
def get_signal(signal_id: int):
    """Get a single signal with full details."""
    with get_db() as conn:
        signals = query_to_dicts(conn, "SELECT * FROM signals WHERE id = ?", (signal_id,))

    if not signals:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal = signals[0]

    # Get additional context
    context = get_signal_context(signal)
    signal["context"] = context

    return signal


def get_signal_context(signal: dict) -> dict:
    """Gather context for a signal (moved from 04_narrate for API use)."""
    context = {}

    with get_db() as conn:
        if signal["entity_type"] == "issue":
            context["top_registrants"] = query_to_dicts(conn, """
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

            context["top_clients"] = query_to_dicts(conn, """
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

    return context


@app.get("/api/entities/registrants")
def list_registrants(
    search: Optional[str] = None,
    limit: int = Query(default=50, le=500)
):
    """List registrants with optional search."""
    sql = """
        SELECT r.*, SUM(f.income) as total_income, COUNT(DISTINCT f.id) as filing_count
        FROM registrants r
        LEFT JOIN filings f ON r.id = f.registrant_id
    """
    params = []

    if search:
        sql += " WHERE r.name LIKE ?"
        params.append(f"%{search}%")

    sql += " GROUP BY r.id ORDER BY total_income DESC"
    sql += f" LIMIT {limit}"

    with get_db() as conn:
        registrants = query_to_dicts(conn, sql, tuple(params))

    return {"registrants": registrants}


@app.get("/api/entities/registrants/{registrant_id}")
def get_registrant(registrant_id: int):
    """Get registrant profile with history."""
    with get_db() as conn:
        registrant = query_to_dicts(
            conn, "SELECT * FROM registrants WHERE id = ?", (registrant_id,)
        )
        if not registrant:
            raise HTTPException(status_code=404, detail="Registrant not found")

        registrant = registrant[0]

        # Quarterly totals
        registrant["quarterly_totals"] = query_to_dicts(conn, """
            SELECT year, quarter, SUM(income) as total_income, COUNT(*) as filing_count
            FROM filings WHERE registrant_id = ?
            GROUP BY year, quarter
            ORDER BY year, quarter
        """, (registrant_id,))

        # Top clients
        registrant["top_clients"] = query_to_dicts(conn, """
            SELECT c.id, c.name, SUM(f.income) as total_income
            FROM filings f
            JOIN clients c ON f.client_id = c.id
            WHERE f.registrant_id = ?
            GROUP BY c.id
            ORDER BY total_income DESC
            LIMIT 10
        """, (registrant_id,))

        # Top issues
        registrant["top_issues"] = query_to_dicts(conn, """
            SELECT i.issue_label, COUNT(*) as count
            FROM filings f
            JOIN activities a ON a.filing_id = f.id
            JOIN issues i ON i.activity_id = a.id
            WHERE f.registrant_id = ?
            GROUP BY i.issue_label
            ORDER BY count DESC
            LIMIT 10
        """, (registrant_id,))

    return registrant


@app.get("/api/entities/clients")
def list_clients(
    search: Optional[str] = None,
    limit: int = Query(default=50, le=500)
):
    """List clients with optional search."""
    sql = """
        SELECT c.*, SUM(f.income) as total_spending, COUNT(DISTINCT f.id) as filing_count
        FROM clients c
        LEFT JOIN filings f ON c.id = f.client_id
    """
    params = []

    if search:
        sql += " WHERE c.name LIKE ?"
        params.append(f"%{search}%")

    sql += " GROUP BY c.id ORDER BY total_spending DESC"
    sql += f" LIMIT {limit}"

    with get_db() as conn:
        clients = query_to_dicts(conn, sql, tuple(params))

    return {"clients": clients}


@app.get("/api/entities/clients/{client_id}")
def get_client(client_id: int):
    """Get client profile with history."""
    with get_db() as conn:
        client = query_to_dicts(
            conn, "SELECT * FROM clients WHERE id = ?", (client_id,)
        )
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        client = client[0]

        # Quarterly totals
        client["quarterly_totals"] = query_to_dicts(conn, """
            SELECT year, quarter, SUM(income) as total_spending, COUNT(*) as filing_count
            FROM filings WHERE client_id = ?
            GROUP BY year, quarter
            ORDER BY year, quarter
        """, (client_id,))

        # Registrants hired
        client["registrants"] = query_to_dicts(conn, """
            SELECT r.id, r.name, SUM(f.income) as total_paid
            FROM filings f
            JOIN registrants r ON f.registrant_id = r.id
            WHERE f.client_id = ?
            GROUP BY r.id
            ORDER BY total_paid DESC
            LIMIT 10
        """, (client_id,))

        # Issues lobbied
        client["issues"] = query_to_dicts(conn, """
            SELECT i.issue_label, COUNT(*) as count
            FROM filings f
            JOIN activities a ON a.filing_id = f.id
            JOIN issues i ON i.activity_id = a.id
            WHERE f.client_id = ?
            GROUP BY i.issue_label
            ORDER BY count DESC
            LIMIT 10
        """, (client_id,))

    return client


@app.get("/api/trends/{issue_label}")
def get_issue_trend(issue_label: str):
    """Get time series for an issue."""
    with get_db() as conn:
        trend = query_to_dicts(conn, """
            SELECT f.year, f.quarter,
                   SUM(f.income / (SELECT COUNT(*) FROM activities WHERE filing_id = f.id)) as allocated_income,
                   COUNT(DISTINCT f.client_id) as client_count,
                   COUNT(DISTINCT f.registrant_id) as registrant_count
            FROM issues i
            JOIN activities a ON i.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            WHERE i.issue_label = ?
            GROUP BY f.year, f.quarter
            ORDER BY f.year, f.quarter
        """, (issue_label,))

        top_registrants = query_to_dicts(conn, """
            SELECT r.name, SUM(f.income) as total_income
            FROM issues i
            JOIN activities a ON i.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            JOIN registrants r ON f.registrant_id = r.id
            WHERE i.issue_label = ?
            GROUP BY r.id
            ORDER BY total_income DESC
            LIMIT 10
        """, (issue_label,))

        top_clients = query_to_dicts(conn, """
            SELECT c.name, SUM(f.income) as total_spending
            FROM issues i
            JOIN activities a ON i.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            JOIN clients c ON f.client_id = c.id
            WHERE i.issue_label = ?
            GROUP BY c.id
            ORDER BY total_spending DESC
            LIMIT 10
        """, (issue_label,))

    return {
        "issue": issue_label,
        "trend": trend,
        "top_registrants": top_registrants,
        "top_clients": top_clients
    }


@app.get("/api/issues")
def list_issues():
    """List all issues with totals."""
    with get_db() as conn:
        issues = query_to_dicts(conn, """
            SELECT i.issue_label,
                   COUNT(*) as activity_count,
                   COUNT(DISTINCT f.client_id) as client_count,
                   COUNT(DISTINCT f.registrant_id) as registrant_count
            FROM issues i
            JOIN activities a ON i.activity_id = a.id
            JOIN filings f ON a.filing_id = f.id
            GROUP BY i.issue_label
            ORDER BY activity_count DESC
        """)

    return {"issues": issues}


@app.post("/api/generate-narrative/{signal_id}")
def generate_narrative_endpoint(signal_id: int):
    """Generate or regenerate narrative for a signal."""
    from llm import get_llm

    with get_db() as conn:
        signals = query_to_dicts(conn, "SELECT * FROM signals WHERE id = ?", (signal_id,))

    if not signals:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal = signals[0]
    context = get_signal_context(signal)

    llm = get_llm()
    narrative = llm.generate_narrative(signal, context)

    with get_db() as conn:
        conn.execute("UPDATE signals SET narrative = ? WHERE id = ?", (narrative, signal_id))
        conn.commit()

    return {"narrative": narrative}


@app.get("/api/stats")
def get_stats():
    """Get overall statistics."""
    with get_db() as conn:
        stats = {
            "total_filings": query_to_dicts(conn, "SELECT COUNT(*) as count FROM filings")[0]["count"],
            "total_registrants": query_to_dicts(conn, "SELECT COUNT(*) as count FROM registrants")[0]["count"],
            "total_clients": query_to_dicts(conn, "SELECT COUNT(*) as count FROM clients")[0]["count"],
            "total_activities": query_to_dicts(conn, "SELECT COUNT(*) as count FROM activities")[0]["count"],
            "classified_activities": query_to_dicts(conn, "SELECT COUNT(DISTINCT activity_id) as count FROM issues")[0]["count"],
            "total_signals": query_to_dicts(conn, "SELECT COUNT(*) as count FROM signals")[0]["count"],
            "signals_by_type": query_to_dicts(conn, "SELECT signal_type, COUNT(*) as count FROM signals GROUP BY signal_type"),
            "latest_period": query_to_dicts(conn, "SELECT MAX(year) as year, MAX(quarter) as quarter FROM filings WHERE year = (SELECT MAX(year) FROM filings)")[0],
        }

    return stats


# Static file serving
static_dir = PROJECT_ROOT / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def root():
    """Serve the dashboard."""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Lobbying Signals API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    init_db()
    uvicorn.run(app, host=API_HOST, port=API_PORT)
