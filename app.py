# app.py
import datetime
import json
import os
import re
import sys

# Optional CLI arg: `python app.py <tenant>` selects the tenant for this run,
# overriding any TENANT already set in the shell. Must run before the
# tenant-aware imports below (usage_db, procurement_config) so they pick it up.
# Gated on __main__ so gunicorn's own argv (e.g. "app:app") is never mistaken
# for a tenant name in production.
if __name__ == "__main__" and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    os.environ["TENANT"] = sys.argv[1]

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
)
from openai import OpenAI

import intake
import usage_db
from procurement_config import cfg

load_dotenv()
usage_db.init()

app = Flask(__name__, static_folder="static")

app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
app.permanent_session_lifetime = datetime.timedelta(days=30)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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


def _is_admin(email: str) -> bool:
    master = os.getenv("ADMIN_USERNAME", "").strip().lower()
    return bool(email) and (email == master or email in cfg.admin_emails())


# ── Auth gate ─────────────────────────────────────────────────

@app.before_request
def require_login():
    path = request.path
    if path in ('/login', '/health', '/dashboard', '/api/config/city-name') or path.startswith(('/auth/', '/static/', '/admin', '/api/admin')):
        return
    if session.get('user_email'):
        return
    if request.method == 'POST' or path.startswith('/api/') or path == '/analyze':
        return jsonify({"error": "Session expired. Please sign in again.", "redirect": "/login"}), 401
    return redirect('/login')


# ── Approval chain helper ─────────────────────────────────────

def compute_approval_chain(amount: float, item_type: str) -> list:
    IT_TYPES = {"it_equipment", "it_software"}

    is_public     = item_type == "construction"
    levels        = cfg.signing_authority_levels()
    required_role = cfg.approval_role(amount, is_public)
    required_idx  = next(
        (i for i, lvl in enumerate(levels) if lvl["role"] == required_role),
        len(levels) - 1
    )
    director_idx  = next(
        (i for i, lvl in enumerate(levels) if lvl["role"] == "director"),
        0
    )

    if item_type in IT_TYPES:
        dept_head = {"role": "it_director", "label": "IT Director", "note": ""}
    else:
        dept_head = {"role": "director", "label": "Department Director / Chief", "note": ""}

    # At or below director level: show only the required signer, no chain above
    if required_idx <= director_idx:
        signing = levels[required_idx]
        label = "IT Director" if item_type in IT_TYPES and signing["role"] == "director" else signing["label"]
        return [{"role": signing["role"], "label": label, "note": "", "approves": True}]

    # Above director: dept head routes up to required signer
    signing = levels[required_idx]
    return [
        {**dept_head, "approves": False},
        {"role": signing["role"], "label": signing["label"],
         "note": "", "approves": True},
    ]


# ── Processing time estimate ───────────────────────────────────

def compute_processing_time(data: dict, result: dict) -> dict | None:
    """
    Classify this request into one of the tenant's configured processing_time
    classes and return the day-range estimate. Returns None if the tenant
    hasn't set up processing_time config yet.
    """
    try:
        pt = cfg.raw("processing_time")
    except KeyError:
        return None

    item_type = data.get("item_type", "other")
    amount    = float(data.get("amount") or 0)
    speed_key = "expedited_days" if data.get("process_timing") == "expedited" else "normal_days"

    if item_type == "construction":
        group = "construction"
        if amount <= 74999.99:
            class_key = "under_75k"
        elif amount <= 219999.99:
            class_key = "informal_bid"
        else:
            class_key = "formal_bid"
    else:
        group = "general"
        if result.get("pcard_eligible"):
            class_key = (
                "pcard_standard" if amount <= cfg.pcard_transaction_limit()
                else "pcard_limit_increase"
            )
        elif cfg.requires_competitive_bid(item_type, amount):
            chain  = result.get("approval_chain") or []
            signer = next((s for s in chain if s.get("approves")), None)
            role   = signer["role"] if signer else ""
            class_key = "formal_bid_council" if role == "city_council" else "formal_bid_acm_cm"
        else:
            tiers = cfg.procurement_methods(item_type)
            idx   = next((i for i, t in enumerate(tiers) if amount <= t["max"]), 0)
            class_key = "informal_3_quotes" if idx >= 1 else "single_quote"

    entry = (pt.get(group) or {}).get(class_key)
    if not entry:
        return None

    low, high  = (entry.get(speed_key) or [0, 0])[:2]
    days_label = str(low) if low == high else f"{low}-{high}"
    unit       = "day" if low == high == 1 else "days"

    disclaimer = (
        f"If your supporting documentation is ready and complete, submitting this "
        f"requisition should take about {days_label} {unit} to process. Procurement "
        f"times vary and depend on complete, accurate submissions, the complexity "
        f"of the request, the availability of required signers, current workloads, "
        f"and other factors. This is only an estimate, to help you plan your "
        f"purchase and receive it on time."
    )

    warning = ""
    required_date = data.get("required_date")
    if data.get("process_timing") == "expedited" and required_date:
        try:
            days_until = (datetime.date.fromisoformat(required_date) - datetime.date.today()).days
        except ValueError:
            days_until = None
        if days_until is not None and days_until < low:
            warning = (
                f"Your required-by date is {days_until} day{'s' if days_until != 1 else ''} away, "
                f"but even on the fastest expedited track this class of purchase takes about "
                f"{days_label} {unit}. Please plan for a later date or contact Procurement to "
                f"discuss options."
            )

    return {
        "class_key":  class_key,
        "label":      entry.get("label", ""),
        "days_min":   low,
        "days_max":   high,
        "disclaimer": disclaimer,
        "caveat":     entry.get("caveat", ""),
        "warning":    warning,
    }


# ── Response sanitization ─────────────────────────────────────

_PCARD_PATTERN = re.compile(r'\s*/\s*P-?[Cc]ard|P-?[Cc]ard\s*/\s*', re.IGNORECASE)
_CURRENT_CAT   = re.compile(
    r'[Uu]nder the current[^.]*categorization[^.]*\.?\s*', re.IGNORECASE
)

def _parse_ai_json(content: str) -> dict:
    """Strip DeepSeek <think> blocks and markdown fences, then parse JSON."""
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)


def _sanitize_result(result: dict) -> None:
    """Remove P-Card from valid_methods names and scrub stale categorization text."""
    for m in result.get("valid_methods", []):
        m["method"] = _PCARD_PATTERN.sub("", m.get("method", "")).strip(" /,")

    seen_methods: set[str] = set()
    deduped = []
    for m in result.get("valid_methods", []):
        name = m.get("method", "").strip()
        key = name.lower()
        if name and "p-card" not in key and key not in seen_methods:
            seen_methods.add(key)
            deduped.append(m)
    result["valid_methods"] = deduped

    for key in ("summary", "flags"):
        val = result.get(key)
        if isinstance(val, str):
            val = _PCARD_PATTERN.sub("", val).strip(" /,")
            result[key] = _CURRENT_CAT.sub("", val).strip()
        elif isinstance(val, list):
            result[key] = [_CURRENT_CAT.sub("", f).strip() for f in val]


# ── Auth routes ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return send_from_directory("static", "login.html")

    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()

    email_re = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    if not email_re.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400

    if app.debug and email == "rjames.callahan@gmail.com":
        session.permanent = True
        session["user_email"] = email
        return jsonify({"ok": True, "dev_link": "/"})

    if not _email_allowed(email):
        domain = cfg.allowed_email_domain()
        return jsonify({"error": f"Must be a {domain} email address."}), 400

    token = usage_db.create_magic_token(email)
    base_url = os.getenv("BASE_URL", request.host_url.rstrip("/"))
    link = f"{base_url}/auth/{token}"

    sent = send_email(
        email,
        "Your Clear2Buy sign-in link",
        f"Click to sign in to Clear2Buy:\n\n{link}\n\nThis link expires in 15 minutes.",
        f"""<div style="font-family:sans-serif;max-width:480px;margin:0 auto">
          <h2 style="color:#1f3864">Clear2Buy Sign-In</h2>
          <p>Click the button below to sign in. This link expires in <strong>15 minutes</strong>.</p>
          <p style="margin:24px 0">
            <a href="{link}" style="background:#1f3864;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;font-size:15px">Sign in to Clear2Buy</a>
          </p>
          <p style="font-size:12px;color:#888">If you didn't request this, ignore this email.</p>
        </div>""",
    )

    if not sent:
        # Dev mode: RESEND not configured — return link directly
        return jsonify({"ok": True, "dev_link": link})

    return jsonify({"ok": True})


@app.route("/auth/<token>")
def auth_callback(token):
    email = usage_db.consume_magic_token(token)
    if not email:
        return redirect("/login?error=invalid")
    session.permanent = True
    session["user_email"] = email
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── Static routes ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "clear2buy.html")


@app.route("/clear2buy")
def clear2buy():
    return send_from_directory("static", "clear2buy.html")


@app.route("/health")
def health():
    return "ok", 200


# ── AI analysis ───────────────────────────────────────────────

def _run_analysis(data: dict) -> dict:
    """Core analysis logic — shared by SSE and test paths."""
    prompt = intake.build_prompt(data)
    model = cfg.ai_model()
    full_content = ""
    stream = client.chat.completions.create(
        model=model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            full_content += delta
    result = _parse_ai_json(full_content)
    amount    = float(data.get("amount") or 0)
    item_type = data.get("item_type", "other")
    result["approval_chain"] = compute_approval_chain(amount, item_type)
    _sanitize_result(result)
    pcard_cap = float((cfg._get("pcard") or {}).get("purchase_cap", float("inf")))
    if amount > pcard_cap:
        result["pcard_eligible"] = False
        result["pcard_note"] = (
            f"P-Card is not permitted for purchases over ${pcard_cap:,.0f}. "
            "A formal requisition is required."
        )
    result["processing_time"] = compute_processing_time(data, result)
    result["log_id"] = usage_db.log_analysis(data, result)
    return result


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data received"}), 400
    data["requester_email"] = session.get("user_email", "")

    # Skip SSE overhead in tests — return a single SSE-formatted result event
    # so test helpers (get_sse_result) still work without Werkzeug streaming delay.
    if app.testing:
        try:
            result = _run_analysis(data)
            body = f"data: {json.dumps({'type': 'result', 'data': result})}\n\n"
            return Response(body, mimetype="text/event-stream")
        except json.JSONDecodeError as e:
            return jsonify({"error": f"Failed to parse AI response: {e!s}"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _sse(event: dict) -> str:
        return f"data: {json.dumps(event)}\n\n"

    def generate():
        try:
            yield _sse({"type": "stage", "message": "Reviewing policy documents\u2026"})
            prompt = intake.build_prompt(data)
            yield _sse({"type": "stage", "message": "Analyzing your request\u2026"})
            model = cfg.ai_model()
            full_content = ""
            first_token = True
            stream = client.chat.completions.create(
                model=model,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_content += delta
                    if first_token:
                        yield _sse({"type": "stage", "message": "Generating recommendation\u2026"})
                        first_token = False
                    else:
                        yield _sse({"type": "token"})
            result = _parse_ai_json(full_content)
            amount    = float(data.get("amount") or 0)
            item_type = data.get("item_type", "other")
            result["approval_chain"] = compute_approval_chain(amount, item_type)
            _sanitize_result(result)
            pcard_cap = float((cfg._get("pcard") or {}).get("purchase_cap", float("inf")))
            if amount > pcard_cap:
                result["pcard_eligible"] = False
                result["pcard_note"] = (
                    f"P-Card is not permitted for purchases over ${pcard_cap:,.0f}. "
                    "A formal requisition is required."
                )
            result["processing_time"] = compute_processing_time(data, result)
            result["log_id"] = usage_db.log_analysis(data, result)
            yield _sse({"type": "result", "data": result})
        except json.JSONDecodeError as e:
            yield _sse({"type": "error", "message": f"Failed to parse AI response: {e!s}"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Send analysis report via email ────────────────────────────

@app.route("/api/send-report", methods=["POST"])
def send_clear2buy_report():
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

    item_name      = data.get("item_name",  "Unknown item")
    description    = data.get("description", "")
    amount         = data.get("amount", 0)
    process_timing = data.get("process_timing", "normal")
    required_date  = data.get("required_date", "")
    verdict        = result.get("verdict", "")
    summary     = result.get("summary", "")

    verdict_label = {"APPROVED": "✅ Valid", "FLAGGED": "🚩 Flagged",
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

    proc_time = result.get("processing_time")
    proc_time_html = (
        f"<h3 style='color:#1f3864'>⏱ Estimated Processing Time</h3>"
        + (f"<p style='background:#fff4e5;border:1px solid #f0c674;border-radius:6px;padding:8px 10px;font-size:13px;color:#8a5a00'>⚠️ {proc_time['warning']}</p>" if proc_time.get("warning") else "")
        + f"<p style='font-size:13px;color:#333'>{proc_time['disclaimer']}</p>"
        + (f"<p style='font-size:12px;color:#888'>{proc_time['caveat']}</p>" if proc_time.get("caveat") else "")
    ) if proc_time else ""

    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#1f3864">Clear2Buy — Policy Report</h2>
      <p><strong>Item:</strong> {item_name}</p>
      <p><strong>Estimated Cost:</strong> ${amount:,.2f}</p>
      {f'<p><strong>Timing:</strong> Expedited — required by {required_date}</p>' if process_timing == "expedited" else ''}
      <p><strong>Description:</strong> {description}</p>
      <hr style="border:none;border-top:1px solid #e0e8f0">
      <p><strong>Verdict:</strong> {verdict_label}</p>
      <p>{summary}</p>
      {"<h3 style='color:#1f3864'>Procurement Path(s)</h3><ul>" + methods_html + "</ul>" if methods_html else ""}
      {chain_html}
      {proc_time_html}
      {f'<h3 style="color:#1f3864">✈️ FAA Compliance Notes</h3><p style="background:#f0f4ff;padding:10px 12px;border-radius:6px;font-size:13px">{result.get("faa_notes")}</p>' if result.get("faa_notes") else ""}
      <hr style="border:none;border-top:1px solid #e0e8f0">
      <p style="font-size:12px;color:#888">This is a policy check only — nothing has been formally submitted.</p>
    </div>"""

    text_body = (
        f"Clear2Buy — Policy Report\n\n"
        f"Item: {item_name}\nEstimated Cost: ${amount:,.2f}\n"
        + (f"Timing: Expedited — required by {required_date}\n" if process_timing == "expedited" else "")
        + f"Description: {description}\n\n"
        f"Verdict: {verdict_label}\n{summary}\n\n"
        + ("Procurement Path(s):\n" + "\n".join(f"- {m['method']}" for m in methods) + "\n\n" if methods else "")
        + (f"WARNING: {proc_time['warning']}\n\n" if proc_time and proc_time.get("warning") else "")
        + (f"Estimated Processing Time:\n{proc_time['disclaimer']}\n" if proc_time else "")
        + (f"{proc_time['caveat']}\n" if proc_time and proc_time.get("caveat") else "")
    )

    success = send_email(email, f"Procurement Policy Check: {item_name}", text_body, html_body)
    if success is False:
        return jsonify({"error": "Email send failed. Check SMTP configuration."}), 500
    usage_db.mark_analysis_emailed(result.get("log_id"), email)
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

        import fitz  # PyMuPDF
        pdf_bytes = f.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pdf_text = "\n".join(str(page.get_text()) for page in pdf_doc)
        pdf_doc.close()

        # Process optional supporting documents
        support_texts = []
        for sup in request.files.getlist("support_files"):
            if sup and sup.filename and sup.filename.lower().endswith(".pdf"):
                try:
                    sup_doc = fitz.open(stream=sup.read(), filetype="pdf")
                    sup_text = "\n".join(str(page.get_text()) for page in sup_doc)
                    sup_doc.close()
                    if sup_text.strip():
                        support_texts.append(f"[Supporting document: {sup.filename}]\n{sup_text.strip()}")
                except Exception:
                    pass

        # Build interview context from requester responses
        interview_fields = [
            ("q1", "Why competition is not possible"),
            ("q2", "Market research performed"),
            ("q3", "Vendors considered"),
            ("q4", "Evidence only one vendor qualifies"),
            ("q5", "Impact if another vendor selected"),
            ("q6", "Proprietary rights involved"),
            ("q7", "Compatibility technically required"),
            ("q8", "Emergency status"),
            ("q9", "Acceptable alternatives"),
        ]
        interview_parts = [
            f"- {label}: {val}"
            for key, label in interview_fields
            if (val := request.form.get(key, "").strip())
        ]
        interview_context = (
            "REQUESTER INTERVIEW RESPONSES:\n" + "\n".join(interview_parts) + "\n\n"
            if interview_parts else ""
        )

        all_docs = pdf_text
        if support_texts:
            all_docs += "\n\n" + "\n\n".join(support_texts)

        prompt = f"""You are a public procurement compliance officer reviewing a sole source justification letter.

Evaluate this letter strictly against the recognized legal bases for sole source procurement under {cfg.city_name()} procurement policy.

""" + """RECOGNIZED JUSTIFICATION TESTS (check each one):
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
        message = client.chat.completions.create(
            model=model,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": f"DOCUMENT CONTENT:\n{all_docs}\n\n{interview_context}{prompt}"
            }],
            response_format={"type": "json_object"},
        )

        content = message.choices[0].message.content or ""
        result = _parse_ai_json(content)

        result["log_id"] = usage_db.log_sole_source(
            f.filename, result, session.get("user_email", ""), file_bytes=pdf_bytes
        )
        return jsonify(result)

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Failed to parse AI response: {e!s}"}), 500
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
    usage_db.mark_sole_source_emailed(r.get("log_id"), email)
    return jsonify({"ok": True})


# ── Config endpoints ──────────────────────────────────────────

@app.route("/api/config/departments")
def get_departments():
    return jsonify({"departments": cfg._get("departments")})


@app.route("/api/config/city-name")
def get_city_name():
    return jsonify({"city_name": cfg.city_name()})


@app.route("/api/config/mail-domain")
def get_mail_domain():
    return jsonify({
        "allowed_domain":    cfg.allowed_email_domain(),
        "allowed_addresses": cfg.allowed_email_addresses(),
    })


@app.route("/api/config/is-admin")
def get_is_admin():
    return jsonify({"is_admin": _is_admin(session.get("user_email", ""))})


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


@app.route("/admin/config/data", methods=["GET"])
def api_admin_config_get():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    with open(cfg._path, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/admin/config/data", methods=["POST"])
def api_admin_config_post():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400
    data["_last_updated"] = datetime.datetime.now(datetime.UTC).date().isoformat()
    with open(cfg._path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    cfg.reload()
    return jsonify({"ok": True})


# ── Usage reporting ───────────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    return redirect("/admin/usage")


@app.route("/admin/usage/data")
def admin_usage():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    totals = usage_db.totals()
    totals["city_name"] = cfg.city_name()
    return jsonify({
        "totals":       totals,
        "summary":      usage_db.monthly_summary(),
        "analyses":     usage_db.recent_analyses(),
        "sole_source":  usage_db.recent_sole_source(),
    })


@app.route("/admin/usage/archive", methods=["POST"])
def admin_usage_archive():
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    kind = body.get("type")
    log_id = body.get("id")
    archived = bool(body.get("archived"))
    if kind == "analysis":
        usage_db.set_analysis_archived(log_id, archived)
    elif kind == "sole_source":
        usage_db.set_sole_source_archived(log_id, archived)
    else:
        return jsonify({"error": "Invalid type"}), 400
    return jsonify({"ok": True})


@app.route("/admin/usage/sole-source/<int:log_id>/file")
def admin_usage_sole_source_file(log_id):
    if not _admin_authorized():
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="Procurement Admin"'}
        )
    found = usage_db.get_sole_source_file(log_id)
    if not found:
        return jsonify({"error": "No file stored for this record"}), 404
    filename, file_bytes = found
    return Response(
        file_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.route("/admin/usage")
def admin_usage_page():
    if not _admin_authorized():
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="Procurement Admin"'}
        )
    return send_from_directory("static", "admin-usage.html")


@app.route("/admin/db/download")
def admin_db_download():
    if not _admin_authorized():
        return Response(
            "Unauthorized", 401,
            {"WWW-Authenticate": 'Basic realm="Procurement Admin"'}
        )
    import shutil
    import tempfile
    db_path = usage_db._DB_PATH
    # Copy first to avoid streaming a live write-locked file
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shutil.copy2(db_path, tmp_path)
    return send_from_directory(
        os.path.dirname(tmp_path),
        os.path.basename(tmp_path),
        as_attachment=True,
        download_name="procurement.db",
    )


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
