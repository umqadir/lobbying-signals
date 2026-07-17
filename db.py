"""Database schema and helpers for lobbying data."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from config import DB_PATH, DATA_DIR

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = """
-- Lobbying firms (registrants)
CREATE TABLE IF NOT EXISTS registrants (
    id INTEGER PRIMARY KEY,
    sopr_id TEXT UNIQUE,
    name TEXT NOT NULL,
    normalized_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Clients being represented
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    sopr_id TEXT UNIQUE,
    name TEXT NOT NULL,
    normalized_name TEXT,
    industry TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Core filing metadata
CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY,
    sopr_filing_id TEXT UNIQUE,
    registrant_id INTEGER REFERENCES registrants(id),
    client_id INTEGER REFERENCES clients(id),
    year INTEGER NOT NULL,
    quarter INTEGER NOT NULL,
    income REAL,
    expenses REAL,
    filing_date TEXT,
    filing_type TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Lobbying activities per filing
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    filing_id INTEGER REFERENCES filings(id),
    description TEXT,
    issue_code TEXT,  -- LDA standard code (e.g., "TRD" for trade)
    houses_lobbied TEXT,  -- "H", "S", or "HS"
    agencies TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- LLM-classified granular issues
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    activity_id INTEGER REFERENCES activities(id),
    issue_label TEXT NOT NULL,  -- From taxonomy (e.g., "tariffs")
    confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Detected anomaly signals
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    signal_type TEXT NOT NULL,  -- "record", "spike", "concentration", "new_entrant", "coordinated"
    entity_type TEXT,  -- "issue", "registrant", "client"
    entity_id TEXT,    -- The specific entity (issue label or db id)
    entity_name TEXT,  -- Human-readable name
    metric TEXT,       -- What was measured (e.g., "quarterly_income")
    current_value REAL,
    prior_value REAL,
    growth_rate REAL,
    historical_pct REAL,  -- Percentile rank in history
    magnitude_score REAL,  -- Importance weighting
    quarter INTEGER,
    year INTEGER,
    narrative TEXT,
    published INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_filings_year_quarter ON filings(year, quarter);
CREATE INDEX IF NOT EXISTS idx_filings_filing_date ON filings(filing_date);
CREATE INDEX IF NOT EXISTS idx_filings_registrant ON filings(registrant_id);
CREATE INDEX IF NOT EXISTS idx_filings_client ON filings(client_id);
CREATE INDEX IF NOT EXISTS idx_filings_reg_client_year_quarter
    ON filings(registrant_id, client_id, year, quarter);
CREATE INDEX IF NOT EXISTS idx_activities_filing ON activities(filing_id);
CREATE INDEX IF NOT EXISTS idx_issues_activity ON issues(activity_id);
CREATE INDEX IF NOT EXISTS idx_issues_label ON issues(issue_label);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_year_quarter ON signals(year, quarter);
"""


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection):
    """Idempotent ALTER TABLEs for columns/indexes added after initial release.

    Guarded by PRAGMA table_info so this is a no-op on a DB that already has
    them (fresh DBs get the columns straight from SCHEMA above) and safe to
    run on every init_db() call, including in CI.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(filings)")}
    if "filing_type" not in cols:
        conn.execute("ALTER TABLE filings ADD COLUMN filing_type TEXT")
    if "is_current" not in cols:
        conn.execute("ALTER TABLE filings ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_filings_reg_client_year_quarter "
        "ON filings(registrant_id, client_id, year, quarter)"
    )


def init_db():
    """Initialize database with schema."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)
        conn.commit()


def normalize_name(name: str) -> str:
    """Normalize entity names for matching."""
    if not name:
        return ""
    # Remove common suffixes
    suffixes = [
        ", Inc.", ", Inc", " Inc.", " Inc",
        ", LLC", " LLC",
        ", L.L.C.", " L.L.C.",
        ", Corp.", ", Corp", " Corp.", " Corp",
        ", Corporation", " Corporation",
        ", LLP", " LLP",
        ", L.P.", " L.P.", ", LP", " LP",
        ", Co.", ", Co", " Co.", " Co",
        ", Ltd.", ", Ltd", " Ltd.", " Ltd",
        ", P.C.", " P.C.",
        ", PLLC", " PLLC",
    ]
    normalized = name.strip()
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    return normalized.strip().upper()


def get_or_create_registrant(conn: sqlite3.Connection, sopr_id: str, name: str) -> int:
    """Get existing registrant or create new one."""
    cur = conn.execute("SELECT id FROM registrants WHERE sopr_id = ?", (sopr_id,))
    row = cur.fetchone()
    if row:
        return row["id"]

    normalized = normalize_name(name)
    cur = conn.execute(
        "INSERT INTO registrants (sopr_id, name, normalized_name) VALUES (?, ?, ?)",
        (sopr_id, name, normalized)
    )
    conn.commit()
    return cur.lastrowid


def get_or_create_client(conn: sqlite3.Connection, sopr_id: str, name: str, industry: str = None) -> int:
    """Get existing client or create new one."""
    cur = conn.execute("SELECT id FROM clients WHERE sopr_id = ?", (sopr_id,))
    row = cur.fetchone()
    if row:
        return row["id"]

    normalized = normalize_name(name)
    cur = conn.execute(
        "INSERT INTO clients (sopr_id, name, normalized_name, industry) VALUES (?, ?, ?, ?)",
        (sopr_id, name, normalized, industry)
    )
    conn.commit()
    return cur.lastrowid


def insert_filing(
    conn: sqlite3.Connection,
    sopr_filing_id: str,
    registrant_id: int,
    client_id: int,
    year: int,
    quarter: int,
    income: float = None,
    expenses: float = None,
    filing_date: str = None,
    filing_type: str = None
) -> int:
    """Insert a filing, returning the id. Skips if already exists.

    New rows default is_current=1 (schema default); callers that ingest
    amendment/termination filings must run recompute_is_current() afterward
    so supersede semantics stay correct across the touched report period.
    """
    cur = conn.execute("SELECT id FROM filings WHERE sopr_filing_id = ?", (sopr_filing_id,))
    row = cur.fetchone()
    if row:
        return row["id"]

    cur = conn.execute(
        """INSERT INTO filings
           (sopr_filing_id, registrant_id, client_id, year, quarter, income, expenses, filing_date, filing_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sopr_filing_id, registrant_id, client_id, year, quarter, income, expenses, filing_date, filing_type)
    )
    conn.commit()
    return cur.lastrowid


def recompute_is_current(conn: sqlite3.Connection, year: int = None, quarter: int = None):
    """Recompute the is_current flag for every (registrant_id, client_id,
    year, quarter) group: the group's latest filing (by filing_date, then id
    as tiebreak) gets is_current=1, every other filing in the group gets 0.

    Pass year/quarter to scope the recompute to a single touched report
    period (cheap — the normal post-ingest path). Omit both to recompute the
    entire table in one pass (used for historical backfill / one-off repair).
    """
    where = ""
    params: tuple = ()
    if year is not None and quarter is not None:
        where = "WHERE year = ? AND quarter = ?"
        params = (year, quarter)

    conn.execute(f"UPDATE filings SET is_current = 0 {where}", params)
    conn.execute(
        f"""
        UPDATE filings SET is_current = 1
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY registrant_id, client_id, year, quarter
                           ORDER BY filing_date DESC, id DESC
                       ) AS rn
                FROM filings
                {where}
            )
            WHERE rn = 1
        )
        """,
        params,
    )
    conn.commit()


def insert_activity(
    conn: sqlite3.Connection,
    filing_id: int,
    description: str,
    issue_code: str = None,
    houses_lobbied: str = None,
    agencies: str = None
) -> int:
    """Insert a lobbying activity."""
    cur = conn.execute(
        """INSERT INTO activities (filing_id, description, issue_code, houses_lobbied, agencies)
           VALUES (?, ?, ?, ?, ?)""",
        (filing_id, description, issue_code, houses_lobbied, agencies)
    )
    conn.commit()
    return cur.lastrowid


def insert_issue(conn: sqlite3.Connection, activity_id: int, issue_label: str, confidence: float) -> int:
    """Insert an LLM-classified issue."""
    cur = conn.execute(
        "INSERT INTO issues (activity_id, issue_label, confidence) VALUES (?, ?, ?)",
        (activity_id, issue_label, confidence)
    )
    conn.commit()
    return cur.lastrowid


def insert_signal(
    conn: sqlite3.Connection,
    signal_type: str,
    entity_type: str,
    entity_id: str,
    entity_name: str,
    metric: str,
    current_value: float,
    prior_value: float,
    growth_rate: float,
    historical_pct: float,
    magnitude_score: float,
    quarter: int,
    year: int,
    narrative: str = None
) -> int:
    """Insert a detected signal."""
    cur = conn.execute(
        """INSERT INTO signals
           (signal_type, entity_type, entity_id, entity_name, metric, current_value, prior_value,
            growth_rate, historical_pct, magnitude_score, quarter, year, narrative)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_type, entity_type, entity_id, entity_name, metric, current_value, prior_value,
         growth_rate, historical_pct, magnitude_score, quarter, year, narrative)
    )
    conn.commit()
    return cur.lastrowid


def query_to_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """Execute query and return list of dicts."""
    cur = conn.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
