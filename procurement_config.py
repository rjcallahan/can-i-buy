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

_TENANT = os.getenv("TENANT", "palm-springs")
_VOLUME_PATH = "/data/procurement_config.json"
_REPO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tenants",
    _TENANT,
    "config.json"
)
# Always allowed for testing, on every city deployment — not city config
# because a city's own edits to mail.allowed_addresses shouldn't remove it.
_DEV_TEST_EMAILS = ["rjames.callahan@gmail.com", "kimbaker0206@gmail.com"]

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
        if not os.path.exists(self._path) and self._path == _VOLUME_PATH and os.path.exists(_REPO_PATH):
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
        Return the ordered tier list for an item type, each tier annotated
        with a "min" (the top of the previous tier + 0.01, or 0 for the
        first tier) so each tier represents a distinct, non-overlapping
        dollar range. Falls back to 'other' if item_type not found.
        """
        methods = self._get("procurement_methods")
        tiers = methods.get(item_type, methods.get("other", []))
        ranged = []
        low = 0.0
        for tier in tiers:
            ranged.append({**tier, "min": low})
            low = tier["max"] + 0.01
        return ranged

    def get_procurement_method(self, item_type: str,
                                amount: float) -> dict:
        """Return the single applicable procurement method rule for amount."""
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

    # ── Optional rule toggles ─────────────────────────────────

    def rule_enabled(self, name: str, default: bool = True) -> bool:
        """
        Return whether an optional processing rule (e.g. "faa") applies to
        this tenant. Rules not present in config default to enabled, so
        existing tenant configs keep their current behavior unchanged.
        """
        try:
            return bool(self._get("rules", name, "enabled"))
        except KeyError:
            return default

    def ai_model(self, default: str = "claude-sonnet-4-20250514") -> str:
        try:
            return self._get("ai", "model") or default
        except KeyError:
            return default

    def allowed_email_domain(self) -> str:
        try:
            return self._get("mail", "allowed_domain") or ""
        except KeyError:
            return ""

    def allowed_email_addresses(self) -> list[str]:
        try:
            configured = self._get("mail", "allowed_addresses") or []
        except KeyError:
            configured = []
        return list(dict.fromkeys(configured + _DEV_TEST_EMAILS))

    def admin_emails(self) -> list[str]:
        try:
            return list(self._get("admin", "emails") or [])
        except KeyError:
            return []

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
    print("Approval role tests:")
    for amt, pub in [(10000, False), (30000, False), (60000, False),
                     (100000, False), (200000, False),
                     (50000, True), (100000, True), (250000, True)]:
        role = cfg.approval_role(amt, pub)
        label = "public" if pub else "non-public"
        print(f"  ${amt:>10,.0f} {label:<12} → {role}")
