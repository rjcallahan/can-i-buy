"""
Tests for pure helper functions in app.py.

These functions have no external dependencies (no Claude, no email, no DB)
so they run fast and test the core business logic directly:

  _email_allowed()          — domain / allowlist gate for report sending
  compute_approval_chain()  — which roles must approve a purchase
  _sanitize_result()        — clean up AI output before returning to client
"""
import pytest
from app import _email_allowed, compute_approval_chain, _sanitize_result


# ── _email_allowed ────────────────────────────────────────────────

class TestEmailAllowed:

    def test_city_domain_is_allowed(self):
        assert _email_allowed("someone@palmspringsca.gov") is True

    def test_city_domain_any_prefix_allowed(self):
        assert _email_allowed("first.last@palmspringsca.gov") is True

    def test_explicit_allowlist_gmail(self):
        # rjames.callahan@gmail.com is in allowed_addresses
        assert _email_allowed("rjames.callahan@gmail.com") is True

    def test_unknown_domain_blocked(self):
        assert _email_allowed("someone@example.com") is False

    def test_similar_but_different_domain_blocked(self):
        # Must end with "@palmspringsca.gov", not just contain it
        assert _email_allowed("evil@fake-palmspringsca.gov") is False

    def test_empty_string_blocked(self):
        assert _email_allowed("") is False

    def test_subdomain_not_allowed(self):
        # "sub.palmspringsca.gov" does NOT end with "@palmspringsca.gov"
        assert _email_allowed("user@sub.palmspringsca.gov") is False

    def test_address_not_in_allowlist_and_wrong_domain_blocked(self):
        assert _email_allowed("unknown@gmail.com") is False


# ── compute_approval_chain ────────────────────────────────────────

class TestComputeApprovalChain:

    # --- Single-level chains (director signs) ---

    def test_small_amount_director_approves(self):
        chain = compute_approval_chain(5_000, "supplies")
        assert len(chain) == 1
        assert chain[0]["role"] == "director"
        assert chain[0]["approves"] is True

    def test_exactly_at_director_limit_still_director(self):
        chain = compute_approval_chain(25_000, "supplies")
        assert len(chain) == 1
        assert chain[0]["role"] == "director"

    # --- Two-level chains (director reviews, higher role signs) ---

    def test_above_director_limit_two_levels(self):
        chain = compute_approval_chain(30_000, "equipment")
        assert len(chain) == 2

    def test_acm_is_signer_at_30k(self):
        chain = compute_approval_chain(30_000, "equipment")
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "acm"

    def test_director_is_reviewer_not_signer(self):
        chain = compute_approval_chain(30_000, "equipment")
        reviewer = next(s for s in chain if not s["approves"])
        assert reviewer["role"] == "director"

    def test_city_manager_signs_at_100k(self):
        chain = compute_approval_chain(100_000, "supplies")
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "city_manager"

    def test_city_council_signs_at_200k_non_public(self):
        chain = compute_approval_chain(200_000, "supplies")
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "city_council"

    # --- Construction uses public thresholds ---

    def test_construction_uses_public_thresholds(self):
        # $55,000 construction (public) → acm (public_max=60k)
        # $55,000 supplies (non-public) → city_manager (non_public_max=50k)
        chain_pub    = compute_approval_chain(55_000, "construction")
        chain_nonpub = compute_approval_chain(55_000, "supplies")
        signer_pub    = next(s for s in chain_pub    if s["approves"])
        signer_nonpub = next(s for s in chain_nonpub if s["approves"])
        assert signer_pub["role"]    == "acm"
        assert signer_nonpub["role"] == "city_manager"

    def test_construction_city_manager_signs_at_200k(self):
        chain = compute_approval_chain(200_000, "construction")
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "city_manager"

    def test_construction_city_council_above_220k(self):
        chain = compute_approval_chain(250_000, "construction")
        signer = next(s for s in chain if s["approves"])
        assert signer["role"] == "city_council"

    # --- IT types use IT Director label ---

    def test_it_equipment_uses_it_director(self):
        chain = compute_approval_chain(10_000, "it_equipment")
        assert chain[0]["role"] == "it_director"
        assert chain[0]["label"] == "IT Director"

    def test_it_software_uses_it_director(self):
        chain = compute_approval_chain(10_000, "it_software")
        assert chain[0]["role"] == "it_director"

    def test_non_it_uses_department_director(self):
        for item_type in ("supplies", "equipment", "maintenance_services",
                          "professional_services", "construction", "other"):
            chain = compute_approval_chain(10_000, item_type)
            assert chain[0]["role"] in ("director", "it_director")
            if item_type not in ("it_equipment", "it_software"):
                assert chain[0]["role"] == "director", f"Failed for {item_type}"

    # --- Invariants that must always hold ---

    def test_exactly_one_approver_in_every_chain(self):
        test_cases = [
            (5_000,   "supplies"),
            (25_000,  "supplies"),
            (25_001,  "supplies"),
            (50_000,  "equipment"),
            (100_000, "equipment"),
            (220_000, "construction"),
            (300_000, "construction"),
            (10_000,  "it_equipment"),
        ]
        for amount, item_type in test_cases:
            chain = compute_approval_chain(amount, item_type)
            signers = [s for s in chain if s["approves"]]
            assert len(signers) == 1, (
                f"Expected exactly 1 approver for ${amount:,} {item_type}, "
                f"got {len(signers)}: {chain}"
            )

    def test_chain_is_not_empty_for_any_amount(self):
        for amount in (0, 1, 25_000, 75_000, 500_000):
            chain = compute_approval_chain(amount, "supplies")
            assert len(chain) >= 1


# ── _sanitize_result ──────────────────────────────────────────────

class TestSanitizeResult:

    # --- P-Card removal from method names ---

    def test_removes_pcard_slash_suffix(self):
        result = {"valid_methods": [{"method": "Single Quote / P-Card"}]}
        _sanitize_result(result)
        assert result["valid_methods"][0]["method"] == "Single Quote"

    def test_removes_pcard_slash_prefix(self):
        result = {"valid_methods": [{"method": "P-Card / Single Quote"}]}
        _sanitize_result(result)
        assert result["valid_methods"][0]["method"] == "Single Quote"

    def test_removes_pcard_only_entry_entirely(self):
        result = {"valid_methods": [{"method": "P-Card"}]}
        _sanitize_result(result)
        assert result["valid_methods"] == []

    def test_removes_pcard_case_insensitive(self):
        result = {"valid_methods": [{"method": "p-card"}]}
        _sanitize_result(result)
        assert result["valid_methods"] == []

    def test_leaves_non_pcard_methods_intact(self):
        result = {"valid_methods": [{"method": "Formal Competitive Bid (IFB/RFP)"}]}
        _sanitize_result(result)
        assert result["valid_methods"][0]["method"] == "Formal Competitive Bid (IFB/RFP)"

    def test_mixed_methods_only_pcard_removed(self):
        result = {
            "valid_methods": [
                {"method": "Single Quote / P-Card"},
                {"method": "Informal Process — 3 Quotes"},
                {"method": "P-Card"},
            ]
        }
        _sanitize_result(result)
        method_names = [m["method"] for m in result["valid_methods"]]
        assert "Single Quote" in method_names
        assert "Informal Process — 3 Quotes" in method_names
        assert all("p-card" not in m.lower() for m in method_names)
        assert len(result["valid_methods"]) == 2

    def test_empty_valid_methods_stays_empty(self):
        result = {"valid_methods": [], "summary": "", "flags": []}
        _sanitize_result(result)
        assert result["valid_methods"] == []

    # --- Categorization text scrubbing ---

    def test_removes_categorization_text_from_string_summary(self):
        result = {
            "valid_methods": [],
            "summary": "Under the current item categorization this applies. Proceed as normal.",
            "flags": [],
        }
        _sanitize_result(result)
        assert "Under the current" not in result["summary"]
        assert "Proceed as normal." in result["summary"]

    def test_removes_categorization_text_from_flags_list(self):
        result = {
            "valid_methods": [],
            "summary": "",
            "flags": [
                "Under the current categorization this is fine.",
                "Verify vendor is registered.",
            ],
        }
        _sanitize_result(result)
        assert not any("Under the current" in f for f in result["flags"])
        assert any("Verify vendor" in f for f in result["flags"])

    def test_summary_without_categorization_unchanged(self):
        original = "Three quotes are required for this purchase."
        result = {"valid_methods": [], "summary": original, "flags": []}
        _sanitize_result(result)
        assert result["summary"] == original

    def test_result_with_no_summary_or_flags_does_not_raise(self):
        result = {"valid_methods": [{"method": "Single Quote / P-Card"}]}
        _sanitize_result(result)  # should not raise
        assert result["valid_methods"][0]["method"] == "Single Quote"

    def test_modifies_in_place(self):
        result = {"valid_methods": [{"method": "P-Card"}], "summary": "", "flags": []}
        original_ref = result
        _sanitize_result(result)
        assert result is original_ref  # same object, mutated in place
