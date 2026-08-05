#!/usr/bin/env python3
"""
Split a procurement rules JSON (e.g. tenants/<city>/config.json) into atomic
rule chunks for the embedding comparison eval.

One chunk per IF/THEN rule or clause: a procurement-method tier, a bid
threshold, a signing-authority level, a P-Card rule, a processing-time class.
Each chunk gets a natural-language sentence so it embeds like a real policy
passage, not a raw JSON fragment.

Usage:
    python extract_chunks.py
    python extract_chunks.py --input ../tenants/palm-springs/config.json --output rules_chunks.json
"""

import argparse
import json
import os

_DEFAULT_INPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tenants", "palm-springs", "config.json"
)
_DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_chunks.json")


def _money(n: float) -> str:
    return f"${n:,.0f}"


def _label(item_type: str) -> str:
    return item_type.replace("_", " ")


def extract_procurement_methods(cfg: dict) -> list[dict]:
    chunks = []
    methods = cfg.get("procurement_methods", {})
    for item_type, tiers in methods.items():
        if item_type.startswith("_"):
            continue
        low = 0.0
        for i, tier in enumerate(tiers):
            high = tier["max"]
            range_text = (
                f"above {_money(low)}" if high >= 999999999
                else f"from {_money(low)} to {_money(high)}"
            )
            text = (
                f"For {_label(item_type)}, purchases {range_text} require "
                f"{tier['method'].strip()}. {tier['process']}"
            )
            chunks.append({
                "id": f"procurement_methods.{item_type}.{i}",
                "text": text,
                "source_field": f"procurement_methods.{item_type}[{i}]",
            })
            low = high + 0.01
    return chunks


def extract_bid_thresholds(cfg: dict) -> list[dict]:
    chunks = []
    for item_type, amount in cfg.get("bid_thresholds", {}).items():
        if item_type.startswith("_"):
            continue
        text = (
            f"For {_label(item_type)}, purchases at or above {_money(amount)} "
            f"require competitive bidding."
        )
        chunks.append({
            "id": f"bid_thresholds.{item_type}",
            "text": text,
            "source_field": f"bid_thresholds.{item_type}",
        })
    return chunks


def extract_signing_authority(cfg: dict) -> list[dict]:
    chunks = []
    levels = cfg.get("signing_authority", {}).get("levels", [])
    for i, level in enumerate(levels):
        text = (
            f"{level['label']} ({level['role']}) may approve non-public "
            f"contracts up to {_money(level['non_public_max'])} and public "
            f"works contracts up to {_money(level['public_max'])}."
        )
        chunks.append({
            "id": f"signing_authority.{level['role']}",
            "text": text,
            "source_field": f"signing_authority.levels[{i}]",
        })
    return chunks


def extract_pcard(cfg: dict) -> list[dict]:
    chunks = []
    pcard = cfg.get("pcard", {})

    prohibited = pcard.get("prohibited_item_types", [])
    if prohibited:
        text = "P-Card may not be used for: " + ", ".join(_label(t) for t in prohibited) + "."
        chunks.append({
            "id": "pcard.prohibited_item_types",
            "text": text,
            "source_field": "pcard.prohibited_item_types",
        })

    if "single_transaction_limit" in pcard:
        text = (
            f"The default P-Card single-transaction limit is "
            f"{_money(pcard['single_transaction_limit'])}. Individual card "
            f"limits may differ."
        )
        chunks.append({
            "id": "pcard.single_transaction_limit",
            "text": text,
            "source_field": "pcard.single_transaction_limit",
        })

    if "purchase_cap" in pcard:
        text = (
            f"P-Card is never permitted for total purchase amounts above "
            f"{_money(pcard['purchase_cap'])}. Purchases over "
            f"{_money(pcard['purchase_cap'])} require a formal agreement or "
            f"contract regardless of item type."
        )
        chunks.append({
            "id": "pcard.purchase_cap",
            "text": text,
            "source_field": "pcard.purchase_cap",
        })

    return chunks


def extract_processing_time(cfg: dict) -> list[dict]:
    chunks = []
    processing = cfg.get("processing_time", {})
    for category, classes in processing.items():
        if category.startswith("_"):
            continue
        for cls, info in classes.items():
            n_lo, n_hi = info["normal_days"]
            e_lo, e_hi = info["expedited_days"]
            text = (
                f"{info['label']}: normal processing takes {n_lo}-{n_hi} "
                f"business days; expedited processing takes {e_lo}-{e_hi} days."
            )
            if info.get("caveat"):
                text += f" {info['caveat']}"
            chunks.append({
                "id": f"processing_time.{category}.{cls}",
                "text": text,
                "source_field": f"processing_time.{category}.{cls}",
            })
    return chunks


def extract_all(cfg: dict) -> list[dict]:
    return (
        extract_procurement_methods(cfg)
        + extract_bid_thresholds(cfg)
        + extract_signing_authority(cfg)
        + extract_pcard(cfg)
        + extract_processing_time(cfg)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=_DEFAULT_INPUT, help="Path to procurement rules JSON")
    parser.add_argument("--output", default=_DEFAULT_OUTPUT, help="Path to write rules_chunks.json")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        cfg = json.load(f)

    chunks = extract_all(cfg)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)

    print(f"Read: {args.input}")
    print(f"Wrote {len(chunks)} chunks to {args.output}")


if __name__ == "__main__":
    main()
