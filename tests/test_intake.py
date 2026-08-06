"""
Tests for intake.build_prompt() — specifically the REQUIRED DOCUMENTS FOR
THIS REQUEST section, which is rendered directly from the tenant config's
per-tier category/documents fields (see tenants/palm-springs/config.json).

policy_rag.query is mocked so these tests don't depend on Ollama or the
chroma store being populated — only the deterministic, config-driven part
of the prompt is under test here.
"""
from unittest.mock import patch

import intake

BASE_DATA = {
    "requester_name": "Test User",
    "department": "Parks",
    "requester_email": "test@example.com",
    "item_name": "Test item",
    "description": "Test description",
    "sole_source": "no",
    "federal_funds": "no",
    "faa_governed": "no",
    "pcard": "no",
}


def _required_documents_section(data: dict) -> str:
    with patch("policy_rag.query", return_value=""):
        prompt = intake.build_prompt(data)
    start = prompt.index("REQUIRED DOCUMENTS FOR THIS REQUEST")
    end = prompt.index("\n\n", start)
    return prompt[start:end]


class TestRequiredDocumentsSection:

    def test_goods_tier_lists_quote_and_po_only(self):
        data = {**BASE_DATA, "item_type": "equipment", "amount": 9_000}
        section = _required_documents_section(data)
        assert 'category is "goods"' in section
        assert "vendor quote, purchase order" in section
        assert "contract" not in section.split("required documents are exactly:")[1].split(".")[0]

    def test_service_tier_lists_contract_and_insurance(self):
        data = {**BASE_DATA, "item_type": "professional_services", "amount": 9_000}
        section = _required_documents_section(data)
        assert 'category is "service"' in section
        assert "service contract" in section
        assert "insurance certificate" in section

    def test_mixed_tier_lists_contract(self):
        data = {**BASE_DATA, "item_type": "construction", "amount": 50_000}
        section = _required_documents_section(data)
        assert 'category is "mixed"' in section
        assert "contract" in section

    def test_supplies_low_tier_is_goods_only(self):
        data = {**BASE_DATA, "item_type": "supplies", "amount": 300}
        section = _required_documents_section(data)
        assert 'category is "goods"' in section
        assert "vendor quote, purchase order" in section

    def test_instructs_model_not_to_add_documents(self):
        data = {**BASE_DATA, "item_type": "equipment", "amount": 9_000}
        section = _required_documents_section(data)
        assert "Do NOT add, infer, or mention any document not in this list" in section
