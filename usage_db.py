# usage_db.py
"""
Usage logging for the Procurement Gateway.

Records every analysis request and sole-source letter evaluation to a
local SQLite database so operational costs and usage patterns can be
reviewed without any external service.

Database location
─────────────────
  Production (Railway volume):  /data/procurement.db (one volume per tenant service)
  Local dev:                    data/<TENANT>/procurement.db
  Override:                     DATA_DIR environment variable

Tables
──────
  analysis_log     — every /analyze call
  sole_source_log  — every /api/analyze-sole-source call

Both tables gain an emailed flag + recipient address when the user
sends the report, letting you see how often results are acted on.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager

_TENANT = os.getenv("TENANT", "palm-springs")
_DATA_DIR = (
    os.getenv("DATA_DIR")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", _TENANT)
)
_DB_PATH = os.path.join(_DATA_DIR, "procurement.db")

# ── Connection helper ─────────────────────────────────────────────

@contextmanager
def _conn():
    os.makedirs(_DATA_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Schema ────────────────────────────────────────────────────────

def init() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS analysis_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    DEFAULT (datetime('now')),
            -- Requester
            requester_name      TEXT,
            requester_email     TEXT,
            department          TEXT,
            -- Purchase details
            item_name           TEXT,
            item_type           TEXT,
            amount              REAL,
            account_code        TEXT,
            description         TEXT,
            process_timing      TEXT,
            required_date       TEXT,
            -- Policy flags
            pcard               TEXT,
            sole_source         TEXT,
            federal_funds       TEXT,
            faa_governed        TEXT,
            -- Outcome
            verdict             TEXT,
            procurement_method  TEXT,
            approval_role       TEXT,
            -- Email
            report_emailed      INTEGER DEFAULT 0,
            report_emailed_to   TEXT
        );

        CREATE TABLE IF NOT EXISTS sole_source_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    DEFAULT (datetime('now')),
            -- File
            filename            TEXT,
            -- Outcome
            strength            TEXT,
            ready_to_submit     INTEGER DEFAULT 0,
            recommendation      TEXT,
            -- Email
            report_emailed      INTEGER DEFAULT 0,
            report_emailed_to   TEXT
        );

        CREATE TABLE IF NOT EXISTS magic_tokens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            token      TEXT    UNIQUE NOT NULL,
            email      TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            expires_at TEXT    NOT NULL,
            used       INTEGER DEFAULT 0
        );
        """)
    try:
        with _conn() as con:
            con.execute("ALTER TABLE sole_source_log ADD COLUMN requester_email TEXT")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE analysis_log ADD COLUMN required_date TEXT")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE analysis_log ADD COLUMN process_timing TEXT")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE analysis_log ADD COLUMN report_json TEXT")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE sole_source_log ADD COLUMN report_json TEXT")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE analysis_log ADD COLUMN archived INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE sole_source_log ADD COLUMN archived INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        with _conn() as con:
            con.execute("ALTER TABLE sole_source_log ADD COLUMN file_blob BLOB")
    except Exception:
        pass


# ── Magic token helpers ───────────────────────────────────────────

def create_magic_token(email: str) -> str:
    import datetime
    import secrets
    token = secrets.token_urlsafe(32)
    expires = (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=15)
    ).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO magic_tokens (token, email, expires_at) VALUES (?,?,?)",
            (token, email, expires),
        )
    return token


def consume_magic_token(token: str) -> str | None:
    """Validate and consume a magic link token. Returns email or None."""
    import datetime
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT email, expires_at, used FROM magic_tokens WHERE token=?",
                (token,),
            ).fetchone()
            if not row or row["used"]:
                return None
            if datetime.datetime.now(datetime.UTC).isoformat() > row["expires_at"]:
                return None
            con.execute("UPDATE magic_tokens SET used=1 WHERE token=?", (token,))
            return row["email"]
    except Exception as e:
        print(f"[usage_db] consume_magic_token failed: {e}", flush=True)
        return None


# ── Analysis logging ──────────────────────────────────────────────

def log_analysis(data: dict, result: dict) -> int:
    """
    Insert a row for a completed /analyze call.
    Returns the new row id so the caller can later mark it as emailed.
    """
    chain  = result.get("approval_chain") or []
    signer = next((s for s in chain if s.get("approves")), None)
    approval_role = signer["role"] if signer else None
    report_json = json.dumps({"data": data, "result": result})

    try:
        with _conn() as con:
            cur = con.execute(
                """
                INSERT INTO analysis_log
                    (requester_name, requester_email, department,
                     item_name, item_type, amount, account_code, description,
                     process_timing, required_date,
                     pcard, sole_source, federal_funds, faa_governed,
                     verdict, procurement_method, approval_role, report_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data.get("requester_name"),
                    data.get("requester_email"),
                    data.get("department"),
                    data.get("item_name"),
                    data.get("item_type"),
                    float(data.get("amount") or 0),
                    data.get("account_code"),
                    data.get("description"),
                    data.get("process_timing"),
                    data.get("required_date"),
                    data.get("pcard"),
                    data.get("sole_source"),
                    data.get("federal_funds"),
                    data.get("faa_governed"),
                    result.get("verdict"),
                    result.get("procurement_method"),
                    approval_role,
                    report_json,
                ),
            )
            return cur.lastrowid or 0
    except Exception as e:
        print(f"[usage_db] log_analysis failed: {e}", flush=True)
        return 0


def mark_analysis_emailed(log_id: int, email: str) -> None:
    """Mark an analysis log row as emailed."""
    if not log_id:
        return
    try:
        with _conn() as con:
            con.execute(
                "UPDATE analysis_log SET report_emailed=1, report_emailed_to=? WHERE id=?",
                (email, log_id),
            )
    except Exception as e:
        print(f"[usage_db] mark_analysis_emailed failed: {e}", flush=True)


# ── Sole source logging ───────────────────────────────────────────

def log_sole_source(filename: str, result: dict, email: str = "", file_bytes: bytes | None = None) -> int:
    """
    Insert a row for a completed /api/analyze-sole-source call.
    Returns the new row id.
    """
    try:
        with _conn() as con:
            cur = con.execute(
                """
                INSERT INTO sole_source_log
                    (filename, strength, ready_to_submit, recommendation, requester_email, report_json, file_blob)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    filename,
                    result.get("strength"),
                    1 if result.get("ready_to_submit") else 0,
                    result.get("recommendation"),
                    email,
                    json.dumps({"filename": filename, "result": result}),
                    file_bytes,
                ),
            )
            return cur.lastrowid or 0
    except Exception as e:
        print(f"[usage_db] log_sole_source failed: {e}", flush=True)
        return 0


def get_sole_source_file(log_id: int) -> tuple[str, bytes] | None:
    """Return (filename, file_bytes) for a stored sole source letter, or None."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT filename, file_blob FROM sole_source_log WHERE id=?",
                (log_id,),
            ).fetchone()
            if not row or row["file_blob"] is None:
                return None
            return row["filename"], row["file_blob"]
    except Exception as e:
        print(f"[usage_db] get_sole_source_file failed: {e}", flush=True)
        return None


def mark_sole_source_emailed(log_id: int, email: str) -> None:
    """Mark a sole source log row as emailed."""
    if not log_id:
        return
    try:
        with _conn() as con:
            con.execute(
                "UPDATE sole_source_log SET report_emailed=1, report_emailed_to=? WHERE id=?",
                (email, log_id),
            )
    except Exception as e:
        print(f"[usage_db] mark_sole_source_emailed failed: {e}", flush=True)


# ── Archiving ──────────────────────────────────────────────────────

def set_analysis_archived(log_id: int, archived: bool) -> None:
    """Archive or unarchive an analysis log row."""
    if not log_id:
        return
    try:
        with _conn() as con:
            con.execute(
                "UPDATE analysis_log SET archived=? WHERE id=?",
                (1 if archived else 0, log_id),
            )
    except Exception as e:
        print(f"[usage_db] set_analysis_archived failed: {e}", flush=True)


def set_sole_source_archived(log_id: int, archived: bool) -> None:
    """Archive or unarchive a sole source log row."""
    if not log_id:
        return
    try:
        with _conn() as con:
            con.execute(
                "UPDATE sole_source_log SET archived=? WHERE id=?",
                (1 if archived else 0, log_id),
            )
    except Exception as e:
        print(f"[usage_db] set_sole_source_archived failed: {e}", flush=True)


# ── Summary and detail queries ────────────────────────────────────

def monthly_summary() -> list[dict]:
    """
    Return per-month counts for quick operational review.
    Each row: { month, analyses, sole_source_reviews, emails_sent }
    """
    try:
        with _conn() as con:
            rows = con.execute("""
                SELECT
                    strftime('%Y-%m', created_at) AS month,
                    COUNT(*)                      AS analyses,
                    SUM(report_emailed)           AS emails_sent
                FROM analysis_log
                GROUP BY month
                ORDER BY month DESC
            """).fetchall()
            analyses = [dict(r) for r in rows]

            rows = con.execute("""
                SELECT
                    strftime('%Y-%m', created_at) AS month,
                    COUNT(*)                      AS sole_source_reviews
                FROM sole_source_log
                GROUP BY month
                ORDER BY month DESC
            """).fetchall()
            ss = {r["month"]: r["sole_source_reviews"] for r in rows}

            for a in analyses:
                a["sole_source_reviews"] = ss.get(a["month"], 0)
            return analyses
    except Exception as e:
        print(f"[usage_db] monthly_summary failed: {e}", flush=True)
        return []


def recent_analyses(limit: int = 250) -> list[dict]:
    """Return the most recent analysis log rows, newest first."""
    try:
        with _conn() as con:
            rows = con.execute(
                """
                SELECT id, created_at, department, item_name, item_type,
                       amount, process_timing, required_date, verdict, procurement_method, approval_role,
                       sole_source, faa_governed, federal_funds,
                       report_emailed, report_emailed_to, report_json, archived
                FROM   analysis_log
                ORDER  BY id DESC
                LIMIT  ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[usage_db] recent_analyses failed: {e}", flush=True)
        return []


def recent_sole_source(limit: int = 100) -> list[dict]:
    """Return the most recent sole source log rows, newest first."""
    try:
        with _conn() as con:
            rows = con.execute(
                """
                SELECT id, created_at, filename, strength,
                       ready_to_submit, recommendation,
                       report_emailed, report_emailed_to, requester_email, report_json, archived,
                       (file_blob IS NOT NULL) AS has_file
                FROM   sole_source_log
                ORDER  BY id DESC
                LIMIT  ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[usage_db] recent_sole_source failed: {e}", flush=True)
        return []


def totals() -> dict:
    """Return lifetime totals for dashboard header cards."""
    try:
        with _conn() as con:
            a = dict(con.execute("""
                SELECT COUNT(*) AS total_analyses,
                       SUM(report_emailed) AS total_emailed,
                       SUM(CASE WHEN verdict='APPROVED' THEN 1 ELSE 0 END) AS approved,
                       SUM(CASE WHEN verdict='FLAGGED'  THEN 1 ELSE 0 END) AS flagged,
                       SUM(CASE WHEN verdict='RETURNED' THEN 1 ELSE 0 END) AS returned
                FROM   analysis_log
            """).fetchone())
            ss = dict(con.execute("""
                SELECT COUNT(*) AS total_ss,
                       SUM(ready_to_submit) AS ss_ready
                FROM   sole_source_log
            """).fetchone())
            return {**a, **ss}
    except Exception as e:
        print(f"[usage_db] totals failed: {e}", flush=True)
        return {}
