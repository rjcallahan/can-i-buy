# -*- coding: utf-8 -*-
# app.py
import os
import json
import re
import base64

import datetime
from flask import Flask, request, jsonify, send_from_directory, Response
from dotenv import load_dotenv
import anthropic
from anthropic.types import TextBlock

import intake
from procurement_config import cfg

load_dotenv()

app = Flask(__name__, static_folder="static")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Email helper ─────────────────────────────────────────────

def send_email(recipient: str, subject: str, body: str,
               html: str | None = None) -> bool:
    import requests as req_lib

    api_key        = os.getenv("RESEND_API_KEY")
    smtp_from      = os.getenv("SMTP_FROM", "")
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "Procurement")

    if not api_key:
        print("RESEND_API_KEY not set — email not sent.")
        return False

    if not smtp_from:
        print("SMTP_FROM not set — email not sent.")
        return False

    try:
        resp = req_lib.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "from":    f"{smtp_from_name} <{smtp_from}>",
                "to":      [recipient],
                "subject": subject,
                "text":    body,
                "html":    html or body,
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"Email sent to {recipient}: {subject}")
        return True
    except Exception as e:
        print(f"Email send failed [{type(e).__name__}]: {e}")
        return False


def _email_allowed(email: str) -> bool:
    domain  = cfg.allowed_email_domain()
    allowed = set(cfg.allowed_email_addresses())
    return email.endswith(domain) or email in allowed


# ── Approval chain helper ─────────────────────────────────────

def compute_approval_chain(amount: float, item_type: str) -> list:
    IT_TYPES = {"it_equipment", "it_software"}

    is_public     = item_type == "construction"
    levels        = cfg.signing_authority_levels()
    required_role = cfg.approval_role(amount, is_public)
    required_idx  = next(
        (i for i, l in enumerate(levels) if l["role"] == required_role),
        len(levels) - 1
    )

    if item_type in IT_TYPES:
        dept_head = {"role": "it_director", "label": "IT Director", "note": ""}
    else:
        dept_head = {"role": "director", "label": "Department Director", "note": ""}

    if required_idx == 0:
        return [{**dept_head, "approves": True}]

    signing = levels[required_idx]
    return [
        {**dept_head, "approves": False},
        {"role": signing["role"], "label": signing["label"],
         "note": "", "approves": True},
    ]


# ── Response sanitization ─────────────────────────────────────

_PCARD_PATTERN = re.compile(r'\s*/\s*P-?[Cc]ard|P-?[Cc]ard\s*/\s*', re.IGNORECASE)
_CURRENT_CAT   = re.compile(
    r'[Uu]nder the current[^.]*categorization[^.]*\.?\s*', re.IGNORECASE
)

def _sanitize_result(result: dict) -> None:
    """Remove P-Card from valid_methods names and scrub stale categorization text."""
    for m in result.get("valid_methods", []):
        m["method"] = _PCARD_PATTERN.sub("", m.get("method", "")).strip(" /,")

    result["valid_methods"] = [
        m for m in result.get("valid_methods", [])
        if m.get("method", "").strip()
        and "p-card" not in m.get("method", "").lower()
    ]

    for key in ("summary", "flags"):
        val = result.get(key)
        if isinstance(val, str):
            result[key] = _CURRENT_CAT.sub("", val).strip()
        elif isinstance(val, list):
            result[key] = [_CURRENT_CAT.sub("", f).strip() for f in val]


# ── Static routes ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "can-i-buy-this.html")


@app.route("/can-i-buy-this")
def can_i_buy_this():
    return send_from_directory("static", "can-i-buy-this.html")


@app.route("/health")
def health():
    return "ok", 200


# ── AI analysis ───────────────────────────────────────────────

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data received"}), 400

        prompt = intake.build_prompt(data)

        model = cfg.ai_model()
        message = client.messages.create(
            model=model,
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
            cleaned = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)

        amount    = float(data.get("amount") or 0)
        item_type = data.get("item_type", "other")
        result["approval_chain"] = compute_approval_chain(amount, item_type)
        _sanitize_result(result)

        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse AI response: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Send analysis report via email ────────────────────────────

@app.route("/api/send-report", methods=["POST"])
def send_can_i_buy_report():
    body  = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()

    email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    if not email_re.match(email):
        return jsonify({"error": "Invalid email address."}), 400
    if not _email_allowed(email):
        domain = cfg.allowed_email_domain()
        return jsonify({"error": f"Must be a {domain} address."}), 400

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

    item_type  = data.get("item_type", "other")
    chain      = result.get("approval_chain") or compute_approval_chain(float(amount), item_type)
    chain_rows = "".join(
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


# ── Sole source letter analysis ───────────────────────────────

@app.route("/api/analyze-sole-source", methods=["POST"])
def analyze_sole_source():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "No filename"}), 400
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"error": "PDF files only"}), 400

        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

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

        model = cfg.ai_model()
        message = client.messages.create(
            model=model,
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
                    {"type": "text", "text": prompt}
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


# ── Send sole source report via email ─────────────────────────

@app.route("/api/send-ss-report", methods=["POST"])
def send_ss_report():
    body  = request.get_json(force=True) or {}
    email = (body.get("email") or "").strip().lower()

    email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    if not email_re.match(email):
        return jsonify({"error": "Invalid email address."}), 400
    if not _email_allowed(email):
        domain = cfg.allowed_email_domain()
        return jsonify({"error": f"Must be a {domain} address."}), 400

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

    city_name = cfg.city_name() or "Procurement"
    html_body = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:660px;margin:0 auto">
      <div style="background:#1f3864;padding:14px 24px;border-radius:6px 6px 0 0">
        <span style="color:#fff;font-size:17px;font-weight:700">{city_name}</span>
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


# ── Config endpoints ──────────────────────────────────────────

@app.route("/api/config/departments")
def get_departments():
    return jsonify({"departments": cfg._get("departments")})


# ── Admin config editor ───────────────────────────────────────

def _admin_authorized() -> bool:
    pwd = os.getenv("ADMIN_PASSWORD")
    if not pwd:
        return True
    auth = request.authorization
    return bool(auth and auth.password == pwd)


@app.route("/admin/config")
def admin_config_page():
    if not _admin_authorized():
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="Procurement Admin"'}
        )
    return send_from_directory("static", "admin-config.html")


@app.route("/api/admin/config", methods=["GET"])
def api_admin_config_get():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    with open(cfg._path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/admin/config", methods=["POST"])
def api_admin_config_post():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    data["_last_updated"] = datetime.date.today().isoformat()
    with open(cfg._path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    cfg.reload()
    return jsonify({"ok": True})


# ── Vector store admin ────────────────────────────────────────

@app.route("/api/admin/ingest", methods=["POST"])
def admin_ingest():
    """
    Trigger re-ingestion of policy documents into ChromaDB.
    Requires INGEST_SECRET header to match the INGEST_SECRET env var.
    """
    import policy_rag

    secret = os.getenv("INGEST_SECRET")
    if secret and request.headers.get("X-Ingest-Secret") != secret:
        return jsonify({"error": "Unauthorized"}), 401

    if not os.getenv("OPENAI_API_KEY"):
        return jsonify({"error": "OPENAI_API_KEY not configured"}), 500

    try:
        count = policy_rag.ingest()
        return jsonify({"ok": True, "documents_ingested": count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/rag-status")
def rag_status():
    """Return whether the vector store is populated."""
    import policy_rag
    return jsonify({
        "ready":      policy_rag.is_ready(),
        "docs_path":  policy_rag.docs_path(),
        "store_path": policy_rag._CHROMA_PATH,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
