"""Lean iterative issue-dictionary loop using Gemini 3 Flash only.

What it does:
1) Optionally snapshot the DB so prior extraction outputs are preserved
2) Build a phrase -> issue dictionary in `issue_dictionary_loop`
3) Apply dictionary phrases to all activities into `issue_matches_loop`
4) Sample uncovered activities and ask Gemini 3 Flash for new phrases
5) Repeat until budget or target coverage
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from config import DB_PATH


MODEL = "gemini-3-flash-preview"
PRICE_INPUT_PER_M = 0.30
PRICE_OUTPUT_PER_M = 2.50

SEED_PHRASES = [
    ("artificial intelligence", "artificial_intelligence"),
    ("generative ai", "artificial_intelligence"),
    ("machine learning", "artificial_intelligence"),
    ("large language model", "artificial_intelligence"),
    ("tariff", "tariff"),
    ("import duty", "tariff"),
    ("section 301", "tariff"),
    ("anti dumping", "trade_enforcement"),
    ("trade agreement", "trade_agreements"),
    ("export control", "export_controls"),
    ("farm bill", "farm_policy"),
    ("crop insurance", "farm_policy"),
    ("agricultural subsidy", "farm_policy"),
    ("snap benefits", "nutrition_assistance"),
    ("appropriations", "appropriations"),
    ("defense authorization", "defense_authorization"),
]

BANNED = {
    "policy",
    "program",
    "funding",
    "issues",
    "legislation",
    "regulation",
    "federal policy",
    "federal government",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS issue_dictionary_loop (
            phrase TEXT PRIMARY KEY,
            issue_label TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS issue_matches_loop (
            activity_id INTEGER NOT NULL,
            issue_label TEXT NOT NULL,
            phrase TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (activity_id, issue_label, phrase)
        );

        CREATE INDEX IF NOT EXISTS idx_issue_matches_loop_activity
        ON issue_matches_loop(activity_id);
        """
    )
    conn.commit()


def snapshot_db(snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = snapshot_dir / f"filings_pre_issue_loop_{ts}.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(out_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out_path


def sanitize_phrase(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip().lower())
    text = re.sub(r"[\"'`]", "", text)
    return text


def sanitize_label(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_ ]+", " ", (value or "").strip().lower())
    text = re.sub(r"\s+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return text[:48]


def estimate_cost(prompt_tokens: int, output_tokens: int) -> float:
    return (prompt_tokens / 1_000_000) * PRICE_INPUT_PER_M + (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_M


def coverage(conn: sqlite3.Connection, min_len: int) -> tuple[int, int, float]:
    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM activities
        WHERE description IS NOT NULL
          AND LENGTH(description) > ?
        """,
        (min_len,),
    ).fetchone()[0]
    covered = conn.execute(
        """
        SELECT COUNT(DISTINCT m.activity_id)
        FROM issue_matches_loop m
        JOIN activities a ON a.id = m.activity_id
        WHERE a.description IS NOT NULL
          AND LENGTH(a.description) > ?
        """,
        (min_len,),
    ).fetchone()[0]
    pct = (covered / total) if total else 0.0
    return total, covered, pct


def phrase_hits(conn: sqlite3.Connection, phrase: str, min_len: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*)
        FROM activities
        WHERE description IS NOT NULL
          AND LENGTH(description) > ?
          AND INSTR(LOWER(description), ?) > 0
        """,
        (min_len, phrase),
    ).fetchone()[0]


def apply_phrases(conn: sqlite3.Connection, phrases: list[tuple[str, str]], min_len: int) -> int:
    total_added = 0
    for phrase, issue in phrases:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO issue_matches_loop (activity_id, issue_label, phrase)
            SELECT a.id, ?, ?
            FROM activities a
            WHERE a.description IS NOT NULL
              AND LENGTH(a.description) > ?
              AND INSTR(LOWER(a.description), ?) > 0
            """,
            (issue, phrase, min_len, phrase),
        )
        total_added += conn.total_changes - before
    conn.commit()
    return total_added


def add_seed_dictionary(conn: sqlite3.Connection) -> int:
    added = 0
    for phrase_raw, issue_raw in SEED_PHRASES:
        phrase = sanitize_phrase(phrase_raw)
        issue = sanitize_label(issue_raw)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO issue_dictionary_loop (phrase, issue_label, source)
            VALUES (?, ?, 'seed')
            """,
            (phrase, issue),
        )
        if cur.rowcount:
            added += 1
    conn.commit()
    return added


def existing_phrases(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT phrase FROM issue_dictionary_loop").fetchall()
    return {row["phrase"] for row in rows}


def sample_uncovered(conn: sqlite3.Connection, sample_size: int, min_len: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT a.id, a.issue_code, a.description
        FROM activities a
        WHERE a.description IS NOT NULL
          AND LENGTH(a.description) > ?
          AND NOT EXISTS (
              SELECT 1 FROM issue_matches_loop m WHERE m.activity_id = a.id
          )
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (min_len, sample_size),
    ).fetchall()
    return rows


def build_prompt(samples: list[sqlite3.Row], existing: set[str], max_additions: int) -> str:
    sample_lines = []
    for row in samples:
        desc = (row["description"] or "").replace("\n", " ").strip()
        if len(desc) > 320:
            desc = desc[:320] + "..."
        sample_lines.append(f'- id={row["id"]} issue_code={row["issue_code"] or ""}: {desc}')

    existing_short = list(sorted(existing))[-200:]
    return f"""Create phrase dictionary additions for lobbying activity text.

Requirements:
- Return at most {max_additions} additions
- phrase: specific reusable substring, 3-60 chars
- issue_label: snake_case
- Avoid generic phrases like policy/program/funding/legislation
- Do not repeat existing phrases

Return JSON only:
{{
  "items": [
    {{"phrase":"...", "issue_label":"..."}}
  ]
}}

Existing phrases:
{json.dumps(existing_short, ensure_ascii=True)}

Uncovered samples:
{chr(10).join(sample_lines)}
"""


def parse_items(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    obj = json.loads(text)
    if isinstance(obj, dict) and isinstance(obj.get("items"), list):
        return [item for item in obj["items"] if isinstance(item, dict)]
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]
    return []


def filter_and_insert(
    conn: sqlite3.Connection,
    items: list[dict[str, Any]],
    existing: set[str],
    min_hits: int,
    max_additions: int,
    min_len: int,
) -> list[tuple[str, str]]:
    accepted: list[tuple[str, str, int]] = []
    seen = set(existing)
    for item in items:
        phrase = sanitize_phrase(str(item.get("phrase", "")))
        issue = sanitize_label(str(item.get("issue_label", "")))
        if not phrase or not issue:
            continue
        if phrase in seen or phrase in BANNED:
            continue
        if len(phrase) < 3 or len(phrase) > 60:
            continue
        hits = phrase_hits(conn, phrase, min_len)
        if hits < min_hits:
            continue
        accepted.append((phrase, issue, hits))
        seen.add(phrase)

    accepted.sort(key=lambda row: row[2], reverse=True)
    accepted = accepted[:max_additions]

    out: list[tuple[str, str]] = []
    for phrase, issue, _hits in accepted:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO issue_dictionary_loop (phrase, issue_label, source)
            VALUES (?, ?, 'llm')
            """,
            (phrase, issue),
        )
        if cur.rowcount:
            out.append((phrase, issue))
    conn.commit()
    return out


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.model != MODEL:
        raise ValueError(f"Only `{MODEL}` is supported")

    conn = connect()
    init_tables(conn)

    snapshot_path = None
    if args.snapshot_db:
        snapshot_path = str(snapshot_db(Path(args.snapshot_dir)))
        print(f"[snapshot] {snapshot_path}")

    if args.reset:
        conn.execute("DELETE FROM issue_matches_loop")
        conn.execute("DELETE FROM issue_dictionary_loop")
        conn.commit()
        print("[reset] cleared loop tables")

    seed_added = add_seed_dictionary(conn)
    print(f"[seed] added={seed_added}")

    all_phrases = conn.execute(
        "SELECT phrase, issue_label FROM issue_dictionary_loop"
    ).fetchall()
    initial_applied = apply_phrases(conn, [(r["phrase"], r["issue_label"]) for r in all_phrases], args.min_description_len)
    total, covered, pct = coverage(conn, args.min_description_len)
    print(f"[start] coverage={covered}/{total} ({pct*100:.2f}%) applied={initial_applied}")

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY or GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    total_prompt = 0
    total_output = 0
    spent = 0.0
    iterations = 0
    status = "budget_exhausted"

    for i in range(1, args.max_iterations + 1):
        total, covered, pct = coverage(conn, args.min_description_len)
        if pct >= args.target_coverage:
            status = "target_reached"
            break
        if spent >= args.budget_usd:
            status = "budget_exhausted"
            break

        samples = sample_uncovered(conn, args.sample_size, args.min_description_len)
        if not samples:
            status = "no_uncovered"
            break

        existing = existing_phrases(conn)
        prompt = build_prompt(samples, existing, args.max_additions_per_iter)
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=args.temperature,
                response_mime_type="application/json",
            ),
        )

        usage = response.usage_metadata
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        iter_cost = estimate_cost(prompt_tokens, output_tokens)
        total_prompt += prompt_tokens
        total_output += output_tokens
        spent += iter_cost

        try:
            items = parse_items(response.text or "")
        except Exception:
            items = []

        additions = filter_and_insert(
            conn=conn,
            items=items,
            existing=existing,
            min_hits=args.min_hits,
            max_additions=args.max_additions_per_iter,
            min_len=args.min_description_len,
        )

        applied = apply_phrases(conn, additions, args.min_description_len) if additions else 0
        total, covered, pct = coverage(conn, args.min_description_len)
        iterations = i

        print(
            f"[iter {i:02d}] samples={len(samples)} raw={len(items)} "
            f"additions={len(additions)} applied={applied} "
            f"coverage={pct*100:.2f}% cost=${iter_cost:.4f} total=${spent:.4f}"
        )

        if not additions and args.stop_on_no_additions:
            status = "no_additions"
            break
    else:
        status = "max_iterations"

    total, covered, pct = coverage(conn, args.min_description_len)
    top_issues = conn.execute(
        """
        SELECT issue_label, COUNT(DISTINCT activity_id) AS n
        FROM issue_matches_loop
        GROUP BY issue_label
        ORDER BY n DESC
        LIMIT 20
        """
    ).fetchall()

    summary = {
        "model": MODEL,
        "status": status,
        "iterations": iterations,
        "budget_usd": args.budget_usd,
        "spent_usd": round(spent, 6),
        "prompt_tokens": total_prompt,
        "output_tokens": total_output,
        "coverage_total": total,
        "coverage_assigned": covered,
        "coverage_pct": round(pct * 100, 3),
        "target_coverage_pct": round(args.target_coverage * 100, 3),
        "snapshot_db": snapshot_path,
        "top_issues": [{"issue_label": row["issue_label"], "count": int(row["n"])} for row in top_issues],
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path("data") / f"issue_loop_summary_{ts}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[summary] {out_path}")
    print(f"[done] coverage={pct*100:.2f}% spent=${spent:.4f} status={status}")
    return summary


def estimate() -> None:
    # Rough planning scenarios, not run output.
    rows = [
        ("light", 120, 40, 0.17),
        ("medium", 220, 80, 0.45),
        ("heavy", 350, 140, 1.10),
    ]
    print(f"model={MODEL}")
    print("scenario|sample_size|iterations|est_cost_usd")
    for name, sample_size, iterations, est in rows:
        print(f"{name}|{sample_size}|{iterations}|{est:.2f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean issue-dictionary loop (Gemini 3 Flash only).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run iterative loop")
    run_p.add_argument("--model", default=MODEL, choices=[MODEL])
    run_p.add_argument("--budget-usd", type=float, default=2.0)
    run_p.add_argument("--target-coverage", type=float, default=0.95)
    run_p.add_argument("--sample-size", type=int, default=220)
    run_p.add_argument("--max-additions-per-iter", type=int, default=10)
    run_p.add_argument("--min-hits", type=int, default=40)
    run_p.add_argument("--min-description-len", type=int, default=20)
    run_p.add_argument("--max-iterations", type=int, default=120)
    run_p.add_argument("--temperature", type=float, default=0.15)
    run_p.add_argument("--reset", action="store_true")
    run_p.add_argument("--snapshot-db", action="store_true", default=True)
    run_p.add_argument("--no-snapshot-db", dest="snapshot_db", action="store_false")
    run_p.add_argument("--snapshot-dir", default="data/archives")
    run_p.add_argument("--stop-on-no-additions", action="store_true", default=True)
    run_p.add_argument("--keep-going-on-no-additions", dest="stop_on_no_additions", action="store_false")

    sub.add_parser("estimate", help="Print rough spend scenarios")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "estimate":
        estimate()
        return
    if args.cmd == "run":
        run(args)
        return
    raise ValueError(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
