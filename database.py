# database.py
import sqlite3
import os
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.getenv("DATA_DIR", _BASE_DIR)
DB_PATH = os.path.join(_DATA_DIR, "procurement.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── Staff ────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            title       TEXT,
            department  TEXT,
            email       TEXT,
            phone       TEXT,
            role        TEXT,
            active      INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── Stage definitions ────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS stage_definitions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            stage_code   TEXT UNIQUE NOT NULL,
            stage_name   TEXT NOT NULL,
            description  TEXT,
            sla_days     INTEGER DEFAULT 5,
            sequence     INTEGER,
            active       INTEGER DEFAULT 1
        )
    """)

    # ── Requests (includes drafts) ───────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id               TEXT UNIQUE,
            draft_name                TEXT,
            status                    TEXT DEFAULT 'draft',
            current_stage             TEXT DEFAULT 'draft',
            requester_name            TEXT,
            requester_email           TEXT,
            requester_phone           TEXT,
            department                TEXT,
            account_code              TEXT,
            item_name                 TEXT,
            item_type                 TEXT,
            amount                    REAL,
            description               TEXT,
            sole_source               TEXT DEFAULT 'no',
            vendor_name               TEXT,
            sole_source_justification TEXT,
            emergency                 TEXT DEFAULT 'no',
            emergency_description     TEXT,
            federal_funds             TEXT DEFAULT 'no',
            pcard                     TEXT DEFAULT 'no',
            delivery_method           TEXT DEFAULT 'shipped',
            bid_path                  INTEGER DEFAULT 0,
            bid_process_sla_days      INTEGER,
            ai_verdict                TEXT,
            ai_procurement_method     TEXT,
            ai_summary                TEXT,
            ai_full_response          TEXT,
            hr_required               INTEGER DEFAULT 0,
            hr_classification         TEXT,
            it_approval_required      INTEGER DEFAULT 0,
            engineering_required      INTEGER DEFAULT 0,
            attorney_required         INTEGER DEFAULT 0,
            spc_required              INTEGER DEFAULT 0,
            delete_requested          INTEGER DEFAULT 0,
            delete_reason             TEXT,
            created_at                TEXT DEFAULT (datetime('now')),
            submitted_at              TEXT,
            updated_at                TEXT DEFAULT (datetime('now')),
            completed_at              TEXT
        )
    """)

    # ── Assignments ──────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id       INTEGER NOT NULL,
            stage_code       TEXT NOT NULL,
            assigned_to_id   INTEGER,
            assigned_to_name TEXT,
            assigned_at      TEXT DEFAULT (datetime('now')),
            due_at           TEXT,
            completed_at     TEXT,
            action_taken     TEXT,
            notes            TEXT,
            overdue_notified INTEGER DEFAULT 0,
            FOREIGN KEY (request_id) REFERENCES requests(id),
            FOREIGN KEY (assigned_to_id) REFERENCES staff(id)
        )
    """)

    # ── Attachments (draft + active) ─────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id   INTEGER NOT NULL,
            filename     TEXT NOT NULL,
            filepath     TEXT,
            file_size    INTEGER,
            status        TEXT DEFAULT 'draft',
            document_type TEXT DEFAULT 'other',
            uploaded_by   TEXT,
            uploaded_at   TEXT DEFAULT (datetime('now')),
            stage_added   TEXT,
            FOREIGN KEY (request_id) REFERENCES requests(id)
        )
    """)

    # ── Audit log ────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id   INTEGER NOT NULL,
            tracking_id  TEXT,
            stage_code   TEXT,
            action       TEXT NOT NULL,
            actor_name   TEXT,
            actor_role   TEXT,
            notes        TEXT,
            timestamp    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (request_id) REFERENCES requests(id)
        )
    """)

    # ── Notifications ────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id   INTEGER NOT NULL,
            recipient    TEXT,
            subject      TEXT,
            body         TEXT,
            sent_at      TEXT DEFAULT (datetime('now')),
            sent_by      TEXT,
            FOREIGN KEY (request_id) REFERENCES requests(id)
        )
    """)

    # ── RFQ templates ────────────────────────────────────────
    # Master list of the four document templates.
    # filepath is relative to the project root templates/rfq/ folder.
    c.execute("""
        CREATE TABLE IF NOT EXISTS rfq_templates (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT UNIQUE NOT NULL,
            name          TEXT NOT NULL,
            document_type TEXT NOT NULL,
            filepath      TEXT NOT NULL,
            active        INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now'))
        )
    """)

    # ── RFQ drafts ───────────────────────────────────────────
    # One row per RFQ being built. Always linked to a request.
    # Generated Word doc is stored in the attachments table, not here.
    c.execute("""
        CREATE TABLE IF NOT EXISTS rfq_drafts (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        INTEGER NOT NULL,
            template_code     TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'draft',
            created_by_email  TEXT,
            conversation_json TEXT,
            field_data_json   TEXT,
            scope_draft       TEXT,
            scope_approved    TEXT,
            line_items_json   TEXT,
            warranty          TEXT,
            notes             TEXT,
            created_at        TEXT DEFAULT (datetime('now')),
            updated_at        TEXT DEFAULT (datetime('now')),
            generated_at      TEXT,
            FOREIGN KEY (request_id)    REFERENCES requests(id),
            FOREIGN KEY (template_code) REFERENCES rfq_templates(code)
        )
    """)

    # ── RFQ validation results ───────────────────────────────
    # Issues flagged by the AI or rules engine during review.
    # Procurement staff resolves these before approving.
    c.execute("""
        CREATE TABLE IF NOT EXISTS rfq_validation_results (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            rfq_draft_id INTEGER NOT NULL,
            severity     TEXT NOT NULL,
            field_ref    TEXT,
            message      TEXT NOT NULL,
            resolved     INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (rfq_draft_id) REFERENCES rfq_drafts(id)
        )
    """)

    conn.commit()

    # ── Seed stage definitions ───────────────────────────────
    c.execute("SELECT COUNT(*) FROM stage_definitions")
    if c.fetchone()[0] == 0:
        # SLA days sourced from procurement_config.json — not hardcoded
        from procurement_config import cfg as _cfg
        s = _cfg.sla_days  # shorthand
        stages = [
            # (stage_code, stage_name, description, sla_days, sequence)
            ("draft",                "Draft",
             "Saved but not yet submitted",                    s("draft"), 0),
            ("procurement_review",   "Procurement Review",
             "Initial review and tracking ID assignment",      s("procurement_review"), 1),
            ("hr_union_review",      "HR / Union Review",
             "HR review for potential union labor overlap",    s("hr_union_review"), 2),
            ("it_review",            "IT Director Review",
             "Required for IT equipment and software",         s("it_review"), 3),
            ("engineering_review",   "City Engineer Review",
             "Required for construction projects",             s("engineering_review"), 4),
            ("attorney_review",      "City Attorney Review",
             "Required for all contracts — see procurement policy",
                                                               s("attorney_review"), 5),
            ("spc_review",           "Special Program Compliance",
             "Required when federal funds are involved",       s("spc_review"), 6),
            ("contract_development", "Contract Development",
             "Procurement prepares contract using approved templates",
                                                               s("contract_development"), 7),
            ("bid_development",      "Bid Development",
             "Procurement prepares bid specs, IFB or RFP documents",
                                                               s("bid_development"), 8),
            ("bid_process",          "Bid Process",
             "Public advertisement, bid receipt and evaluation",
                                                               s("bid_process"), 9),
            ("bid_award",            "Bid Award",
             "Procurement scores bids and selects vendor",     s("bid_award"), 10),
            ("bid_contract",         "Contract Development (Post-Bid)",
             "Procurement prepares contract with selected vendor",
                                                               s("bid_contract"), 11),
            ("director_approval",    "Department Director Approval",
             "Approval per signing authority matrix",          s("director_approval"), 12),
            ("manager_approval",     "City Manager Approval",
             "Required when amount exceeds director authority",s("manager_approval"), 13),
            ("acm_approval",         "ACM Approval",
             "Assistant City Manager approval",                s("acm_approval"), 14),
            ("council_approval",     "City Council Approval",
             "Required for large public/non-public projects",  s("council_approval"), 15),
            ("complete",             "Complete",
             "Request fully approved and processed",           s("complete"), 16),
            ("rejected",             "Rejected",
             "Request rejected",                               s("rejected"), 17),
            ("returned_to_requester","Returned to Requester",
             "Request returned for additional information",    s("returned_to_requester"), 18),
        ]
        c.executemany("""
            INSERT INTO stage_definitions
                (stage_code, stage_name, description, sla_days, sequence)
            VALUES (?,?,?,?,?)
        """, stages)
        conn.commit()

    conn.close()
    print(f"Database initialized: {DB_PATH}")
    _migrate_db()


def _migrate_db():
    """
    Apply incremental data/schema migrations to existing databases.
    Safe to run on every startup — all operations are idempotent.
    Add a new numbered block here for every future schema change.
    """
    conn = get_db()
    c = conn.cursor()

    # M-001: add returned_to_requester to stage_definitions if missing
    c.execute(
        "SELECT COUNT(*) FROM stage_definitions "
        "WHERE stage_code='returned_to_requester'"
    )
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO stage_definitions
                (stage_code, stage_name, description, sla_days, sequence)
            VALUES (
                'returned_to_requester',
                'Returned to Requester',
                'Request returned for additional information',
                0, 18
            )
        """)

    # M-002: seed rfq_templates if table exists but is empty
    try:
        c.execute("SELECT COUNT(*) FROM rfq_templates")
        if c.fetchone()[0] == 0:
            templates = [
                ("IQR_SERVICES", "IQR - Services",  "IQR",
                 "templates/rfq/iqr_services.docx"),
                ("IQR_SUPPLIES", "IQR - Supplies",  "IQR",
                 "templates/rfq/iqr_supplies.docx"),
                ("RFP_GENERAL",  "RFP - General",   "RFP",
                 "templates/rfq/rfp_general.docx"),
                ("RFP_AVIATION", "RFP - Aviation",  "RFP",
                 "templates/rfq/rfp_aviation.docx"),
            ]
            c.executemany("""
                INSERT INTO rfq_templates
                    (code, name, document_type, filepath)
                VALUES (?, ?, ?, ?)
            """, templates)
    except Exception:
        pass  # table not yet created on very old DBs — init_db handles it

    # M-003: add delivery_method column to requests if missing
    try:
        c.execute("ALTER TABLE requests ADD COLUMN delivery_method TEXT DEFAULT 'shipped'")
        print("M-003: added delivery_method column")
    except Exception:
        pass  # column already exists

    # M-004: add document_type column to attachments if missing
    try:
        c.execute("ALTER TABLE attachments ADD COLUMN document_type TEXT DEFAULT 'other'")
        print("M-004: added document_type column to attachments")
    except Exception:
        pass  # column already exists

    conn.commit()
    conn.close()


# ── Request / Draft operations ───────────────────────────────

def create_or_update_draft(form_data: dict,
                            request_id: int | None = None) -> int:
    """
    Save a draft. If request_id provided, update existing.
    Otherwise create new. Returns request ID.
    """
    conn = get_db()
    c = conn.cursor()

    fields = {
        "draft_name":                form_data.get("draft_name") or
                                     form_data.get("item_name") or
                                     "Unnamed",
        "requester_name":            form_data.get("requester_name"),
        "requester_email":           form_data.get(
                                         "requester_email", "").lower().strip(),
        "requester_phone":           form_data.get("phone"),
        "department":                form_data.get("department"),
        "account_code":              form_data.get("account_code"),
        "item_name":                 form_data.get("item_name"),
        "item_type":                 form_data.get("item_type"),
        "amount":                    form_data.get("amount"),
        "description":               form_data.get("description"),
        "sole_source":               form_data.get("sole_source", "no"),
        "vendor_name":               form_data.get("vendor_name"),
        "sole_source_justification": form_data.get(
                                         "sole_source_justification"),
        "emergency":                 form_data.get("emergency", "no"),
        "emergency_description":     form_data.get("emergency_description"),
        "federal_funds":             form_data.get("federal_funds", "no"),
        "pcard":                     form_data.get("pcard", "no"),
        "delivery_method":           form_data.get("delivery_method", "shipped"),
    }

    if request_id:
        c.execute("""
            UPDATE requests SET
                draft_name=:draft_name,
                requester_name=:requester_name,
                requester_email=:requester_email,
                requester_phone=:requester_phone,
                department=:department,
                account_code=:account_code,
                item_name=:item_name,
                item_type=:item_type,
                amount=:amount,
                description=:description,
                sole_source=:sole_source,
                vendor_name=:vendor_name,
                sole_source_justification=:sole_source_justification,
                emergency=:emergency,
                emergency_description=:emergency_description,
                federal_funds=:federal_funds,
                pcard=:pcard,
                delivery_method=:delivery_method,
                updated_at=datetime('now')
            WHERE id=:id AND status IN ('draft','returned_to_requester', 'pending_procurement')
        """, {**fields, "id": request_id})
        conn.commit()
        conn.close()
        return request_id
    else:
        c.execute("""
            INSERT INTO requests (
                draft_name, status, current_stage,
                requester_name, requester_email, requester_phone,
                department, account_code, item_name, item_type,
                amount, description, sole_source, vendor_name,
                sole_source_justification, emergency,
                emergency_description, federal_funds, pcard,
                delivery_method
            ) VALUES (
                :draft_name, 'draft', 'draft',
                :requester_name, :requester_email, :requester_phone,
                :department, :account_code, :item_name, :item_type,
                :amount, :description, :sole_source, :vendor_name,
                :sole_source_justification, :emergency,
                :emergency_description, :federal_funds, :pcard,
                :delivery_method
            )
        """, fields)
        new_id = c.lastrowid
        if new_id is None:
            raise ValueError("Failed to create draft")
        conn.commit()
        conn.close()
        return new_id


def promote_draft_to_request(request_id: int,
                              ai_result: dict) -> bool:
    """
    Mark a draft as pending_procurement after AI approval.
    Records submitted_at timestamp and promotes attachments.
    """
    conn = get_db()
    conn.execute("""
        UPDATE requests SET
            status='pending_procurement',
            current_stage='procurement_review',
            submitted_at=datetime('now'),
            ai_verdict=?,
            ai_procurement_method=?,
            ai_summary=?,
            ai_full_response=?,
            updated_at=datetime('now')
        WHERE id=?
    """, (
        ai_result.get("verdict"),
        ai_result.get("procurement_method"),
        ai_result.get("summary"),
        str(ai_result),
        request_id
    ))
    # Promote attachments from draft to active
    conn.execute("""
        UPDATE attachments SET status='active'
        WHERE request_id=? AND status='draft'
    """, (request_id,))
    conn.commit()
    conn.close()
    return True


def return_request_to_draft(request_id: int,
                             ai_result: dict) -> bool:
    """Return a request to draft status with AI feedback preserved."""
    conn = get_db()
    conn.execute("""
        UPDATE requests SET
            status='returned_to_requester',
            current_stage='draft',
            ai_verdict=?,
            ai_procurement_method=?,
            ai_summary=?,
            ai_full_response=?,
            updated_at=datetime('now')
        WHERE id=?
    """, (
        ai_result.get("verdict"),
        ai_result.get("procurement_method"),
        ai_result.get("summary"),
        str(ai_result),
        request_id
    ))
    conn.commit()
    conn.close()
    return True


def get_drafts_by_email(email: str) -> list:
    """Get all requests for a requester email ordered by status priority."""
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*,
               COUNT(a.id) AS attachment_count
        FROM requests r
        LEFT JOIN attachments a ON a.request_id = r.id
        WHERE LOWER(r.requester_email) = ?
        GROUP BY r.id
        ORDER BY
            CASE r.status
                WHEN 'returned_to_requester' THEN 1
                WHEN 'draft'                 THEN 2
                WHEN 'pending_procurement'   THEN 3
                WHEN 'in_workflow'           THEN 4
                WHEN 'complete'              THEN 5
                WHEN 'rejected'              THEN 6
                ELSE 7
            END,
            r.updated_at DESC
    """, (email.lower().strip(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_drafts_by_department(department: str) -> dict:
    """Get all requests for a department split by draft/active."""
    conn = get_db()

    drafts = conn.execute("""
        SELECT r.*, COUNT(a.id) AS attachment_count
        FROM requests r
        LEFT JOIN attachments a ON a.request_id = r.id
        WHERE r.department = ?
          AND r.status IN ('draft', 'returned_to_requester')
        GROUP BY r.id
        ORDER BY r.updated_at DESC
    """, (department,)).fetchall()

    active = conn.execute("""
        SELECT r.*,
               s.stage_name,
               asn.assigned_to_name,
               asn.due_at,
               CAST((julianday('now') - julianday(asn.assigned_at))
                    AS INTEGER) AS days_in_stage
        FROM requests r
        LEFT JOIN stage_definitions s ON r.current_stage = s.stage_code
        LEFT JOIN assignments asn ON (
            asn.request_id = r.id AND
            asn.completed_at IS NULL AND
            asn.stage_code = r.current_stage
        )
        WHERE r.department = ?
          AND r.status NOT IN ('draft', 'returned_to_requester')
        ORDER BY r.created_at DESC
    """, (department,)).fetchall()

    conn.close()
    return {
        "drafts":   [dict(d) for d in drafts],
        "requests": [dict(r) for r in active],
    }


def create_request(form_data: dict, ai_result: dict) -> int:
    """
    Promote an existing draft to a formal request.
    If no request_id in form_data, creates a new record directly.
    Returns request ID.
    """
    request_id = form_data.get("request_id")

    if request_id:
        promote_draft_to_request(int(request_id), ai_result)
        return int(request_id)

    # No existing draft — create fresh request record
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO requests (
            draft_name, status, current_stage,
            requester_name, requester_email, requester_phone,
            department, account_code, item_name, item_type,
            amount, description, sole_source, vendor_name,
            sole_source_justification, emergency,
            emergency_description, federal_funds, pcard,
            delivery_method, ai_verdict, ai_procurement_method,
            ai_summary, ai_full_response,
            submitted_at
        ) VALUES (
            :draft_name, 'pending_procurement', 'procurement_review',
            :requester_name, :requester_email, :requester_phone,
            :department, :account_code, :item_name, :item_type,
            :amount, :description, :sole_source, :vendor_name,
            :sole_source_justification, :emergency,
            :emergency_description, :federal_funds, :pcard,
            :delivery_method, :ai_verdict, :ai_procurement_method,
            :ai_summary, :ai_full_response,
            datetime('now')
        )
    """, {
        "draft_name":                form_data.get("item_name", "Unnamed"),
        "requester_name":            form_data.get("requester_name"),
        "requester_email":           form_data.get(
                                         "requester_email", "").lower().strip(),
        "requester_phone":           form_data.get("phone"),
        "department":                form_data.get("department"),
        "account_code":              form_data.get("account_code"),
        "item_name":                 form_data.get("item_name"),
        "item_type":                 form_data.get("item_type"),
        "amount":                    form_data.get("amount"),
        "description":               form_data.get("description"),
        "sole_source":               form_data.get("sole_source", "no"),
        "vendor_name":               form_data.get("vendor_name"),
        "sole_source_justification": form_data.get(
                                         "sole_source_justification"),
        "emergency":                 form_data.get("emergency", "no"),
        "emergency_description":     form_data.get("emergency_description"),
        "federal_funds":             form_data.get("federal_funds", "no"),
        "pcard":                     form_data.get("pcard", "no"),
        "delivery_method":           form_data.get("delivery_method", "shipped"),
        "ai_verdict":                ai_result.get("verdict"),
        "ai_procurement_method":     ai_result.get("procurement_method"),
        "ai_summary":                ai_result.get("summary"),
        "ai_full_response":          str(ai_result),
    })
    new_id = c.lastrowid
    if new_id is None:
        raise ValueError("Failed to create request")
    conn.commit()
    conn.close()
    return new_id


def request_deletion(request_id: int,
                     reason: str | None = None) -> bool:
    """Flag a request for deletion/cancellation by requester."""
    conn = get_db()
    conn.execute("""
        UPDATE requests SET
            delete_requested=1,
            delete_reason=?,
            updated_at=datetime('now')
        WHERE id=?
    """, (reason, request_id))
    conn.commit()
    conn.close()
    return True


def get_deletion_requested() -> list:
    """Return all requests flagged for deletion."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM requests
        WHERE delete_requested=1
        ORDER BY updated_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_request_record(request_id: int) -> list:
    """
    Hard delete a request and all related records.
    Returns list of filepaths to delete from disk.
    """
    conn = get_db()
    # Collect attachment filepaths before deleting
    rows = conn.execute(
        "SELECT filepath FROM attachments WHERE request_id=?",
        (request_id,)).fetchall()
    filepaths = [r["filepath"] for r in rows if r["filepath"]]

    conn.execute("DELETE FROM attachments  WHERE request_id=?", (request_id,))
    conn.execute("DELETE FROM assignments  WHERE request_id=?", (request_id,))
    conn.execute("DELETE FROM audit_log    WHERE request_id=?", (request_id,))
    conn.execute("DELETE FROM notifications WHERE request_id=?", (request_id,))
    conn.execute("DELETE FROM requests     WHERE id=?", (request_id,))
    conn.commit()
    conn.close()
    return filepaths


def add_attachment(request_id: int, filename: str,
                   filepath: str,
                   file_size: int | None = None,
                   status: str = "draft",
                   document_type: str = "other") -> int:
    """Add an attachment to a request."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO attachments
            (request_id, filename, filepath, file_size, status, document_type)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (request_id, filename, filepath, file_size, status, document_type))
    attachment_id = c.lastrowid
    if attachment_id is None:
        raise ValueError("Failed to add attachment")
    conn.commit()
    conn.close()
    return attachment_id


def remove_attachment(attachment_id: int) -> str | None:
    """Remove attachment record, return filepath for file deletion."""
    conn = get_db()
    row = conn.execute(
        "SELECT filepath FROM attachments WHERE id=?",
        (attachment_id,)).fetchone()
    if not row:
        conn.close()
        return None
    filepath = row["filepath"]
    conn.execute(
        "DELETE FROM attachments WHERE id=?", (attachment_id,))
    conn.commit()
    conn.close()
    return filepath


def get_all_requests(status_filter: str | None = None) -> list:
    """Fetch all requests for the dashboard including drafts."""
    conn = get_db()
    query = """
        SELECT r.*,
               s.stage_name,
               s.sla_days,
               a.assigned_to_name,
               a.assigned_at   AS stage_assigned_at,
               a.due_at,
               CAST(
                 (julianday('now') - julianday(a.assigned_at))
                 AS INTEGER
               ) AS days_in_stage
        FROM requests r
        LEFT JOIN stage_definitions s ON r.current_stage = s.stage_code
        LEFT JOIN assignments a ON (
            a.request_id   = r.id AND
            a.completed_at IS NULL AND
            a.stage_code   = r.current_stage
        )
    """
    if status_filter:
        rows = conn.execute(
            query + " WHERE r.status=? ORDER BY r.updated_at DESC",
            (status_filter,)).fetchall()
    else:
        rows = conn.execute(
            query + " ORDER BY r.updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_request(request_id: int) -> dict | None:
    """Fetch a single request with full history."""
    conn = get_db()
    c = conn.cursor()

    req = c.execute(
        "SELECT * FROM requests WHERE id=?",
        (request_id,)).fetchone()
    if not req:
        conn.close()
        return None

    assignments = c.execute("""
        SELECT a.*, s.stage_name, s.sla_days
        FROM assignments a
        LEFT JOIN stage_definitions s ON a.stage_code = s.stage_code
        WHERE a.request_id=?
        ORDER BY a.assigned_at ASC
    """, (request_id,)).fetchall()

    audit = c.execute("""
        SELECT * FROM audit_log
        WHERE request_id=?
        ORDER BY timestamp ASC
    """, (request_id,)).fetchall()

    attachments = c.execute(
        "SELECT * FROM attachments WHERE request_id=?",
        (request_id,)).fetchall()

    conn.close()
    return {
        "request":     dict(req),
        "assignments": [dict(a) for a in assignments],
        "audit":       [dict(a) for a in audit],
        "attachments": [dict(a) for a in attachments],
    }


def assign_stage(request_id: int, stage_code: str,
                 assigned_to_id: int | None = None,
                 assigned_to_name: str | None = None,
                 notes: str | None = None):
    """Create a new stage assignment."""
    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT sla_days FROM stage_definitions WHERE stage_code=?",
        (stage_code,))
    row      = c.fetchone()
    sla_days = row["sla_days"] if row else 5

    due_at = None
    if sla_days > 0:
        due_at = (datetime.now() +
                  timedelta(days=sla_days)).strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        INSERT INTO assignments
            (request_id, stage_code, assigned_to_id,
             assigned_to_name, due_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (request_id, stage_code, assigned_to_id,
          assigned_to_name, due_at, notes))

    c.execute("""
        UPDATE requests
        SET current_stage=?, updated_at=datetime('now')
        WHERE id=?
    """, (stage_code, request_id))

    conn.commit()
    conn.close()


def log_action(request_id: int, stage_code: str, action: str,
               actor_name: str | None = None,
               actor_role: str | None = None,
               notes: str | None = None,
               tracking_id: str | None = None):
    """Append to audit log."""
    conn = get_db()
    conn.execute("""
        INSERT INTO audit_log
            (request_id, tracking_id, stage_code,
             action, actor_name, actor_role, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (request_id, tracking_id, stage_code,
          action, actor_name, actor_role, notes))
    conn.commit()
    conn.close()


def assign_tracking_id(request_id: int, tracking_id: str,
                       assigned_by: str | None = None) -> bool:
    """Assign manual tracking ID and move to in_workflow."""
    conn = get_db()
    c = conn.cursor()
    existing = c.execute(
        "SELECT id FROM requests WHERE tracking_id=?",
        (tracking_id,)).fetchone()
    if existing:
        conn.close()
        return False
    c.execute("""
        UPDATE requests
        SET tracking_id=?,
            status='in_workflow',
            updated_at=datetime('now')
        WHERE id=?
    """, (tracking_id, request_id))
    conn.commit()
    conn.close()
    log_action(request_id, "procurement_review",
               "tracking_id_assigned",
               actor_name=assigned_by,
               actor_role="procurement_manager",
               notes=f"Tracking ID assigned: {tracking_id}",
               tracking_id=tracking_id)
    return True


def get_staff(role: str | None = None) -> list:
    conn = get_db()
    if role:
        rows = conn.execute(
            "SELECT * FROM staff WHERE role=? AND active=1 ORDER BY name",
            (role,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staff WHERE active=1 ORDER BY name"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stage_definitions() -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM stage_definitions ORDER BY sequence"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_stage_sla(stage_code: str, sla_days: int):
    conn = get_db()
    conn.execute(
        "UPDATE stage_definitions SET sla_days=? WHERE stage_code=?",
        (sla_days, stage_code))
    conn.commit()
    conn.close()




# ── RFQ operations ───────────────────────────────────────────

def get_rfq_templates() -> list:
    """Return all active RFQ templates."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM rfq_templates WHERE active=1 ORDER BY code"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_rfq_draft(request_id: int, template_code: str,
                     created_by_email: str) -> int:
    """
    Create a new RFQ draft linked to a procurement request.
    Returns the new rfq_draft id.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO rfq_drafts
            (request_id, template_code, status, created_by_email,
             conversation_json, field_data_json, line_items_json)
        VALUES (?, ?, 'draft', ?, '[]', '{}', '[]')
    """, (request_id, template_code, created_by_email))
    new_id = c.lastrowid
    if new_id is None:
        raise ValueError("Failed to create RFQ draft")
    conn.commit()
    conn.close()
    return new_id


def get_rfq_draft(rfq_id: int) -> dict | None:
    """Return a single RFQ draft with its validation results."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM rfq_drafts WHERE id=?", (rfq_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None
    draft = dict(row)

    validations = conn.execute("""
        SELECT * FROM rfq_validation_results
        WHERE rfq_draft_id=?
        ORDER BY severity, created_at
    """, (rfq_id,)).fetchall()
    conn.close()

    draft["validations"] = [dict(v) for v in validations]
    return draft


def get_rfq_drafts_for_request(request_id: int) -> list:
    """Return all RFQ drafts for a procurement request."""
    conn = get_db()
    rows = conn.execute("""
        SELECT d.*, t.name AS template_name, t.document_type
        FROM rfq_drafts d
        JOIN rfq_templates t ON d.template_code = t.code
        WHERE d.request_id=?
        ORDER BY d.created_at DESC
    """, (request_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_rfq_draft(rfq_id: int, updates: dict) -> bool:
    """
    Update any combination of rfq_draft fields.
    Allowed keys: conversation_json, field_data_json, scope_draft,
    scope_approved, line_items_json, warranty, notes, status,
    generated_at.
    Always updates updated_at.
    """
    allowed = {
        "conversation_json", "field_data_json", "scope_draft",
        "scope_approved", "line_items_json", "warranty",
        "notes", "status", "generated_at",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False

    set_clause = ", ".join(f"{k}=?" for k in filtered)
    values     = list(filtered.values()) + [rfq_id]

    conn = get_db()
    conn.execute(
        f"UPDATE rfq_drafts SET {set_clause}, "
        f"updated_at=datetime('now') WHERE id=?",
        values
    )
    conn.commit()
    conn.close()
    return True


def save_rfq_validation_results(rfq_id: int,
                                 results: list[dict]) -> None:
    """
    Replace all validation results for an RFQ draft.
    Each result dict: {severity, field_ref, message}
    severity: error | warning | info
    """
    conn = get_db()
    conn.execute(
        "DELETE FROM rfq_validation_results WHERE rfq_draft_id=?",
        (rfq_id,)
    )
    conn.executemany("""
        INSERT INTO rfq_validation_results
            (rfq_draft_id, severity, field_ref, message)
        VALUES (?, ?, ?, ?)
    """, [
        (rfq_id,
         r.get("severity", "info"),
         r.get("field_ref", ""),
         r.get("message", ""))
        for r in results
    ])
    conn.commit()
    conn.close()


def resolve_rfq_validation(validation_id: int) -> bool:
    """Mark a single validation result as resolved."""
    conn = get_db()
    conn.execute(
        "UPDATE rfq_validation_results SET resolved=1 WHERE id=?",
        (validation_id,)
    )
    conn.commit()
    conn.close()
    return True


def delete_rfq_draft(rfq_id: int) -> bool:
    """Hard delete an RFQ draft and its validation results."""
    conn = get_db()
    conn.execute(
        "DELETE FROM rfq_validation_results WHERE rfq_draft_id=?",
        (rfq_id,)
    )
    conn.execute(
        "DELETE FROM rfq_drafts WHERE id=?", (rfq_id,)
    )
    conn.commit()
    conn.close()
    return True

if __name__ == "__main__":
    init_db()

