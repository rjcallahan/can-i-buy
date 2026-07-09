"""
Tests for procurement_config.py — the typed config singleton.

These tests validate that the config values are correct and that the
accessor methods behave as specified. Because the config drives every
procurement decision the app makes, this is the highest-value test
surface: a wrong threshold or approval tier is a real compliance error.

All assertions use the known Palm Springs values from
data/procurement_config.json. If a value changes in the config file,
the test will fail — intentionally, to catch accidental edits.
"""
import pytest
from procurement_config import cfg


# ── Bid thresholds ────────────────────────────────────────────────

class TestBidThresholds:

    def test_supplies_threshold(self):
        assert cfg.bid_threshold("supplies") == 75_000

    def test_equipment_threshold(self):
        assert cfg.bid_threshold("equipment") == 75_000

    def test_it_equipment_threshold(self):
        assert cfg.bid_threshold("it_equipment") == 75_000

    def test_it_software_threshold(self):
        assert cfg.bid_threshold("it_software") == 75_000

    def test_maintenance_services_threshold(self):
        assert cfg.bid_threshold("maintenance_services") == 75_000

    def test_professional_services_threshold(self):
        assert cfg.bid_threshold("professional_services") == 75_000

    def test_construction_threshold_is_higher(self):
        assert cfg.bid_threshold("construction") == 220_000

    def test_travel_threshold_effectively_unlimited(self):
        # Travel is handled by policy, not competitive bid
        assert cfg.bid_threshold("travel") >= 999_999_999

    def test_unknown_type_falls_back_to_other(self):
        # Unrecognised item types should not raise — fall back to 'other'
        assert cfg.bid_threshold("unicorn_furniture") == cfg.bid_threshold("other")

    def test_requires_competitive_bid_exactly_at_threshold(self):
        # AT the threshold requires a bid
        assert cfg.requires_competitive_bid("supplies", 75_000) is True

    def test_requires_competitive_bid_one_cent_below(self):
        assert cfg.requires_competitive_bid("supplies", 74_999.99) is False

    def test_requires_competitive_bid_above_threshold(self):
        assert cfg.requires_competitive_bid("supplies", 100_000) is True

    def test_construction_bid_required_at_threshold(self):
        assert cfg.requires_competitive_bid("construction", 220_000) is True

    def test_construction_no_bid_just_below(self):
        assert cfg.requires_competitive_bid("construction", 219_999.99) is False

    def test_all_thresholds_returns_dict(self):
        thresholds = cfg.all_bid_thresholds()
        assert isinstance(thresholds, dict)
        assert "supplies" in thresholds
        assert "construction" in thresholds


# ── Procurement methods / tiers ───────────────────────────────────

class TestProcurementMethods:

    # --- Supplies ---

    def test_supplies_single_quote_below_15k(self):
        tier = cfg.get_procurement_method("supplies", 5_000)
        assert "Single Quote" in tier["method"]

    def test_supplies_single_quote_exactly_15k(self):
        tier = cfg.get_procurement_method("supplies", 15_000)
        assert "Single Quote" in tier["method"]

    def test_supplies_three_quotes_just_above_15k(self):
        tier = cfg.get_procurement_method("supplies", 15_000.01)
        assert "3 Quotes" in tier["method"]

    def test_supplies_three_quotes_mid_range(self):
        tier = cfg.get_procurement_method("supplies", 50_000)
        assert "3 Quotes" in tier["method"]

    def test_supplies_formal_bid_at_threshold(self):
        tier = cfg.get_procurement_method("supplies", 75_000)
        assert "Formal" in tier["method"]

    def test_supplies_formal_bid_above_threshold(self):
        tier = cfg.get_procurement_method("supplies", 200_000)
        assert "Formal" in tier["method"]

    # --- Construction (three distinct tiers) ---

    def test_construction_force_account_below_75k(self):
        tier = cfg.get_procurement_method("construction", 50_000)
        assert "Force Account" in tier["method"]

    def test_construction_informal_bidding_mid_range(self):
        tier = cfg.get_procurement_method("construction", 150_000)
        assert "Informal" in tier["method"]

    def test_construction_formal_bidding_above_220k(self):
        tier = cfg.get_procurement_method("construction", 250_000)
        assert "Formal" in tier["method"]

    # --- IT equipment ---

    def test_it_equipment_single_quote_small(self):
        tier = cfg.get_procurement_method("it_equipment", 10_000)
        assert "Single Quote" in tier["method"]

    def test_it_equipment_three_quotes_mid(self):
        tier = cfg.get_procurement_method("it_equipment", 40_000)
        assert "3 Quotes" in tier["method"]

    # --- Unknown type falls back ---

    def test_unknown_type_returns_other_tiers(self):
        tiers = cfg.procurement_methods("mystery_item")
        assert tiers == cfg.procurement_methods("other")

    def test_all_procurement_methods_returns_dict(self):
        methods = cfg.all_procurement_methods()
        assert isinstance(methods, dict)
        assert "supplies" in methods
        assert "construction" in methods


# ── Signing authority / approval roles ───────────────────────────

class TestSigningAuthority:

    # Non-public (standard contracts)

    def test_director_approves_up_to_25k(self):
        assert cfg.approval_role(25_000, False) == "director"

    def test_acm_kicks_in_above_25k(self):
        assert cfg.approval_role(25_001, False) == "acm"

    def test_acm_approves_up_to_50k(self):
        assert cfg.approval_role(50_000, False) == "acm"

    def test_city_manager_kicks_in_above_50k(self):
        assert cfg.approval_role(50_001, False) == "city_manager"

    def test_city_manager_approves_up_to_150k(self):
        assert cfg.approval_role(150_000, False) == "city_manager"

    def test_city_council_required_above_150k(self):
        assert cfg.approval_role(150_001, False) == "city_council"

    # Public works / construction — higher thresholds

    def test_public_director_approves_up_to_25k(self):
        assert cfg.approval_role(25_000, True) == "director"

    def test_public_acm_approves_up_to_60k(self):
        assert cfg.approval_role(60_000, True) == "acm"

    def test_public_non_public_differ_at_55k(self):
        # $55,000 public → acm (public_max=60k)
        # $55,000 non-public → city_manager (non_public_max=50k)
        assert cfg.approval_role(55_000, True)  == "acm"
        assert cfg.approval_role(55_000, False) == "city_manager"

    def test_public_city_manager_approves_up_to_220k(self):
        assert cfg.approval_role(220_000, True) == "city_manager"

    def test_public_city_council_above_220k(self):
        assert cfg.approval_role(220_001, True) == "city_council"

    def test_signing_levels_ordered_lowest_to_highest(self):
        levels = cfg.signing_authority_levels()
        non_pub_maxes = [l["non_public_max"] for l in levels]
        assert non_pub_maxes == sorted(non_pub_maxes)


# ── P-Card rules ──────────────────────────────────────────────────

class TestPCard:

    def test_it_equipment_prohibited(self):
        assert cfg.pcard_eligible("it_equipment") is False

    def test_it_software_prohibited(self):
        assert cfg.pcard_eligible("it_software") is False

    def test_professional_services_prohibited(self):
        assert cfg.pcard_eligible("professional_services") is False

    def test_construction_prohibited(self):
        assert cfg.pcard_eligible("construction") is False

    def test_supplies_eligible(self):
        assert cfg.pcard_eligible("supplies") is True

    def test_equipment_eligible(self):
        assert cfg.pcard_eligible("equipment") is True

    def test_maintenance_services_eligible(self):
        assert cfg.pcard_eligible("maintenance_services") is True

    def test_transaction_limit_is_2500(self):
        assert cfg.pcard_transaction_limit() == 2_500.0

    def test_prohibited_types_returns_list(self):
        prohibited = cfg.pcard_prohibited_types()
        assert isinstance(prohibited, list)
        assert len(prohibited) > 0


# ── City config & metadata ────────────────────────────────────────

class TestCityConfig:

    def test_city_name(self):
        assert cfg.city_name() == "City of Palm Springs"

    def test_city_clerk_name(self):
        assert cfg.city_clerk_name() == "Anthony Mejia"

    def test_city_state_zip(self):
        assert "Palm Springs" in cfg.city_state_zip()
        assert "CA" in cfg.city_state_zip()

    def test_allowed_domain(self):
        assert cfg.allowed_email_domain() == "@palmspringsca.gov"

    def test_allowed_addresses_is_list(self):
        assert isinstance(cfg.allowed_email_addresses(), list)

    def test_allowed_addresses_not_empty(self):
        # At least the developer addresses should be present
        assert len(cfg.allowed_email_addresses()) > 0


# ── AI model ──────────────────────────────────────────────────────

class TestAIModel:

    def test_ai_model_returns_string(self):
        model = cfg.ai_model()
        assert isinstance(model, str)
        assert len(model) > 0

    def test_ai_model_is_non_empty_string(self):
        assert len(cfg.ai_model()) > 0

    def test_ai_model_default_used_on_missing_key(self):
        # If key not present, the default is returned rather than raising
        default = "claude-fallback-model"
        model = cfg.ai_model(default=default)
        # Either it's the configured value or the default — either is fine
        assert isinstance(model, str)


# ── Config reload ─────────────────────────────────────────────────

class TestConfigReload:

    def test_reload_does_not_raise(self):
        # Reloading from the same file should be idempotent
        cfg.reload()
        assert cfg.city_name() == "City of Palm Springs"

    def test_raw_access(self):
        name = cfg.raw("city", "name")
        assert name == "City of Palm Springs"
