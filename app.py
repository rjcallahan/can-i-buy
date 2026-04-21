# -*- coding: utf-8 -*-
# app.py
import os
import json
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import (Flask, request, jsonify, send_from_directory,
                   redirect)
from dotenv import load_dotenv
import anthropic
from anthropic.types import TextBlock

import database
import workflow
import staff as staff_module
import intake
from procurement_config import cfg

load_dotenv()

app = Flask(__name__, static_folder="static",
            template_folder="templates")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

database.init_db()

# Uploads directory — uses DATA_DIR so Railway volume keeps files across deploys
_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
UPLOADS_DIR = os.path.join(_DATA_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Base URL for email links
BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")


def get_upload_folder(email: str) -> str:
    safe_email = email.lower().strip().replace(
        "/", "_").replace("\\", "_")
    folder = os.path.join(UPLOADS_DIR, safe_email)
    os.makedirs(folder, exist_ok=True)
    return folder


def delete_file_safe(filepath: str) -> bool:
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            return True
    except Exception as e:
        print(f"Warning: could not delete {filepath}: {e}")
    return False


# ── Email helpers ────────────────────────────────────────────

def send_email(recipient: str, subject: str, body: str,
               html: str | None = None) -> bool:
    """
    Send an email via Zoho SMTP.
    Accepts optional HTML version — falls back to plain text wrapper.
    Returns True if sent successfully, False otherwise.
    """
    mail_cfg       = cfg._get("mail")
    smtp_host      = os.getenv("SMTP_HOST")      or mail_cfg.get("smtp_host", "smtp.zoho.com")
    smtp_port      = int(os.getenv("SMTP_PORT")  or mail_cfg.get("smtp_port", 587))
    smtp_user      = os.getenv("SMTP_USER")      or mail_cfg.get("smtp_user") or ""
    smtp_password  = os.getenv("SMTP_PASSWORD")
    smtp_from      = os.getenv("SMTP_FROM")      or mail_cfg.get("smtp_from") or smtp_user or ""
    smtp_from_name = os.getenv("SMTP_FROM_NAME") or mail_cfg.get("smtp_from_name", "CAPA Procurement")

    if not smtp_user or not smtp_password:
        print("SMTP not configured — email logged only.")
        return False

    if not smtp_from:
        print("SMTP_FROM not configured — email logged only.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{smtp_from_name} <{smtp_from}>"
        msg["To"]      = recipient

        # Plain text part
        text_part = MIMEText(body, "plain")
        msg.attach(text_part)

        # HTML part
        if html:
            html_part = MIMEText(html, "html")
        else:
            # Simple fallback wrapper
            simple_html = f"""<!DOCTYPE html>
<html><body style="font-family:'Segoe UI',Arial,sans-serif;
                   color:#222;max-width:700px;margin:0 auto;padding:20px">
  <div style="background:#1F3864;padding:14px 24px;
              border-radius:6px 6px 0 0">
    <span style="color:#fff;font-size:18px;font-weight:700">
      CAPA <span style="color:#7BAFD4">Consulting</span>
    </span>
    <span style="color:#cdd8e8;font-size:13px;margin-left:16px">
      City of Palm Springs &mdash; Procurement Gateway
    </span>
  </div>
  <div style="background:#fff;border:1px solid #e0e8f0;
              border-top:none;padding:24px;
              border-radius:0 0 6px 6px">
    <pre style="font-family:'Segoe UI',Arial,sans-serif;
                font-size:13px;line-height:1.7;
                white-space:pre-wrap;margin:0">{body}</pre>
  </div>
  <div style="text-align:center;margin-top:14px;
              font-size:11px;color:#aaa">
    This is an automated message from the City of Palm Springs
    Procurement Gateway. Please do not reply to this email.
  </div>
</body></html>"""
            html_part = MIMEText(simple_html, "html")

        msg.attach(html_part)

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, recipient, msg.as_string())

        print(f"Email sent to {recipient}: {subject}")
        return True

    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def _tl_status_span(t: dict) -> str:
    """Return the coloured status span for a timeline row."""
    if t["completed_at"]:
        return ('<span style="color:#538135;font-weight:600">'
                '&#10003; Complete</span>')
    if t["overdue"]:
        return ('<span style="color:#C00000;font-weight:600">'
                '&#9888; Overdue</span>')
    return '<span style="color:#E97132">In Progress</span>'


def _tl_row_bg(i: int) -> str:
    return "#f8f9fb" if i % 2 else "#fff"


def build_status_email_html(req: dict, timeline: list,
                             extra_note: str) -> str:
    """Build the HTML version of the status email."""
    tl_parts = []
    for i, t in enumerate(timeline):
        bg     = _tl_row_bg(i)
        status = _tl_status_span(t)
        tl_parts.append(
            f'<tr style="background:{bg}">'
            f'<td style="padding:7px 12px;color:#333">{t["stage_name"]}</td>'
            f'<td style="padding:7px 12px;color:#555">{t["assigned_to"]}</td>'
            f'<td style="padding:7px 12px;color:#555">{t["elapsed"]}</td>'
            f'<td style="padding:7px 12px">{status}</td>'
            f'</tr>'
        )
    timeline_rows = "".join(tl_parts)

    note_block = (
        f'<div style="background:#f0f6fb;border-left:3px solid #2E5496;'
        f'padding:12px 16px;border-radius:0 6px 6px 0;'
        f'margin-bottom:24px;font-size:13px">'
        f'<div style="font-weight:700;color:#1F3864;margin-bottom:4px">'
        f'Note from Procurement</div>'
        f'<div style="color:#333">{extra_note}</div>'
        f'</div>'
    ) if extra_note else ""

    submitted = (req.get("submitted_at") or "")[:10] or "&mdash;"
    tracking  = req.get("tracking_id") or "Pending Assignment"
    name      = req.get("draft_name") or req.get("item_name") or ""
    dept      = req.get("department") or ""
    amount    = req.get("amount") or 0
    started   = (req.get("created_at") or "")[:10]
    stage     = (req.get("current_stage") or "").replace("_", " ").title()
    status    = (req.get("status") or "").replace("_", " ").title()

    def row(label: str, value: str, shaded: bool = False) -> str:
        bg = ' style="background:#f8f9fb"' if shaded else ""
        return (
            f'<tr{bg}>'
            f'<td style="padding:5px 12px 5px 0;color:#1F3864;'
            f'font-weight:700;white-space:nowrap;width:140px;'
            f'vertical-align:top">{label}</td>'
            f'<td style="padding:5px 0;color:#333">{value}</td>'
            f'</tr>'
        )

    return (
        f'<!DOCTYPE html>'
        f'<html><body style="font-family:\'Segoe UI\',Arial,sans-serif;'
        f'color:#222;max-width:700px;margin:0 auto;padding:20px">'
        f'<div style="background:#1F3864;padding:14px 24px;'
        f'border-radius:6px 6px 0 0">'
        f'<span style="color:#fff;font-size:18px;font-weight:700">'
        f'CAPA <span style="color:#7BAFD4">Consulting</span></span>'
        f'<span style="color:#cdd8e8;font-size:13px;margin-left:16px">'
        f'City of Palm Springs &mdash; Procurement Gateway</span>'
        f'</div>'
        f'<div style="background:#fff;border:1px solid #e0e8f0;'
        f'border-top:none;padding:24px;border-radius:0 0 6px 6px">'
        f'<table style="width:100%;border-collapse:collapse;'
        f'margin-bottom:24px;font-size:13px">'
        + row("Tracking ID",   tracking)
        + row("Request Name",  name,   True)
        + row("Department",    dept)
        + row("Amount",        f"${float(amount):,.2f}", True)
        + row("Started",       started)
        + row("Submitted",     submitted, True)
        + row("Current Stage", stage)
        + row("Status",
              f'<span style="background:#E8EEF8;color:#1F3864;'
              f'padding:2px 10px;border-radius:12px;'
              f'font-size:12px;font-weight:700">{status}</span>',
              True)
        + f'</table>'
        f'<div style="font-size:12px;font-weight:700;color:#1F3864;'
        f'text-transform:uppercase;letter-spacing:0.4px;'
        f'margin-bottom:8px">Workflow Timeline</div>'
        f'<table style="width:100%;border-collapse:collapse;'
        f'font-size:12px;margin-bottom:24px">'
        f'<thead><tr style="background:#1F3864;color:#fff">'
        f'<th style="padding:7px 12px;text-align:left">Stage</th>'
        f'<th style="padding:7px 12px;text-align:left">Assigned To</th>'
        f'<th style="padding:7px 12px;text-align:left">Time</th>'
        f'<th style="padding:7px 12px;text-align:left">Status</th>'
        f'</tr></thead>'
        f'<tbody>{timeline_rows}</tbody>'
        f'</table>'
        f'{note_block}'
        f'<div style="color:#888;font-size:12px;'
        f'border-top:1px solid #e0e8f0;padding-top:14px">'
        f'For questions contact the Procurement &amp; Contracting Division.'
        f'</div>'
        f'</div>'
        f'<div style="text-align:center;margin-top:14px;'
        f'font-size:11px;color:#aaa">'
        f'This is an automated message from the City of Palm Springs '
        f'Procurement Gateway. Please do not reply to this email.'
        f'</div>'
        f'</body></html>'
    )


def notify_requester(request_id: int, event: str,
                     notes: str | None = None) -> bool:
    """
    Send an automatic notification email to the requester
    when their request status changes.
    """
    req_data = database.get_request(request_id)
    if not req_data:
        return False

    req       = req_data["request"]
    recipient = req.get("requester_email")
    if not recipient or "@" not in recipient:
        return False

    name     = req.get("draft_name") or req.get("item_name") or ""
    tracking = req.get("tracking_id") or f"#{request_id}"
    dept     = req.get("department", "")
    amount   = req.get("amount") or 0
    url      = f"{BASE_URL}/my-requests"

    subjects = {
        "returned": f"Action Required \u2014 {tracking}: {name}",
        "rejected": f"Request Not Approved \u2014 {tracking}: {name}",
        "approved": f"Request Approved \u2014 {tracking}: {name}",
        "complete": f"Request Complete \u2014 {tracking}: {name}",
    }

    bodies = {
        "returned": (
            f"Your procurement request has been returned for "
            f"additional information.\n\n"
            f"Request  : {name}\n"
            f"Dept     : {dept}\n"
            f"Tracking : {tracking}\n"
            f"Amount   : ${float(amount):,.2f}\n\n"
            f"REASON / INSTRUCTIONS:\n"
            f"{notes or 'Please review and resubmit your request.'}\n\n"
            f"To edit and resubmit your request, visit:\n{url}\n\n"
            f"For questions contact the Procurement & Contracting Division."
        ),
        "rejected": (
            f"Your procurement request could not be approved.\n\n"
            f"Request  : {name}\n"
            f"Dept     : {dept}\n"
            f"Tracking : {tracking}\n"
            f"Amount   : ${float(amount):,.2f}\n\n"
            f"REASON:\n"
            f"{notes or 'Please contact the Procurement & Contracting Division.'}\n\n"
            f"You may resubmit a new request at:\n{url}\n\n"
            f"For questions contact the Procurement & Contracting Division."
        ),
        "approved": (
            f"Your procurement request has been approved and is "
            f"moving through the workflow.\n\n"
            f"Request  : {name}\n"
            f"Dept     : {dept}\n"
            f"Tracking : {tracking}\n"
            f"Amount   : ${float(amount):,.2f}\n\n"
            f"Check the status of your request at:\n{url}\n\n"
            f"For questions contact the Procurement & Contracting Division."
        ),
        "complete": (
            f"Your procurement request has been fully approved "
            f"and processed.\n\n"
            f"Request  : {name}\n"
            f"Dept     : {dept}\n"
            f"Tracking : {tracking}\n"
            f"Amount   : ${float(amount):,.2f}\n\n"
            f"Contact Procurement for next steps on contract execution "
            f"or purchase order issuance.\n\n"
            f"For questions contact the Procurement & Contracting Division."
        ),
    }

    subject = subjects.get(
        event, f"Procurement Update \u2014 {tracking}: {name}")
    body    = bodies.get(
        event, f"Your request {tracking} has been updated.")

    return send_email(recipient, subject, body)


# ── Static ───────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "can-i-buy-this.html")

@app.route("/health")
def health():
    return "ok", 200

@app.route("/can-i-buy-this")
def can_i_buy_this():
    return send_from_directory("static", "can-i-buy-this.html")


# ── Draft save (auto-save from intake form) ──────────────────
@app.route("/api/requests/draft", methods=["POST"])
def save_draft():
    """Create or update a draft request."""
    try:
        data       = request.get_json()
        email      = data.get("requester_email", "").strip()
        request_id = data.get("request_id")

        if not email:
            return jsonify({"error": "Email is required"}), 400

        staff_module.auto_register_requester(
            name       = data.get("requester_name", ""),
            email      = data.get("requester_email", ""),
            department = data.get("department", ""),
            phone      = data.get("phone", "")
        )
        rid = database.create_or_update_draft(data, request_id)

        return jsonify({
            "success":    True,
            "request_id": rid,
            "draft_name": data.get("draft_name") or
                          data.get("item_name") or "Draft",
            "message":    "Draft saved."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Attachment upload ────────────────────────────────────────
@app.route("/api/requests/<int:request_id>/attachments",
           methods=["POST"])
def upload_attachment(request_id):
    """Upload a PDF attachment to a request/draft."""
    try:
        req = database.get_request(request_id)
        if not req:
            return jsonify({"error": "Request not found"}), 404

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "No filename"}), 400
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"error": "PDF files only"}), 400

        email     = req["request"].get("requester_email", "unknown")
        safe_name = f"req_{request_id}_{f.filename}"
        folder    = get_upload_folder(email)
        filepath  = os.path.join(folder, safe_name)

        f.save(filepath)
        file_size = os.path.getsize(filepath)

        status        = "draft" if req["request"]["status"] == "draft" \
                        else "active"
        document_type = request.form.get("document_type", "other")

        attachment_id = database.add_attachment(
            request_id, f.filename, filepath, file_size, status,
            document_type)

        return jsonify({
            "success":       True,
            "attachment_id": attachment_id,
            "filename":      f.filename,
            "file_size":     file_size,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attachments/<int:attachment_id>",
           methods=["DELETE"])
def delete_attachment(attachment_id):
    try:
        filepath = database.remove_attachment(attachment_id)
        if filepath:
            delete_file_safe(filepath)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attachments/<int:attachment_id>/download")
def download_attachment(attachment_id):
    try:
        conn = database.get_db()
        row  = conn.execute(
            "SELECT * FROM attachments WHERE id=?",
            (attachment_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        row       = dict(row)
        directory = os.path.dirname(row["filepath"])
        filename  = os.path.basename(row["filepath"])
        return send_from_directory(
            directory, filename,
            as_attachment=True,
            download_name=row["filename"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Approval chain helper ────────────────────────────────────
def compute_approval_chain(amount: float, item_type: str) -> list:
    """
    Return the ordered approval chain for a given amount and item type.
    Each entry: {role, label, note, approves}

    Chain structure:
      1. Department head — IT Director for IT purchases, Department Director otherwise
      2. The signing authority whose threshold covers the amount (only that level,
         not all intermediate levels)

    If the department head IS the signing authority (small purchases), only one step.
    """
    IT_TYPES = {"it_equipment", "it_software"}

    is_public     = item_type == "construction"
    levels        = cfg.signing_authority_levels()
    required_role = cfg.approval_role(amount, is_public)
    required_idx  = next(
        (i for i, l in enumerate(levels) if l["role"] == required_role),
        len(levels) - 1
    )

    # Department head
    if item_type in IT_TYPES:
        dept_head = {"role": "it_director", "label": "IT Director", "note": ""}
    else:
        dept_head = {"role": "director", "label": "Department Director", "note": ""}

    # If Director-level authority is sufficient, dept head signs directly
    if required_idx == 0:
        return [{**dept_head, "approves": True}]

    # Otherwise: dept head recommends, signing authority signs
    signing = levels[required_idx]
    return [
        {**dept_head, "approves": False},
        {"role": signing["role"], "label": signing["label"],
         "note": "", "approves": True},
    ]


# ── Send "Can I Buy This?" report via email ──────────────────
@app.route("/api/send-report", methods=["POST"])
def send_can_i_buy_report():
    """Email the Can I Buy This analysis result to a @palmspringsca.gov address."""
    import re
    body  = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()

    email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    if not email_re.match(email):
        return jsonify({"error": "Invalid email address."}), 400
    allowed_emails = {"rjames.callahan@gmail.com", "kimbaker0206@gmail.com"}
    if not email.endswith("@palmspringsca.gov") and email not in allowed_emails:
        return jsonify({"error": "Must be a @palmspringsca.gov address."}), 400

    data   = body.get("data",   {})
    result = body.get("result", {})

    item_name   = data.get("item_name",  "Unknown item")
    description = data.get("description", "")
    amount      = data.get("amount", 0)
    verdict     = result.get("verdict", "")
    summary     = result.get("summary", "")

    verdict_label = {"APPROVED": "✅ Approved", "FLAGGED": "🚩 Flagged",
                     "RETURNED": "🔄 Returned"}.get(verdict, verdict)

    methods = result.get("valid_methods") or (
        [{"method": result["procurement_method"], "description": "", "documents_needed": []}]
        if result.get("procurement_method") else []
    )
    methods_html = "".join(
        f"<li><strong>{m['method']}</strong>"
        f"{': ' + m['description'] if m.get('description') else ''}"
        f"{'<br><em>Docs needed: ' + ', '.join(m['documents_needed']) + '</em>' if m.get('documents_needed') else ''}"
        f"</li>"
        for m in methods
    )

    # Approval chain
    item_type    = data.get("item_type", "other")
    chain        = result.get("approval_chain") or compute_approval_chain(float(amount), item_type)
    chain_rows   = "".join(
        f"<tr>"
        f"<td style='padding:6px 10px;font-weight:{'700' if s['approves'] else '400'};color:#1f3864'>{s['label']}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:{'#1a7a4a' if s['approves'] else '#777'}'>"
        f"{'Signs contract &amp; authorizes PO' if s['approves'] else 'Reviews &amp; recommends'}</td>"
        f"</tr>"
        for s in chain
    )
    chain_html = (
        f"<h3 style='color:#1f3864'>Contract Approval Path</h3>"
        f"<p style='font-size:12px;color:#666'>No approvals needed before submitting. Once submitted, "
        f"Procurement generates a contract and routes it through these levels before a PO is issued.</p>"
        f"<table style='border-collapse:collapse;width:100%'>{chain_rows}</table>"
    ) if chain_rows else ""

    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#1f3864">Can I Buy This? — Policy Report</h2>
      <p><strong>Item:</strong> {item_name}</p>
      <p><strong>Estimated Cost:</strong> ${amount:,.2f}</p>
      <p><strong>Description:</strong> {description}</p>
      <hr style="border:none;border-top:1px solid #e0e8f0">
      <p><strong>Verdict:</strong> {verdict_label}</p>
      <p>{summary}</p>
      {"<h3 style='color:#1f3864'>Procurement Path(s)</h3><ul>" + methods_html + "</ul>" if methods_html else ""}
      {chain_html}
      {f'<h3 style="color:#1f3864">✈️ FAA Compliance Notes</h3><p style="background:#f0f4ff;padding:10px 12px;border-radius:6px;font-size:13px">{result.get("faa_notes")}</p>' if result.get("faa_notes") else ""}
      <hr style="border:none;border-top:1px solid #e0e8f0">
      <p style="font-size:12px;color:#888">This is a policy check only — nothing has been formally submitted.</p>
    </div>"""

    text_body = (
        f"Can I Buy This? — Policy Report\n\n"
        f"Item: {item_name}\nEstimated Cost: ${amount:,.2f}\nDescription: {description}\n\n"
        f"Verdict: {verdict_label}\n{summary}\n\n"
        + ("Procurement Path(s):\n" + "\n".join(f"- {m['method']}" for m in methods) if methods else "")
    )

    success = send_email(email, f"Procurement Policy Check: {item_name}", text_body, html_body)
    if success is False:
        return jsonify({"error": "Email send failed. Check SMTP configuration."}), 500
    return jsonify({"ok": True})


# ── Sole source letter analysis ─────────────────────────────
@app.route("/api/analyze-sole-source", methods=["POST"])
def analyze_sole_source():
    """
    Accept a sole source justification PDF, extract its text,
    and evaluate it against California public procurement criteria.
    Returns a structured JSON report.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "No filename"}), 400
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"error": "PDF files only"}), 400

        import base64
        pdf_bytes = f.read()
        pdf_b64   = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        prompt = """You are a California public procurement compliance officer reviewing a sole source justification letter.

Evaluate this letter strictly against the recognized legal bases for sole source procurement under California Government Code and City of Palm Springs procurement policy.

RECOGNIZED JUSTIFICATION TESTS (check each one):
1. Unique Capability — Only vendor holds patents, proprietary technology, or specialized expertise not available elsewhere
2. Compatibility / Integration — Must be compatible with existing systems and no alternative is interoperable
3. Continuity of Service — Switching vendors causes unacceptable disruption, data loss, or excessive migration cost
4. Single Manufacturer — Only one manufacturer produces the item to the required specification
5. Authorized Dealer / OEM — Warranty, support, or safety requires purchase from original manufacturer or authorized distributor
6. Standardization — Department has formally standardized on this product for valid operational reasons
7. Price Reasonableness — Evidence provided that price is fair and reasonable despite absence of competition
8. Certifying Official — Letter is signed by an authorized official with name and title

For each criterion, determine whether it is: claimed and supported, claimed but vague/unsupported, not claimed, or not applicable.

Return ONLY this JSON — no markdown, no text outside the JSON:
{
  "strength": "weak" | "adequate" | "strong",
  "justification_types": ["list of which tests are claimed"],
  "checks": [
    {
      "criterion": "criterion name",
      "status": "pass" | "partial" | "fail" | "not_applicable",
      "note": "1-2 sentence assessment"
    }
  ],
  "flags": ["list of specific deficiencies or concerns — empty array if none"],
  "recommendation": "2-3 sentence plain-English summary of the letter's adequacy and what, if anything, needs to be strengthened",
  "ready_to_submit": true | false
}

Scoring guide:
- strong: at least one test is clearly and specifically supported with facts; price reasonableness addressed; official identified
- adequate: at least one test is claimed with partial support; minor gaps exist but the letter would likely survive review
- weak: justification is vague, conclusory, or relies entirely on convenience rather than legal necessity"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        )

        block = message.content[0]
        if not isinstance(block, TextBlock):
            raise ValueError(f"Unexpected response type: {type(block)}")
        content = block.text
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            cleaned = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)

        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse AI response: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Email sole source analysis ───────────────────────────────
@app.route("/api/send-ss-report", methods=["POST"])
def send_ss_report():
    import re
    body  = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()

    email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    if not email_re.match(email):
        return jsonify({"error": "Invalid email address."}), 400
    allowed_emails = {"rjames.callahan@gmail.com", "kimbaker0206@gmail.com"}
    if not email.endswith("@palmspringsca.gov") and email not in allowed_emails:
        return jsonify({"error": "Must be a @palmspringsca.gov address."}), 400

    r        = body.get("result", {})
    filename = body.get("filename", "sole source letter")
    strength = (r.get("strength") or "unknown").capitalize()
    ready    = r.get("ready_to_submit", False)

    strength_color = {"Strong": "#1a7a4a", "Adequate": "#c07000", "Weak": "#c0392b"}.get(strength, "#555")
    strength_bg    = {"Strong": "#edfaf3", "Adequate": "#fff8f0", "Weak": "#fdf2f2"}.get(strength, "#f9f9f9")

    status_colors = {"pass": "#1a7a4a", "partial": "#c07000", "fail": "#c0392b", "not_applicable": "#888"}
    status_labels = {"pass": "✓ Supported", "partial": "~ Partial", "fail": "✗ Missing", "not_applicable": "— N/A"}

    checks_html = ""
    for i, c in enumerate(r.get("checks", [])):
        row_bg   = "#f8f9fb" if i % 2 else "#fff"
        status   = c.get("status", "")
        sc       = status_colors.get(status, "#555")
        sl       = status_labels.get(status, status)
        checks_html += (
            f"<tr style='background:{row_bg}'>"
            f"<td style='padding:6px 10px;font-weight:600;color:{sc}'>{sl}</td>"
            f"<td style='padding:6px 10px;font-weight:600;color:#1f3864'>{c.get('criterion','')}</td>"
            f"<td style='padding:6px 10px;color:#444;font-size:12px'>{c.get('note','')}</td>"
            f"</tr>"
        )

    flags_html = "".join(
        f"<li style='color:#c0392b;font-size:13px'>{flag}</li>"
        for flag in r.get("flags", [])
    )
    flags_block = (
        f"<div style='margin-top:14px'>"
        f"<div style='font-weight:700;color:#c0392b;margin-bottom:6px'>Issues to address:</div>"
        f"<ul style='margin:0;padding-left:18px'>{flags_html}</ul></div>"
    ) if flags_html else ""

    ready_badge = (
        "<span style='background:#edfaf3;color:#1a7a4a;border:1px solid #a8dfc0;"
        "border-radius:10px;font-size:11px;font-weight:700;padding:2px 9px'>Ready to submit</span>"
        if ready else
        "<span style='background:#fdf2f2;color:#c0392b;border:1px solid #f0b8b8;"
        "border-radius:10px;font-size:11px;font-weight:700;padding:2px 9px'>Needs strengthening</span>"
    )

    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:660px;margin:0 auto">
      <div style="background:#1f3864;padding:14px 24px;border-radius:6px 6px 0 0">
        <span style="color:#fff;font-size:17px;font-weight:700">CAPA <span style="color:#7bafd4">Consulting</span></span>
        <span style="color:#cdd8e8;font-size:13px;margin-left:16px">Sole Source Letter Analysis</span>
      </div>
      <div style="background:#fff;border:1px solid #e0e8f0;border-top:none;padding:24px;border-radius:0 0 6px 6px">
        <p style="margin:0 0 4px"><strong>Document:</strong> {filename}</p>
        <div style="background:{strength_bg};border-left:3px solid {strength_color};padding:10px 14px;
                    border-radius:0 6px 6px 0;margin:14px 0;display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:700;color:{strength_color}">Letter strength: {strength}</span>
          {ready_badge}
        </div>
        <p style="font-size:13px;color:#333">{r.get('recommendation','')}</p>
        <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:14px">
          <thead><tr style="background:#1f3864;color:#fff">
            <th style="padding:7px 10px;text-align:left;width:110px">Status</th>
            <th style="padding:7px 10px;text-align:left">Criterion</th>
            <th style="padding:7px 10px;text-align:left">Assessment</th>
          </tr></thead>
          <tbody>{checks_html}</tbody>
        </table>
        {flags_block}
        <p style="font-size:11px;color:#aaa;border-top:1px solid #e0e8f0;padding-top:12px;margin-top:20px">
          This is a policy pre-check only — not a formal determination.
        </p>
      </div>
    </div>"""

    text_body = (
        f"Sole Source Letter Analysis\n\nDocument: {filename}\n"
        f"Strength: {strength}\nReady to submit: {'Yes' if ready else 'No'}\n\n"
        f"{r.get('recommendation','')}\n\n"
        + "\n".join(
            f"{status_labels.get(c.get('status',''),'')} — {c.get('criterion','')}: {c.get('note','')}"
            for c in r.get("checks", [])
        )
    )

    success = send_email(email, f"Sole Source Letter Analysis: {filename}", text_body, html_body)
    if success is False:
        return jsonify({"error": "Email send failed. Check SMTP configuration."}), 500
    return jsonify({"ok": True})


# ── AI analysis ──────────────────────────────────────────────
@app.route("/analyze", methods=["POST"])
def analyze():
    """Run AI policy analysis and promote draft if approved."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data received"}), 400

        prompt = intake.build_prompt(data)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        block = message.content[0]
        if not isinstance(block, TextBlock):
            raise ValueError(f"Unexpected response block type: {type(block)}")
        content = block.text
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            cleaned = content.replace(
                "```json", "").replace("```", "").strip()
            result = json.loads(cleaned)

        request_id = data.get("request_id")
        is_demo    = data.get("demo", False)

        if result.get("verdict") == "APPROVED" and not is_demo:
            formal_id = database.create_request(data, result)
            wf_result = workflow.initialize_workflow(formal_id, data)

            if wf_result["status"] == "rejected":
                if request_id is not None:
                    database.return_request_to_draft(formal_id, result)
                result["verdict"]    = "RETURNED"
                result["summary"]    = wf_result["message"]
                result["flags"]      = result.get("flags", []) + [
                    wf_result["message"]]
                result["request_id"] = formal_id
            else:
                result["request_id"]     = formal_id
                result["workflow"]       = wf_result
                result["stage_sequence"] = wf_result.get(
                    "stage_sequence", [])

        elif result.get("verdict") in ("RETURNED", "FLAGGED") and not is_demo:
            if request_id is not None:
                database.return_request_to_draft(request_id, result)
            result["request_id"] = request_id

        # Inject deterministic approval chain
        amount    = float(data.get("amount") or 0)
        item_type = data.get("item_type", "other")
        result["approval_chain"] = compute_approval_chain(amount, item_type)

        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({
            "error": f"Failed to parse AI response: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Dashboard ────────────────────────────────────────────────
@app.route("/dashboard")
def dashboard():
    return redirect("/", code=302)


@app.route("/api/dashboard/data")
def dashboard_data():
    try:
        all_requests  = database.get_all_requests()
        overdue       = workflow.get_overdue_requests()
        stages        = database.get_stage_definitions()
        all_staff     = staff_module.get_all_staff()
        deletion_reqs = database.get_deletion_requested()

        counts = {
            "total":       len(all_requests),
            "drafts":      sum(1 for r in all_requests
                               if r["status"] == "draft"),
            "pending":     sum(1 for r in all_requests
                               if r["status"] == "pending_procurement"),
            "in_workflow": sum(1 for r in all_requests
                               if r["status"] == "in_workflow"),
            "complete":    sum(1 for r in all_requests
                               if r["status"] == "complete"),
            "rejected":    sum(1 for r in all_requests
                               if r["status"] in (
                                   "rejected",
                                   "returned_to_requester")),
            "overdue":     len(overdue),
            "deletion_requested": len(deletion_reqs),
        }

        return jsonify({
            "requests":          all_requests,
            "overdue":           overdue,
            "stages":            stages,
            "staff":             all_staff,
            "counts":            counts,
            "deletion_requests": deletion_reqs,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Request detail ───────────────────────────────────────────
@app.route("/request/<int:request_id>")
def request_detail(request_id):
    return redirect("/", code=302)


@app.route("/api/request/<int:request_id>")
def get_request_data(request_id):
    try:
        data = database.get_request(request_id)
        if not data:
            return jsonify({"error": "Request not found"}), 404
        timeline = workflow.get_timeline(request_id)
        data["timeline"] = timeline
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── My Requests ──────────────────────────────────────────────
@app.route("/my-requests")
def my_requests():
    return redirect("/dashboard?view=requester", code=302)


@app.route("/api/my-requests/lookup", methods=["POST"])
def lookup_requester():
    try:
        body  = request.get_json()
        email = body.get("email", "").lower().strip()
        if not email:
            return jsonify({"error": "Email is required"}), 400

        conn  = database.get_db()
        staff = conn.execute(
            "SELECT * FROM staff WHERE LOWER(email)=? AND active=1",
            (email,)).fetchone()
        conn.close()

        if staff:
            staff_dict = dict(staff)
            dept_data  = database.get_drafts_by_department(
                staff_dict["department"])
            return jsonify({
                "view":       "department",
                "email":      email,
                "name":       staff_dict["name"],
                "department": staff_dict["department"],
                "role":       staff_dict["role"],
                "drafts":     dept_data["drafts"],
                "requests":   dept_data["requests"],
            })
        else:
            requests_list = database.get_drafts_by_email(email)
            return jsonify({
                "view":     "personal",
                "email":    email,
                "requests": requests_list,
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Request deletion / cancellation ─────────────────────────
@app.route("/api/request/<int:request_id>/request-deletion",
           methods=["POST"])
def request_deletion(request_id):
    try:
        body   = request.get_json()
        reason = body.get("reason", "")
        database.request_deletion(request_id, reason)
        return jsonify({
            "success": True,
            "message": "Deletion request submitted to Procurement."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/request/<int:request_id>/dismiss-deletion",
           methods=["POST"])
def dismiss_deletion(request_id):
    """Procurement dismisses a cancellation request."""
    try:
        conn = database.get_db()
        conn.execute("""
            UPDATE requests SET
                delete_requested=0,
                delete_reason=NULL,
                updated_at=datetime('now')
            WHERE id=?
        """, (request_id,))
        conn.commit()
        conn.close()
        database.log_action(
            request_id, "procurement_review",
            "cancellation_dismissed",
            actor_name="Procurement",
            actor_role="procurement_manager",
            notes="Cancellation request dismissed by Procurement"
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/request/<int:request_id>", methods=["DELETE"])
def delete_request(request_id):
    """Hard delete — procurement manager only."""
    try:
        req = database.get_request(request_id)
        if not req:
            return jsonify({"error": "Request not found"}), 404

        for att in req.get("attachments", []):
            delete_file_safe(att.get("filepath", ""))

        filepaths = database.delete_request_record(request_id)
        for fp in filepaths:
            delete_file_safe(fp)

        return jsonify({"success": True, "message": "Request deleted."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Assign tracking ID ───────────────────────────────────────
@app.route("/api/request/<int:request_id>/tracking-id",
           methods=["POST"])
def assign_tracking_id(request_id):
    try:
        body                 = request.get_json()
        tracking_id          = body.get("tracking_id", "").strip()
        assigned_by          = body.get("assigned_by", "Procurement")
        bid_process_sla_days = body.get("bid_process_sla_days")

        if not tracking_id:
            return jsonify({"error": "Tracking ID is required"}), 400

        success = database.assign_tracking_id(
            request_id, tracking_id, assigned_by)

        if not success:
            return jsonify({
                "error": f"Tracking ID '{tracking_id}' already in use"
            }), 409

        if bid_process_sla_days:
            conn = database.get_db()
            conn.execute("""
                UPDATE requests SET bid_process_sla_days=?
                WHERE id=?
            """, (int(bid_process_sla_days), request_id))
            conn.commit()
            conn.close()

        return jsonify({
            "success":     True,
            "tracking_id": tracking_id,
            "message":     f"Tracking ID {tracking_id} assigned."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Advance workflow stage ───────────────────────────────────
@app.route("/api/request/<int:request_id>/advance",
           methods=["POST"])
def advance_request(request_id):
    try:
        body       = request.get_json()
        action     = body.get("action")
        actor_name = body.get("actor_name", "Procurement")
        actor_role = body.get("actor_role", "procurement_manager")
        notes      = body.get("notes", "")

        if action not in ("approved", "rejected", "returned"):
            return jsonify({"error": "Invalid action"}), 400

        req_data = database.get_request(request_id)
        if not req_data:
            return jsonify({"error": "Request not found"}), 404

        current_stage = req_data["request"].get("current_stage", "")
        attachments   = req_data.get("attachments", [])
        active_attachments = [a for a in attachments
                              if a.get("status") == "active"]

        # ── Attachment gates ─────────────────────────────────────
        # Stage gates are configured in procurement_config.json
        # under stage_gates. Each stage can require a specific
        # document type before advancement is allowed.
        if action == "approved":
            from procurement_config import cfg as _cfg
            passes, gate_error = _cfg.stage_requires_document_type(
                current_stage, attachments
            )
            if not passes:
                return jsonify({"error": gate_error}), 400

        tracking_id = req_data["request"].get("tracking_id")

        result = workflow.advance_stage(
            request_id, action,
            actor_name, actor_role,
            notes=notes,
            tracking_id=tracking_id
        )

        # Auto-notify requester on key status changes
        new_stage = result.get("new_stage", "")
        if action == "returned":
            notify_requester(request_id, "returned", notes)
        elif action == "rejected":
            notify_requester(request_id, "rejected", notes)
        elif new_stage == "complete":
            notify_requester(request_id, "complete", notes)

        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Assign staff to stage ────────────────────────────────────
@app.route("/api/request/<int:request_id>/assign-staff",
           methods=["POST"])
def assign_staff(request_id):
    try:
        body        = request.get_json()
        staff_id    = body.get("staff_id")
        assigned_by = body.get("assigned_by", "Procurement")

        if not staff_id:
            return jsonify({"error": "staff_id is required"}), 400

        conn  = database.get_db()
        staff = conn.execute(
            "SELECT * FROM staff WHERE id=?", (staff_id,)
        ).fetchone()
        conn.close()

        if not staff:
            return jsonify({"error": "Staff not found"}), 404

        req_data = database.get_request(request_id)
        if not req_data:
            return jsonify({"error": "Request not found"}), 404

        current_stage = req_data["request"]["current_stage"]

        conn = database.get_db()
        conn.execute("""
            UPDATE assignments
            SET assigned_to_id=?, assigned_to_name=?
            WHERE request_id=? AND stage_code=?
              AND completed_at IS NULL
        """, (staff_id, staff["name"], request_id, current_stage))
        conn.commit()
        conn.close()

        database.log_action(
            request_id, current_stage,
            "staff_assigned",
            actor_name=assigned_by,
            actor_role="procurement_manager",
            notes=f"Assigned to {staff['name']} ({staff['email']})",
            tracking_id=req_data["request"].get("tracking_id")
        )

        return jsonify({
            "success":     True,
            "assigned_to": staff["name"],
            "email":       staff["email"],
            "phone":       staff["phone"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Status email ─────────────────────────────────────────────
@app.route("/api/request/<int:request_id>/status-email",
           methods=["POST"])
def send_status_email(request_id):
    try:
        body       = request.get_json()
        sent_by    = body.get("sent_by", "Procurement")
        extra_note = body.get("note", "")

        req_data = database.get_request(request_id)
        if not req_data:
            return jsonify({"error": "Request not found"}), 404

        req      = req_data["request"]
        timeline = workflow.get_timeline(request_id)

        subject = (
            f"Procurement Request Status \u2014 "
            f"{req.get('tracking_id') or 'Pending ID'}: "
            f"{req.get('draft_name') or req['item_name']}"
        )

        # Plain text timeline
        timeline_rows = ""
        for t in timeline:
            if t["completed_at"]:
                status_str = "Complete"
                days_str   = t["elapsed"]
            else:
                elapsed_days = max(0, (
                    datetime.now() - datetime.strptime(
                        t["assigned_at"], "%Y-%m-%d %H:%M:%S")
                ).days)
                days_str   = f"{elapsed_days}d"
                status_str = "Overdue" if t["overdue"] else "In Progress"

            timeline_rows += (
                f"{t['stage_name']:<30} "
                f"{t['assigned_to']:<22} "
                f"{days_str:<8} "
                f"{status_str}\n"
            )

        sep        = "=" * 60
        dash       = "-" * 60
        note_block = f"NOTE FROM PROCUREMENT:\n{extra_note}\n" \
                     if extra_note else ""

        body_text = (
            f"CITY OF PALM SPRINGS \u2014 PROCUREMENT STATUS UPDATE\n"
            f"{sep}\n\n"
            f"{'Tracking ID':<16}: "
            f"{req.get('tracking_id') or 'Pending Assignment'}\n"
            f"{'Request Name':<16}: "
            f"{req.get('draft_name') or req.get('item_name')}\n"
            f"{'Department':<16}: {req['department']}\n"
            f"{'Amount':<16}: ${req['amount']:,.2f}\n"
            f"{'Started':<16}: {req['created_at'][:10]}\n"
            f"{'Submitted':<16}: "
            f"{req['submitted_at'][:10] if req.get('submitted_at') else chr(8212)}\n"
            f"{'Current Stage':<16}: "
            f"{req['current_stage'].replace('_', ' ').title()}\n"
            f"{'Status':<16}: "
            f"{req['status'].replace('_', ' ').title()}\n\n"
            f"WORKFLOW TIMELINE:\n"
            f"{sep}\n"
            f"{'Stage':<30} {'Assigned To':<22} {'Days':<8} Status\n"
            f"{dash}\n"
            f"{timeline_rows}"
            f"{sep}\n"
            f"{note_block}\n"
            f"For questions contact the Procurement & Contracting Division.\n"
            f"This is an automated message from the CAPA Procurement Gateway."
        ).strip()

        # Log the notification
        recipient = req.get("requester_email") or req.get("requester_name")
        conn = database.get_db()
        conn.execute("""
            INSERT INTO notifications
                (request_id, recipient, subject, body, sent_by)
            VALUES (?, ?, ?, ?, ?)
        """, (request_id, recipient, subject, body_text, sent_by))
        conn.commit()
        conn.close()

        database.log_action(
            request_id, req["current_stage"],
            "status_email_sent",
            actor_name=sent_by,
            actor_role="procurement_manager",
            notes=f"Status email for {req.get('requester_name')}",
            tracking_id=req.get("tracking_id")
        )

        # Build HTML and send
        email_sent = False
        if recipient and "@" in recipient:
            html_email = build_status_email_html(req, timeline, extra_note)
            email_sent = send_email(recipient, subject,
                                    body_text, html_email)
        else:
            print(f"No valid email for request {request_id} — logged only.")

        return jsonify({
            "success":    True,
            "subject":    subject,
            "body":       body_text,
            "recipient":  recipient,
            "email_sent": email_sent,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Staff management ─────────────────────────────────────────
@app.route("/api/staff", methods=["GET"])
def get_staff():
    return jsonify(staff_module.get_all_staff())


@app.route("/api/staff/import", methods=["POST"])
def import_staff():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        f       = request.files["file"]
        content = f.read().decode("utf-8")
        result  = staff_module.import_from_csv(content)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/staff/template")
def staff_template():
    from flask import Response
    return Response(
        staff_module.get_csv_template(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=staff_template.csv"}
    )


@app.route("/api/staff/<int:staff_id>", methods=["DELETE"])
def deactivate_staff(staff_id):
    try:
        staff_module.deactivate_staff(staff_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Config endpoints ─────────────────────────────────────────
@app.route("/api/config/departments")
def get_departments():
    """Return department list from procurement config."""
    return jsonify({"departments": cfg._get("departments")})


@app.route("/api/config/document-types")
def get_document_types():
    """Return document types from procurement config."""
    from procurement_config import cfg
    return jsonify({"types": cfg.document_types()})


# ── Stage SLA ────────────────────────────────────────────────
@app.route("/api/stages", methods=["GET"])
def get_stages():
    return jsonify(database.get_stage_definitions())


@app.route("/api/stages/<stage_code>/sla", methods=["POST"])
def update_sla(stage_code):
    try:
        body     = request.get_json()
        sla_days = int(body.get("sla_days", 5))
        database.update_stage_sla(stage_code, sla_days)
        return jsonify({"success": True, "sla_days": sla_days})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stages/<stage_code>/staff")
def staff_for_stage(stage_code):
    department = request.args.get("department", "")
    return jsonify(
        staff_module.get_staff_for_stage(stage_code, department))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
