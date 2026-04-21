# procurement_config.py
"""
Procurement Gateway — configuration loader.

Reads procurement_config.json from the project root and exposes
typed accessors used throughout the application.

Usage
─────
    from procurement_config import cfg

    threshold = cfg.bid_threshold("supplies")        # 75000
    methods   = cfg.procurement_methods("equipment") # list of tier dicts
    sla       = cfg.sla_days("attorney_review")      # 10
    levels    = cfg.signing_authority_levels()        # list of level dicts
    role      = cfg.approval_role(80000, False)       # "city_manager"

All modules that previously defined their own BID_THRESHOLDS,
SIGNING_AUTHORITY, MAINTENANCE_KEYWORDS etc. should import from
here instead.

The config file is loaded once at import time. To reload without
restarting Flask, call cfg.reload().
"""

from __future__ import annotations

import json
import os
from typing import Any

_VOLUME_PATH = "/data/procurement_config.json"
_REPO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "procurement_config.json"
)
_CONFIG_PATH = (
    os.getenv("CONFIG_PATH")
    or (_VOLUME_PATH if os.path.exists(_VOLUME_PATH) else _REPO_PATH)
)


class _Config:
    """
    Typed wrapper around the JSON config file.
    All public methods raise KeyError or ValueError with clear messages
    rather than silently returning None.
    """

    def __init__(self, path: str = _CONFIG_PATH):
        self._path = path
        self._data: dict = {}
        self._load()

    def _load(self):
        # If volume path doesn't exist but repo copy does, bootstrap the volume
        if not os.path.exists(self._path) and self._path == _VOLUME_PATH:
            if os.path.exists(_REPO_PATH):
                import shutil
                os.makedirs(os.path.dirname(_VOLUME_PATH), exist_ok=True)
                shutil.copy(_REPO_PATH, _VOLUME_PATH)
                print(f"Bootstrapped config from repo to {_VOLUME_PATH}", flush=True)

        if not os.path.exists(self._path):
            raise FileNotFoundError(
                f"procurement_config.json not found. "
                f"Checked volume path ({_VOLUME_PATH}) and repo path ({_REPO_PATH}). "
                f"Ensure the file exists in at least one of these locations."
            )
        with open(self._path, encoding="utf-8") as f:
            self._data = json.load(f)

    def reload(self):
        """Reload config from disk — useful during development."""
        self._load()

    def _get(self, *keys: str) -> Any:
        """Navigate nested keys, skipping _comment / _note keys."""
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                raise KeyError(f"Expected dict at '{k}', got {type(node)}")
            node = node[k]
        return node

    # ── Bid thresholds ────────────────────────────────────────

    def bid_threshold(self, item_type: str) -> float:
        """
        Return the competitive bid threshold for an item type.
        Amounts AT OR ABOVE this value require competitive bidding.
        Falls back to 'other' if item_type not found.
        """
        thresholds = self._get("bid_thresholds")
        return float(thresholds.get(item_type,
                                    thresholds.get("other", 75000)))

    def requires_competitive_bid(self, item_type: str,
                                  amount: float) -> bool:
        """Return True if the amount meets or exceeds the bid threshold."""
        return amount >= self.bid_threshold(item_type)

    def all_bid_thresholds(self) -> dict:
        """Return the full bid_thresholds dict."""
        return {k: v for k, v in self._get("bid_thresholds").items()
                if not k.startswith("_")}

    # ── Procurement methods ───────────────────────────────────

    def procurement_methods(self, item_type: str) -> list[dict]:
        """
        Return the ordered tier list for an item type.
        Falls back to 'other' if item_type not found.
        """
        methods = self._get("procurement_methods")
        return methods.get(item_type, methods.get("other", []))

    def get_procurement_method(self, item_type: str,
                                amount: float) -> dict:
        """Return the applicable procurement method rule for amount."""
        tiers = self.procurement_methods(item_type)
        for tier in tiers:
            if amount <= tier["max"]:
                return tier
        return tiers[-1] if tiers else {}

    def all_procurement_methods(self) -> dict:
        """Return the full procurement_methods dict."""
        return {k: v for k, v in self._get("procurement_methods").items()
                if not k.startswith("_")}

    # ── Signing authority ─────────────────────────────────────

    def signing_authority_levels(self) -> list[dict]:
        """
        Return the signing authority levels ordered from lowest to highest.
        Each level: {role, label, non_public_max, public_max}
        """
        return self._get("signing_authority", "levels")

    def approval_role(self, amount: float,
                      is_public_project: bool) -> str:
        """
        Return the lowest role with authority to approve this amount.
        Returns one of: director | acm | city_manager | city_council
        """
        key = "public_max" if is_public_project else "non_public_max"
        for level in self.signing_authority_levels():
            if amount <= level[key]:
                return level["role"]
        # Should never reach here given city_council max = 999999999
        return "city_council"

    # ── Attorney review ───────────────────────────────────────

    def attorney_review_required(self, is_pcard: bool = False,
                                  app_generated: bool = False,
                                  amount: float = 0) -> bool:
        """
        Return True if attorney review is required.
        Phase 2: app_generated contracts under the threshold may be exempt.
        """
        if is_pcard:
            return False

        ar = self._get("attorney_review")
        if ar.get("always_required", True):
            # Check phase 2 bypass
            threshold = ar.get("phase2_app_generated_threshold")
            if app_generated and threshold is not None:
                return amount >= float(threshold)
            return True
        return False

    # ── P-Card ────────────────────────────────────────────────

    def pcard_prohibited_types(self) -> list[str]:
        """Return list of item types where P-Card is prohibited."""
        return self._get("pcard", "prohibited_item_types")

    def pcard_eligible(self, item_type: str) -> bool:
        """Return True if P-Card is allowed for this item type."""
        return item_type not in self.pcard_prohibited_types()

    def pcard_transaction_limit(self) -> float:
        """Return the default per-transaction P-Card limit."""
        return float(self._get("pcard", "single_transaction_limit"))

    # ── Federal funds ─────────────────────────────────────────

    def federal_micro_purchase_limit(self,
                                      is_construction: bool) -> float:
        """Return the federal micro-purchase threshold."""
        key = ("micro_purchase_construction" if is_construction
               else "micro_purchase_non_construction")
        return float(self._get("federal_funds", key))

    # ── SLA days ──────────────────────────────────────────────

    def sla_days(self, stage_code: str) -> int:
        """
        Return SLA days for a stage. Returns 5 if stage not found
        (safe default matching the DB column default).
        """
        sla = self._get("sla_days")
        return int(sla.get(stage_code, 5))

    def all_sla_days(self) -> dict:
        """Return the full sla_days dict."""
        return {k: v for k, v in self._get("sla_days").items()
                if not k.startswith("_")}

    # ── RFQ document thresholds ───────────────────────────────

    def iqr_max_amount(self) -> float:
        """Maximum amount for an IQR document."""
        return float(self._get("rfq_document_thresholds", "iqr_max"))

    def rfp_min_amount(self) -> float:
        """Minimum amount requiring an RFP document."""
        return float(self._get("rfq_document_thresholds", "rfp_min"))

    def suggested_rfq_template(self, item_type: str,
                                amount: float) -> str:
        """
        Suggest the appropriate RFQ template code based on amount.
        Returns IQR_SERVICES, IQR_SUPPLIES, or RFP_GENERAL.
        Caller should check for aviation context separately.
        """
        if amount >= self.rfp_min_amount():
            return "RFP_GENERAL"
        supply_types = {"supplies", "equipment", "it_equipment", "it_software"}
        return "IQR_SUPPLIES" if item_type in supply_types else "IQR_SERVICES"

    # ── Keywords ──────────────────────────────────────────────

    def hr_review_keywords(self) -> list[str]:
        """Return keywords that trigger HR/union review."""
        return self._get("hr_review_keywords")

    def maintenance_redirect_keywords(self) -> list[str]:
        """Return keywords that redirect to city maintenance."""
        return self._get("maintenance_redirect_keywords")

    # ── City defaults ─────────────────────────────────────────

    def city(self, key: str, default: str = "") -> str:
        """Return a city-specific default value."""
        return str(self._get("city").get(key, default))

    def city_name(self) -> str:
        return self.city("name", "City of Palm Springs")

    def city_clerk_name(self) -> str:
        return self.city("city_clerk_name", "")

    def city_state_zip(self) -> str:
        return self.city("city_state_zip", "Palm Springs, CA 92262")

    # ── Document types ───────────────────────────────────────

    def document_types(self) -> list[dict]:
        """Return all document types. Each: {code, label, multiple}"""
        return self._get("document_types", "types")

    def document_type_codes(self) -> list[str]:
        """Return just the codes list."""
        return [t["code"] for t in self.document_types()]

    def document_type_label(self, code: str) -> str:
        """Return display label for a document type code."""
        for t in self.document_types():
            if t["code"] == code:
                return t["label"]
        return code.replace("_", " ").title()

    def stage_gate_types(self, stage_code: str) -> list[str]:
        """
        Return required document type codes for a stage gate.
        Empty list means no gate — any attachment (or none) is fine.
        """
        gates = self._get("stage_gates")
        return [v for k, v in gates.items()
                if k == stage_code and not k.startswith("_")]

    def stage_requires_document_type(self, stage_code: str,
                                      attachments: list[dict]) -> tuple[bool, str]:
        """
        Check if a stage gate is satisfied by the current attachments.
        Returns (passes, error_message).
        passes=True means gate is satisfied or no gate exists.
        """
        gates = self._get("stage_gates")
        required_types = gates.get(stage_code, [])

        # No gate for this stage
        if not required_types:
            return True, ""

        # Check if any attachment matches a required type
        active = [a for a in attachments if a.get("status") == "active"]
        active_types = {a.get("document_type", "other") for a in active}

        for req_type in required_types:
            if req_type in active_types:
                return True, ""

        # Gate not satisfied — build helpful error message
        type_labels = [self.document_type_label(t) for t in required_types]
        if len(type_labels) == 1:
            needed = f"a {type_labels[0]}"
        else:
            needed = f"a {' or '.join(type_labels)}"

        stage_label = stage_code.replace("_", " ").title()
        return False, (
            f"{needed} must be attached before advancing from "
            f"{stage_label}. Please upload the document and try again."
        )

    # ── Raw access ────────────────────────────────────────────

    def raw(self, *keys: str) -> Any:
        """
        Direct access to any config value by key path.
        Use sparingly — prefer typed accessors above.
        Example: cfg.raw("city", "procurement_email")
        """
        return self._get(*keys)

    def __repr__(self) -> str:
        return f"<ProcurementConfig path={self._path}>"


# ── Module-level singleton ────────────────────────────────────
# Import this everywhere: from procurement_config import cfg

try:
    cfg = _Config()
except FileNotFoundError as e:
    import sys
    print(f"WARNING: {e}", file=sys.stderr)
    print("App starting with empty config — set CONFIG_PATH or place procurement_config.json in /data/", file=sys.stderr)
    cfg = _Config.__new__(_Config)
    cfg._path = _CONFIG_PATH
    cfg._data = {}


# ── Convenience re-exports ────────────────────────────────────
# These let existing code like:
#   from procurement_config import BID_THRESHOLDS
# work without changes during the migration period.

BID_THRESHOLDS       = cfg.all_bid_thresholds()       if cfg._data else {}
SIGNING_AUTHORITY    = cfg.signing_authority_levels()  if cfg._data else []
HR_REVIEW_KEYWORDS   = cfg.hr_review_keywords()        if cfg._data else []
MAINTENANCE_KEYWORDS = cfg.maintenance_redirect_keywords() if cfg._data else []
PCARD_PROHIBITED     = cfg.pcard_prohibited_types()    if cfg._data else []


if __name__ == "__main__":
    print(f"Config loaded from: {cfg._path}")
    print()
    print("Bid thresholds:")
    for k, v in cfg.all_bid_thresholds().items():
        print(f"  {k:<25} ${v:>12,.0f}")
    print()
    print("Signing authority:")
    for level in cfg.signing_authority_levels():
        print(f"  {level['role']:<15} "
              f"non-public ≤ ${level['non_public_max']:>12,.0f}  "
              f"public ≤ ${level['public_max']:>12,.0f}")
    print()
    print("SLA days:")
    for stage, days in cfg.all_sla_days().items():
        print(f"  {stage:<25} {days} days")
    print()
    print("Attorney review always required:",
          cfg.attorney_review_required())
    print("Attorney review (P-Card):",
          cfg.attorney_review_required(is_pcard=True))
    print()
    print("Approval role tests:")
    for amt, pub in [(10000, False), (30000, False), (60000, False),
                     (100000, False), (200000, False),
                     (50000, True), (100000, True), (250000, True)]:
        role = cfg.approval_role(amt, pub)
        label = "public" if pub else "non-public"
        print(f"  ${amt:>10,.0f} {label:<12} → {role}")
