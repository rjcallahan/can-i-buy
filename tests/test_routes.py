"""
Integration tests for Flask routes.

External dependencies (OpenAI-compatible client, Resend email, policy_rag) are mocked
so these tests run without network access or API keys.

The Flask test client is provided by the `client` fixture in conftest.py.
"""
import base64
import json
import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────

def basic_auth_header(password: str, username: str = "admin") -> dict:
    """Build an HTTP Basic Auth header."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def make_ai_response(result: dict) -> list:
    """
    Return a one-chunk streaming mock for client.chat.completions.create(stream=True).
    The /analyze route iterates chunks and reads chunk.choices[0].delta.content.
    """
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = json.dumps(result)
    return [chunk]


def get_sse_result(response) -> dict:
    """Parse the final 'result' event from an SSE response."""
    for line in response.data.decode("utf-8").split("\n\n"):
        if line.startswith("data: "):
            event = json.loads(line[6:])
            if event.get("type") == "result":
                return event["data"]
    return None


# ── Representative payloads ────────────────────────────────────────

VALID_ANALYZE_PAYLOAD = {
    "requester_name":  "Test User",
    "requester_email": "test@palmspringsca.gov",
    "department":      "Finance",
    "item_name":       "Office Supplies",
    "item_type":       "supplies",
    "amount":          500,
    "description":     "Paper and pens",
    "pcard":           "yes",
    "sole_source":     "no",
    "federal_funds":   "no",
    "faa_governed":    "no",
}

MOCK_AI_RESULT = {
    "verdict":           "APPROVED",
    "procurement_method": "Single Quote / P-Card",
    "valid_methods": [
        {"method": "Single Quote / P-Card", "description": "One quote.", "documents_needed": []}
    ],
    "summary":           "This purchase can proceed via P-Card.",
    "missing_items":     [],
    "required_documents": [],
    "flags":             [],
    "next_steps":        [],
    "federal_notes":     "",
    "faa_notes":         "",
    "pcard_eligible":    True,
    "pcard_note":        "",
    "approval_chain":    [],
}


# ── Health & static routes ────────────────────────────────────────

class TestHealthAndStaticRoutes:

    def test_health_returns_200_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.data == b"ok"

    def test_root_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"html" in r.data.lower()

    def test_clear2buy_route_same_as_root(self, client):
        r = client.get("/clear2buy")
        assert r.status_code == 200
        assert b"html" in r.data.lower()

    def test_departments_endpoint_returns_list(self, client):
        r = client.get("/api/config/departments")
        assert r.status_code == 200
        data = r.get_json()
        assert "departments" in data
        assert isinstance(data["departments"], list)
        assert len(data["departments"]) > 0

    def test_departments_includes_expected_dept(self, client):
        r = client.get("/api/config/departments")
        depts = r.get_json()["departments"]
        assert "Finance" in depts
        assert "Airport" in depts


# ── Admin auth ────────────────────────────────────────────────────

class TestAdminAuth:

    def test_no_password_env_grants_access(self, client, monkeypatch):
        """If ADMIN_PASSWORD is unset, the endpoint is open (dev mode)."""
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        r = client.get("/admin/config/data")
        assert r.status_code == 200

    def test_wrong_password_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config/data", headers=basic_auth_header("wrongpassword"))
        assert r.status_code == 401

    def test_no_auth_header_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config/data")
        assert r.status_code == 401

    def test_correct_password_returns_200(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config/data", headers=basic_auth_header("secret123"))
        assert r.status_code == 200

    def test_correct_password_returns_config_json(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config/data", headers=basic_auth_header("secret123"))
        data = r.get_json()
        assert "bid_thresholds" in data
        assert "signing_authority" in data

    def test_admin_page_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config")
        assert r.status_code == 401

    def test_admin_page_with_correct_auth_returns_html(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.get("/admin/config", headers=basic_auth_header("secret123"))
        assert r.status_code == 200
        assert b"html" in r.data.lower()

    def test_admin_post_without_auth_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "secret123")
        r = client.post("/admin/config/data", json={"bid_thresholds": {}})
        assert r.status_code == 401


# ── /analyze endpoint ────────────────────────────────────────────

class TestAnalyzeEndpoint:

    def test_analyze_returns_verdict(self, client):
        with patch("app.client.chat.completions.create", return_value=make_ai_response(MOCK_AI_RESULT)), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=VALID_ANALYZE_PAYLOAD)
        assert r.status_code == 200
        data = get_sse_result(r)
        assert data is not None
        assert data["verdict"] == "APPROVED"

    def test_analyze_always_includes_approval_chain(self, client):
        """The route should always append approval_chain, overwriting the model's."""
        with patch("app.client.chat.completions.create", return_value=make_ai_response({**MOCK_AI_RESULT, "approval_chain": []})), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json={**VALID_ANALYZE_PAYLOAD, "amount": 10_000})
        data = get_sse_result(r)
        assert "approval_chain" in data
        assert len(data["approval_chain"]) >= 1
        assert any(s["approves"] for s in data["approval_chain"])

    def test_analyze_sanitizes_pcard_from_methods(self, client):
        """P-Card references should be stripped from valid_methods by the time it reaches the client."""
        result_with_pcard = {**MOCK_AI_RESULT, "valid_methods": [
            {"method": "Single Quote / P-Card", "description": "One quote.", "documents_needed": []},
        ]}
        with patch("app.client.chat.completions.create", return_value=make_ai_response(result_with_pcard)), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=VALID_ANALYZE_PAYLOAD)
        data = get_sse_result(r)
        for m in data.get("valid_methods", []):
            assert "p-card" not in m["method"].lower(), f"P-Card not stripped: {m['method']}"

    def test_analyze_handles_markdown_fenced_json(self, client):
        """Model sometimes wraps output in ```json fences — app must handle this gracefully."""
        wrapped = "```json\n" + json.dumps(MOCK_AI_RESULT) + "\n```"
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = wrapped
        with patch("app.client.chat.completions.create", return_value=[chunk]), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=VALID_ANALYZE_PAYLOAD)
        assert get_sse_result(r) is not None

    def test_analyze_handles_think_block(self, client):
        """DeepSeek-style <think>...</think> blocks must be stripped before JSON parsing."""
        wrapped = "<think>reasoning here</think>\n" + json.dumps(MOCK_AI_RESULT)
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = wrapped
        with patch("app.client.chat.completions.create", return_value=[chunk]), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=VALID_ANALYZE_PAYLOAD)
        data = get_sse_result(r)
        assert data["verdict"] == "APPROVED"

    def test_analyze_missing_body_returns_400(self, client):
        r = client.post("/analyze", data="", content_type="application/json")
        assert r.status_code == 400

    def test_analyze_invalid_json_body_returns_400(self, client):
        r = client.post("/analyze", data="not json", content_type="application/json")
        assert r.status_code == 400

    def test_analyze_approval_chain_correct_for_small_purchase(self, client):
        payload = {**VALID_ANALYZE_PAYLOAD, "amount": 5_000, "item_type": "supplies"}
        with patch("app.client.chat.completions.create", return_value=make_ai_response(MOCK_AI_RESULT)), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=payload)
        chain = get_sse_result(r)["approval_chain"]
        # $5,000 supplies → director approves solo
        assert len(chain) == 1
        assert chain[0]["role"] == "director"
        assert chain[0]["approves"] is True

    def test_analyze_approval_chain_correct_for_large_purchase(self, client):
        payload = {**VALID_ANALYZE_PAYLOAD, "amount": 100_000, "item_type": "supplies"}
        with patch("app.client.chat.completions.create", return_value=make_ai_response(MOCK_AI_RESULT)), \
             patch("intake.build_prompt", return_value="test prompt"):
            r = client.post("/analyze", json=payload)
        chain = get_sse_result(r)["approval_chain"]
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "city_manager"


# ── /api/send-report endpoint ─────────────────────────────────────

class TestSendReportEndpoint:

    BASE_PAYLOAD = {
        "data": {
            "item_name": "Test Item",
            "amount": 500,
            "item_type": "supplies",
            "description": "Test purchase",
        },
        "result": {
            "verdict": "APPROVED",
            "summary": "OK to proceed.",
            "valid_methods": [],
            "approval_chain": [],
        },
    }

    def test_invalid_email_format_returns_400(self, client):
        r = client.post("/api/send-report", json={**self.BASE_PAYLOAD, "email": "not-an-email"})
        assert r.status_code == 400

    def test_disallowed_domain_returns_400(self, client):
        r = client.post("/api/send-report", json={**self.BASE_PAYLOAD, "email": "user@outsider.com"})
        assert r.status_code == 400

    def test_missing_email_returns_400(self, client):
        r = client.post("/api/send-report", json={**self.BASE_PAYLOAD, "email": ""})
        assert r.status_code == 400

    def test_allowed_city_domain_sends_report(self, client):
        with patch("app.send_email", return_value=True):
            r = client.post("/api/send-report", json={
                **self.BASE_PAYLOAD, "email": "staff@palmspringsca.gov"
            })
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

    def test_allowlisted_address_sends_report(self, client):
        with patch("app.send_email", return_value=True):
            r = client.post("/api/send-report", json={
                **self.BASE_PAYLOAD, "email": "rjames.callahan@gmail.com"
            })
        assert r.status_code == 200

    def test_email_send_failure_returns_500(self, client):
        with patch("app.send_email", return_value=False):
            r = client.post("/api/send-report", json={
                **self.BASE_PAYLOAD, "email": "staff@palmspringsca.gov"
            })
        assert r.status_code == 500


# ── /api/send-ss-report endpoint ──────────────────────────────────

class TestSendSsReportEndpoint:

    BASE_PAYLOAD = {
        "filename": "sole-source-letter.pdf",
        "result": {
            "strength": "adequate",
            "ready_to_submit": False,
            "recommendation": "Strengthen the justification.",
            "checks": [],
            "flags": [],
        },
    }

    def test_disallowed_email_returns_400(self, client):
        r = client.post("/api/send-ss-report", json={
            **self.BASE_PAYLOAD, "email": "user@outsider.com"
        })
        assert r.status_code == 400

    def test_allowed_email_sends_report(self, client):
        with patch("app.send_email", return_value=True):
            r = client.post("/api/send-ss-report", json={
                **self.BASE_PAYLOAD, "email": "staff@palmspringsca.gov"
            })
        assert r.status_code == 200
        assert r.get_json()["ok"] is True


# ── /api/admin/ingest endpoint ────────────────────────────────────

class TestIngestEndpoint:

    def test_wrong_secret_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("INGEST_SECRET", "real-secret")
        r = client.post("/api/admin/ingest", headers={"X-Ingest-Secret": "wrong"})
        assert r.status_code == 401

    def test_missing_secret_header_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("INGEST_SECRET", "real-secret")
        r = client.post("/api/admin/ingest")
        assert r.status_code == 401

    def test_correct_secret_triggers_ingest(self, client, monkeypatch):
        monkeypatch.setenv("INGEST_SECRET", "real-secret")
        with patch("policy_rag.ingest", return_value=12):
            r = client.post("/api/admin/ingest", headers={"X-Ingest-Secret": "real-secret"})
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["documents_ingested"] == 12


# ── /api/admin/rag-status endpoint ───────────────────────────────

class TestRagStatusEndpoint:

    def test_rag_status_returns_ready_flag(self, client):
        with patch("policy_rag.is_ready", return_value=False), \
             patch("policy_rag.docs_path", return_value="/data/documents"), \
             patch("policy_rag._CHROMA_PATH", "/data/chroma_db"):
            r = client.get("/api/admin/rag-status")
        assert r.status_code == 200
        data = r.get_json()
        assert "ready" in data
        assert data["ready"] is False
