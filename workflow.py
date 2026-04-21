# workflow.py
import os
import json
import anthropic
from datetime import datetime, timedelta
from dotenv import load_dotenv
import database

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Config-driven constants ───────────────────────────────────
# All thresholds and keyword lists live in procurement_config.json.
# Do not hardcode values here — edit the JSON file instead.
from procurement_config import cfg

MAINTENANCE_KEYWORDS = cfg.maintenance_redirect_keywords()
HR_REVIEW_KEYWORDS   = cfg.hr_review_keywords()


def requires_competitive_bid(item_type: str, amount: float) -> bool:
    """Return True if the amount meets or exceeds the bid threshold."""
    return cfg.requires_competitive_bid(item_type, amount)


def classify_service_for_hr(item_name: str,
                             description: str,
                             item_type: str) -> dict:
    """
    Use Claude to classify a service request into one of three categories:
    - maintenance_redirect: should go to city maintenance, reject intake
    - hr_required: external service with potential union labor overlap
    - no_hr: external service, no union overlap likely
    """
    if item_type not in ("professional_services", "other"):
        return {"classification": "no_hr",
                "reasoning": "Not a service request."}

    combined = (item_name + " " + description).lower()
    for kw in cfg.maintenance_redirect_keywords():
        if kw in combined:
            return {
                "classification": "maintenance_redirect",
                "reasoning": (
                    f"Request appears to involve city maintenance scope "
                    f"('{kw}'). Requester should contact the Maintenance "
                    f"& Facilities department directly."
                )
            }

    prompt = f"""You are a procurement compliance officer for the City of Palm Springs.

A service request has been submitted. Classify it into exactly one of these three categories:

1. MAINTENANCE_REDIRECT — The work is ONLY within the scope of the City's
   internal Maintenance & Facilities department. This means physical repairs
   to CITY-OWNED BUILDINGS AND INFRASTRUCTURE ONLY — fixing doors, windows,
   plumbing, HVAC, electrical outlets, ceiling tiles, flooring, and walls
   inside city facilities.

   This does NOT include:
   - Refurbishment or repair of equipment, furniture, or furnishings
   - Restoration of library materials, carts, or collections
   - Maintenance contracts for specialized equipment
   - Any service requiring outside expertise or specialized skills
   - Anything that would typically be sent to an outside vendor

   When in doubt, do NOT classify as maintenance redirect. Only use this
   category when the work is unambiguously a building/facility repair that
   city maintenance staff handle as a routine internal function.

2. HR_REQUIRED — An external service contract where the work could potentially
   be performed by City union employees (e.g. grounds keeping, janitorial,
   security services, training/instruction, staffing, K9/dog training,
   facility management, custodial, inspection services, parking enforcement).
   These require HR/Union review before proceeding.

3. NO_HR — An external service with no realistic union labor overlap
   (e.g. bottled water delivery, software subscription, legal counsel,
   medical services, specialized consulting, utilities, catering for events).

Request details:
- Item/Service Name: {item_name}
- Description: {description}
- Item Type: {item_type}

Respond with ONLY this JSON (no markdown):
{{
  "classification": "maintenance_redirect" | "hr_required" | "no_hr",
  "reasoning": "One sentence explanation"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        content = message.content[0].text.strip()
        content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        return {
            "classification": "hr_required",
            "reasoning": (
                f"Classification service unavailable — "
                f"defaulting to HR review. ({e})"
            )
        }


def determine_approval_level(amount: float,
                              is_public_project: bool) -> str:
    """Return the required approval role based on amount and project type."""
    return cfg.approval_role(amount, is_public_project)


def build_stage_sequence(request_data: dict,
                         hr_classification: dict) -> list:
    """
    Determine the ordered list of stages this request must pass through.

    Paths:
      P-Card          → complete (no workflow stages)
      Direct contract → procurement_review → [specialist reviews]
                        → contract_development → [approvals] → complete
      Competitive bid → procurement_review → [specialist reviews]
                        → bid_development → bid_process → bid_award
                        → bid_contract → [approvals] → complete
    """
    item_type = request_data.get("item_type", "")
    amount    = float(request_data.get("amount", 0))
    federal   = request_data.get("federal_funds") == "yes"
    pcard     = request_data.get("pcard") == "yes"
    is_public = item_type == "construction"
    hr_class  = hr_classification.get("classification", "no_hr")

    # ── P-Card path — no workflow needed ────────────────────
    if pcard:
        return ["complete"]

    stages = []

    # ── Procurement review always first ─────────────────────
    stages.append("procurement_review")

    # ── Specialist reviews ───────────────────────────────────
    if hr_class == "hr_required":
        stages.append("hr_union_review")

    if item_type in ("it_equipment", "it_software"):
        stages.append("it_review")

    if item_type == "construction":
        stages.append("engineering_review")

    if federal:
        stages.append("spc_review")

    # ── Contract vs Bid path ─────────────────────────────────
    if requires_competitive_bid(item_type, amount):
        # Competitive bid path
        stages.append("bid_development")
        stages.append("bid_process")
        stages.append("bid_award")
        stages.append("bid_contract")
    else:
        # Direct contract path
        stages.append("contract_development")

    # ── Attorney review — after contract is drafted ──────────
    if cfg.attorney_review_required(is_pcard=pcard):
        stages.append("attorney_review")

    # ── Approval chain ───────────────────────────────────────
    approval_role = determine_approval_level(amount, is_public)

    if approval_role == "city_council":
        stages.append("director_approval")
        stages.append("acm_approval")
        stages.append("manager_approval")
        stages.append("council_approval")
    elif approval_role == "city_manager":
        stages.append("director_approval")
        stages.append("manager_approval")
    elif approval_role == "acm":
        stages.append("director_approval")
        stages.append("acm_approval")
    else:
        stages.append("director_approval")

    stages.append("complete")
    return stages


def initialize_workflow(request_id: int, form_data: dict) -> dict:
    """
    Called after a request is approved by the intake AI.
    Classifies service type, builds stage sequence, creates first assignment.
    """
    item_type   = form_data.get("item_type", "")
    item_name   = form_data.get("item_name", "")
    description = form_data.get("description", "")
    amount      = float(form_data.get("amount", 0))
    pcard       = form_data.get("pcard") == "yes"

    # ── P-Card path — mark complete immediately ──────────────
    if pcard:
        conn = database.get_db()
        conn.execute("""
            UPDATE requests
            SET status='complete',
                current_stage='complete',
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE id=?
        """, (request_id,))
        conn.commit()
        conn.close()

        database.log_action(
            request_id, "complete",
            "pcard_auto_complete",
            actor_name="System",
            actor_role="workflow_engine",
            notes="P-Card purchase — no workflow required. "
                  "AI analysis serves as authorization record."
        )
        return {
            "status":         "complete",
            "stage_sequence": ["complete"],
            "current_stage":  "complete",
            "message":        "P-Card purchase approved. "
                              "Use AI analysis as your authorization record."
        }

    # ── Classify service for HR routing ─────────────────────
    hr_classification = classify_service_for_hr(
        item_name, description, item_type)

    # ── Maintenance redirect — reject before workflow ────────
    if hr_classification["classification"] == "maintenance_redirect":
        conn = database.get_db()
        conn.execute("""
            UPDATE requests
            SET status='rejected', current_stage='rejected',
                updated_at=datetime('now')
            WHERE id=?
        """, (request_id,))
        conn.commit()
        conn.close()

        database.assign_stage(request_id, "rejected",
                              notes=hr_classification["reasoning"])
        database.log_action(
            request_id, "rejected",
            "auto_rejected_maintenance",
            actor_name="System",
            actor_role="workflow_engine",
            notes=hr_classification["reasoning"]
        )
        return {
            "status":         "rejected",
            "classification": hr_classification,
            "message":        hr_classification["reasoning"]
        }

    # ── Build stage sequence ─────────────────────────────────
    stage_sequence = build_stage_sequence(form_data, hr_classification)
    is_bid_path    = requires_competitive_bid(item_type, amount)

    # ── Store routing flags on request ───────────────────────
    conn = database.get_db()
    conn.execute("""
        UPDATE requests SET
            hr_required          = ?,
            hr_classification    = ?,
            it_approval_required = ?,
            engineering_required = ?,
            attorney_required    = ?,
            spc_required         = ?,
            bid_path             = ?,
            updated_at           = datetime('now')
        WHERE id = ?
    """, (
        1 if hr_classification["classification"] == "hr_required" else 0,
        hr_classification["classification"],
        1 if "it_review"           in stage_sequence else 0,
        1 if "engineering_review"  in stage_sequence else 0,
        1 if "attorney_review"     in stage_sequence else 0,
        1 if "spc_review"          in stage_sequence else 0,
        1 if is_bid_path           else 0,
        request_id
    ))
    conn.commit()
    conn.close()

    # ── Create first assignment ──────────────────────────────
    path_label = "competitive bid" if is_bid_path else "direct contract"
    database.assign_stage(
        request_id,
        "procurement_review",
        notes=(
            f"Workflow initialized — {path_label} path. "
            f"Stage sequence: {' → '.join(stage_sequence)}"
        )
    )

    database.log_action(
        request_id, "procurement_review",
        "workflow_initialized",
        actor_name="System",
        actor_role="workflow_engine",
        notes=(
            f"Path: {path_label}. "
            f"HR: {hr_classification['classification']}. "
            f"Stages: {' → '.join(stage_sequence)}"
        )
    )

    return {
        "status":         "in_workflow",
        "classification": hr_classification,
        "stage_sequence": stage_sequence,
        "current_stage":  "procurement_review",
        "bid_path":       is_bid_path,
        "message":        (
            f"Request entered workflow — {path_label} path."
        )
    }


def advance_stage(request_id: int, action: str,
                  actor_name: str | None = None,
                  actor_role: str | None = None,
                  notes: str | None = None,
                  tracking_id: str | None = None) -> dict:
    """
    Move a request to its next stage.
    action: 'approved', 'rejected', 'returned'
    """
    req_data = database.get_request(request_id)
    if not req_data:
        return {"error": "Request not found"}

    req           = req_data["request"]
    current_stage = req["current_stage"]

    # Mark current assignment complete
    conn = database.get_db()
    conn.execute("""
        UPDATE assignments
        SET completed_at=datetime('now'), action_taken=?, notes=?
        WHERE request_id=? AND stage_code=? AND completed_at IS NULL
    """, (action, notes, request_id, current_stage))
    conn.commit()
    conn.close()

    database.log_action(
        request_id, current_stage, action,
        actor_name=actor_name,
        actor_role=actor_role,
        notes=notes,
        tracking_id=tracking_id or req.get("tracking_id")
    )

    # ── Rejection ────────────────────────────────────────────
    if action == "rejected":
        conn = database.get_db()
        conn.execute("""
            UPDATE requests
            SET status='rejected', current_stage='rejected',
                updated_at=datetime('now')
            WHERE id=?
        """, (request_id,))
        conn.commit()
        conn.close()
        database.assign_stage(request_id, "rejected", notes=notes)
        return {"new_stage": "rejected", "status": "rejected"}

    # ── Return to requester ──────────────────────────────────
    # Keep current_stage pointing at the stage that returned the request
    # so dashboard JOINs on stage_definitions remain valid.
    # Status alone signals "returned_to_requester".
    if action == "returned":
        conn = database.get_db()
        conn.execute("""
            UPDATE requests
            SET status='returned_to_requester',
                updated_at=datetime('now')
            WHERE id=?
        """, (request_id,))
        conn.commit()
        conn.close()
        return {
            "new_stage": current_stage,   # stage that issued the return
            "status":    "returned_to_requester"
        }

    # ── Advance to next stage ────────────────────────────────
    # Rebuild the stage sequence using stored routing flags
    form_data = {
        "item_type":     req["item_type"],
        "amount":        req["amount"],
        "federal_funds": req["federal_funds"],
        "pcard":         req["pcard"],
    }
    hr_class   = {"classification": req.get("hr_classification", "no_hr")}
    sequence   = build_stage_sequence(form_data, hr_class)

    # For bid_process stage, check if a custom SLA was set
    try:
        current_idx = sequence.index(current_stage)
        next_stage  = sequence[current_idx + 1]
    except (ValueError, IndexError):
        next_stage = "complete"

    # Update request status
    conn = database.get_db()
    new_status = "complete" if next_stage == "complete" else "in_workflow"
    if next_stage == "complete":
        conn.execute("""
            UPDATE requests
            SET current_stage=?, status=?,
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE id=?
        """, (next_stage, new_status, request_id))
    else:
        conn.execute("""
            UPDATE requests
            SET current_stage=?, status=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (next_stage, new_status, request_id))
    conn.commit()
    conn.close()

    if next_stage != "complete":
        # For bid_process, use custom SLA if set
        if next_stage == "bid_process" and req.get("bid_process_sla_days"):
            # Temporarily override SLA for this assignment
            conn = database.get_db()
            c = conn.cursor()
            custom_sla = req["bid_process_sla_days"]
            due_at = (datetime.now() +
                      timedelta(days=custom_sla)).strftime(
                          "%Y-%m-%d %H:%M:%S")
            c.execute("""
                INSERT INTO assignments
                    (request_id, stage_code, due_at, notes)
                VALUES (?, ?, ?, ?)
            """, (request_id, next_stage, due_at,
                  f"Custom bid period: {custom_sla} days"))
            c.execute("""
                UPDATE requests
                SET current_stage=?, updated_at=datetime('now')
                WHERE id=?
            """, (next_stage, request_id))
            conn.commit()
            conn.close()
        else:
            database.assign_stage(request_id, next_stage, notes=notes)

    database.log_action(
        request_id, next_stage,
        f"advanced_to_{next_stage}",
        actor_name="System",
        actor_role="workflow_engine",
        notes=f"Advanced from {current_stage} after {action}",
        tracking_id=tracking_id or req.get("tracking_id")
    )

    return {
        "new_stage":      next_stage,
        "status":         new_status,
        "previous_stage": current_stage,
    }


def get_overdue_requests() -> list:
    """Return all requests where current stage has exceeded SLA."""
    conn = database.get_db()
    rows = conn.execute("""
        SELECT r.id, r.tracking_id, r.draft_name, r.item_name,
               r.department, r.current_stage, s.stage_name, s.sla_days,
               a.assigned_at, a.due_at, a.assigned_to_name,
               CAST((julianday('now') - julianday(a.assigned_at))
                    AS INTEGER) AS days_in_stage
        FROM requests r
        JOIN stage_definitions s ON r.current_stage = s.stage_code
        JOIN assignments a ON (
            a.request_id   = r.id AND
            a.completed_at IS NULL AND
            a.stage_code   = r.current_stage
        )
        WHERE r.status NOT IN (
            'complete','rejected','returned_to_requester','draft'
        )
          AND s.sla_days > 0
          AND julianday('now') > julianday(a.due_at)
        ORDER BY days_in_stage DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_timeline(request_id: int) -> list:
    """Build a human-readable timeline for the detail view."""
    conn = database.get_db()

    assignments = conn.execute("""
        SELECT a.*, s.stage_name, s.sla_days
        FROM assignments a
        LEFT JOIN stage_definitions s ON a.stage_code = s.stage_code
        WHERE a.request_id = ?
        ORDER BY a.assigned_at ASC
    """, (request_id,)).fetchall()

    conn.close()

    timeline = []
    for a in assignments:
        try:
            assigned_at = datetime.strptime(
                a["assigned_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            assigned_at = datetime.now()

        completed_at = None
        if a["completed_at"]:
            try:
                completed_at = datetime.strptime(
                    a["completed_at"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        if completed_at:
            delta   = completed_at - assigned_at
            elapsed = f"{delta.days}d {delta.seconds//3600}h"
        else:
            delta   = datetime.now() - assigned_at
            elapsed = f"{delta.days}d {delta.seconds//3600}h (ongoing)"

        due_at    = a["due_at"]
        overdue   = False
        days_over = 0
        if due_at and not a["completed_at"]:
            try:
                due_dt  = datetime.strptime(due_at, "%Y-%m-%d %H:%M:%S")
                overdue = datetime.now() > due_dt
                if overdue:
                    days_over = (datetime.now() - due_dt).days
            except Exception:
                pass

        timeline.append({
            "stage_code":   a["stage_code"],
            "stage_name":   a["stage_name"] or a["stage_code"],
            "assigned_to":  a["assigned_to_name"] or "Unassigned",
            "assigned_at":  a["assigned_at"],
            "completed_at": a["completed_at"],
            "action_taken": a["action_taken"],
            "elapsed":      elapsed,
            "sla_days":     a["sla_days"],
            "due_at":       due_at,
            "overdue":      overdue,
            "days_over":    days_over,
            "notes":        a["notes"],
        })

    return timeline


if __name__ == "__main__":
    print("Workflow engine loaded.")
    print("\nSigning authority test:")
    for amt in [5000, 15000, 30000, 60000, 100000, 200000]:
        role = determine_approval_level(amt, False)
        print(f"  ${amt:>10,.0f} non-public → {role}")
    for amt in [50000, 100000, 250000]:
        role = determine_approval_level(amt, True)
        print(f"  ${amt:>10,.0f} public     → {role}")

    print("\nBid threshold test:")
    for itype, amt in [
        ("supplies", 50000), ("supplies", 80000),
        ("construction", 200000), ("construction", 250000),
        ("professional_services", 74999), ("professional_services", 75000),
        ("travel", 999999),
    ]:
        bid = requires_competitive_bid(itype, amt)
        print(f"  {itype:25} ${amt:>10,.0f} → {'BID' if bid else 'direct'}")