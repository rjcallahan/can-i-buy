# intake.py
from procurement_config import cfg

# ── Config-driven constants ───────────────────────────────────
# These are loaded from procurement_config.json.
# Do not hardcode thresholds here — edit the JSON file instead.

PCARD_PROHIBITED = cfg.pcard_prohibited_types()


def get_threshold(item_type: str, amount: float) -> dict:
    """Return the applicable procurement threshold rule."""
    return cfg.get_procurement_method(item_type, amount)


def requires_competitive_bid(item_type: str, amount: float) -> bool:
    """Return True if this request requires competitive bidding."""
    return cfg.requires_competitive_bid(item_type, amount)


def build_prompt(data: dict) -> str:
    """Build the policy analysis prompt from form data."""
    import json

    item_type = data.get("item_type", "other")
    amount    = float(data.get("amount", 0))

    threshold_rule  = get_threshold(item_type, amount)
    is_bid          = requires_competitive_bid(item_type, amount)

    threshold_rules = json.dumps(
        cfg.procurement_methods(item_type),
        indent=2, default=str
    )

    sole_source_block = ""
    if data.get("sole_source") == "yes":
        sole_source_block = (
            f"- Proposed Vendor: {data.get('vendor_name', '')}\n"
            f"- Sole Source Justification: "
            f"{data.get('sole_source_justification', '')}"
        )


    # Build attachment list — one per line
    attachment_lines = "\n".join(
        f"    * {f}" for f in data.get("attachments_list", [])
    ) or "    (none)"

    # Bid path note for the AI
    bid_note = (
        f"NOTE: This request is AT OR ABOVE the ${cfg.bid_threshold(item_type):,.0f} "
        f"competitive bid threshold for {item_type}. "
        f"Procurement will manage the formal bid process — "
        f"do NOT flag this as an error or missing item. "
        f"APPROVE the request and note the bid requirement in next_steps only."
        if is_bid else
        f"NOTE: This request is BELOW the competitive bid threshold. "
        f"The required method is: {threshold_rule['method']}."
    )

    is_demo = data.get("demo", False)
    summary_instruction = (
        "2-3 sentences. If the purchase is P-Card eligible, assume it WILL be paid by P-Card — "
        "state that it will be purchased via P-Card and no vendor quote is needed. "
        "If the purchase is NOT P-Card eligible, state what procurement method applies. "
        "If vendor quotes are required, say 'You will need [number] vendor quote(s) when you are "
        "ready to submit this request to Procurement.' "
        "Do NOT mention approvals, approval chains, or approving officials — those happen after "
        "submission and are handled separately. Do NOT list approvals as documents needed. "
        "Do NOT use the word 'missing'. Do NOT say the request is incomplete."
        if is_demo else
        "2-3 sentence plain English summary of the decision"
    )

    return f"""You are a procurement compliance officer for the City of Palm Springs, California.
Analyze the following procurement request and return a JSON response only — no markdown, no explanation outside the JSON.

PROCUREMENT REQUEST:
- Requester: {data.get('requester_name')}, {data.get('department')}
- Requester Email: {data.get('requester_email')} (for notification only — do not evaluate)
- Request Name: {data.get('draft_name') or data.get('item_name')}
- Item/Service: {data.get('item_name')}
- Type: {item_type}
- Estimated Amount: ${amount:,.2f}
- Account Code: {data.get('account_code') or 'To be assigned'}
- Description/Justification: {data.get('description')}
- Sole Source: {data.get('sole_source')}
{sole_source_block}
- FAA-Governed Purchase: {data.get('faa_governed', 'no')}
- Involves Federal Funds: {data.get('federal_funds')}
- P-Card Requested: {data.get('pcard')}
- Attachments Provided: {data.get('attachments_count', 0)} file(s)
- Attached File Names:
{attachment_lines}

APPLICABLE PROCUREMENT THRESHOLDS FOR THIS ITEM TYPE:
{threshold_rules}

{bid_note}

CITY POLICIES TO APPLY:

1. THRESHOLD AND BID PATH:
   The thresholds above define the procurement method for this item type.
   - BELOW threshold → direct contract path. Approve if otherwise compliant.
   - AT OR ABOVE threshold → competitive bid path. Procurement manages the
     bid process. Do NOT flag bid path requests as non-compliant simply
     because of their dollar amount. Approve them and note bid requirement.
   - $74,000 equipment is BELOW the $75,000 threshold — this is a direct
     contract, NOT a bid. Never flag amounts below the threshold as requiring
     competitive bidding.

2. P-CARD:
   P-Card is a PAYMENT METHOD, not a procurement method. Never include
   "P-Card" in the procurement_method field — that field describes HOW
   the purchase is sourced (Single Quote, 3 Quotes, Competitive Bid),
   not how it is paid.
   - If pcard=no: set pcard_eligible=false, pcard_note="P-Card not requested"
   - If pcard=yes AND item type is prohibited: set pcard_eligible=false
   - If pcard=yes AND item type is allowed: set pcard_eligible=true
   P-Card is PROHIBITED for: IT equipment, IT software, professional
   services, construction, rentals, leases, and all services.
   The procurement_method field must NEVER contain the word "P-Card".

3. SOLE SOURCE:
   If sole_source=yes, simply note that a sole source justification document
   will be required at submission. Do NOT change the verdict, procurement
   method, or flag it as a policy concern. Do NOT require it to be provided
   now — it is a document to include when submitting the formal request.

4. FEDERAL FUNDS:
   Trigger additional requirements: Buy American, 2 CFR §200.320
   micro-purchase limits ($2,000 construction / $3,000 non-construction),
   mandatory competition requirements, and federal conflict of interest
   standards.

6. PROFESSIONAL SERVICES:
   Require an insurance certificate on file before work begins.

7. CONFLICT OF INTEREST:
   No employee may procure from family members, close friends, or relatives.

8. PURCHASE SPLITTING:
   Splitting purchases to circumvent spending limits is strictly prohibited.

9. IT APPROVAL:
   IT equipment and software require IT Director approval regardless of amount.

10. DOCUMENT MATCHING — CRITICAL:
    Check EACH attached file independently using fuzzy matching.
    Process every file before making document status decisions.
    - quote, quotation, bid, proposal, estimate → vendor quote PROVIDED
    - insurance, cert, coi, acord, liability    → insurance cert PROVIDED
    - sole, source, letter                      → sole source letter PROVIDED
    - contract, agreement, scope, sow           → contract PROVIDED
    - w9, w-9, vendor-form                      → vendor form PROVIDED
    Mark as provided generously. Only mark required if NO file could
    reasonably match. Do not require documents that are not applicable
    to this type of purchase.

11. REQUIRED DOCUMENTS BY TYPE:
    - supplies/equipment under $15k: vendor quote (if not sole source)
    - supplies/equipment $15k-$75k: three vendor quotes
    - it_equipment/software: IT Director approval form (note as next step, not blocking)
    - professional_services: insurance certificate
    - construction: engineering plans (note as next step for large projects)
    - travel: travel authorization form
    - bid path (any type at/above threshold): do NOT require quotes —
      Procurement will solicit bids formally. Only flag if sole source
      docs are missing on a sole source request.

12. EMAIL:
    Do not flag or comment on the requester's email domain. Email is for
    notification only.

13. ACCOUNT CODE:
    Account code is optional at intake. Never flag a missing account code.

14. FAA-GOVERNED PURCHASES:
    If faa_governed=yes, this purchase is subject to FAA regulations (AIP-funded
    or FAA-regulated airport purchase). Do NOT change the verdict or procurement
    method — FAA compliance is informational only. Populate the faa_notes field
    with a plain-English compliance summary covering whichever of the following
    apply to this purchase type and amount:
    - Buy American: Materials and equipment must be U.S.-produced (49 U.S.C. § 50101).
      An FAA waiver is required for any foreign-sourced items.
    - DBE: Disadvantaged Business Enterprise participation goals apply (49 CFR Part 26).
      Requester should contact Procurement for the current DBE goal percentage.
    - Lower thresholds: FAA micro-purchase threshold is $10,000 (vs city standard).
      Simplified acquisition threshold is $250,000.
    - Required contract clauses: FAA mandatory provisions must be included in all
      contracts (AC 150/5370-10).
    - Independent estimate: Required for professional services contracts.
    - FAA pre-approval: May be required for capital expenditures — confirm with
      Airport Administration.
    If faa_governed=no, set faa_notes to empty string.

{"DEMO MODE RULES — override normal verdict logic:" if is_demo else ""}
{"""- This is a policy preview only. The requester has not submitted any documents yet.
- NEVER return RETURNED due to missing vendor quotes, missing IT Director approval, or any other missing document. Documents are not expected at this stage.
- Only return RETURNED if there is a genuine policy violation (e.g. sole source flagged but no justification provided).
- Return APPROVED for all standard, compliant purchase requests regardless of attachment count.""" if is_demo else ""}

Evaluate this request and return ONLY this JSON structure:
{{
  "verdict": "APPROVED" | "RETURNED" | "FLAGGED",
  "procurement_method": "string — the primary required procurement method",
  "valid_methods": [
    {{"method": "method name", "description": "1 sentence explaining when/why this path applies", "documents_needed": ["list of required documents for this path"]}}
  ],
  "summary": "{summary_instruction}",
  "missing_items": ["list of missing required items — empty array if none"],
  "required_documents": [
    {{"name": "document name", "status": "required" | "provided" | "not_applicable"}}
  ],
  "flags": ["list of policy concerns — empty array if none"],
  "next_steps": ["ordered list of what the requester must do next"],
  "federal_notes": "additional notes if federal funds involved — empty string if not",
  "faa_notes": "FAA compliance summary if faa_governed=yes — empty string if not",
  "pcard_eligible": true | false,
  "pcard_note": "explanation of P-Card eligibility decision"
}}

For valid_methods: list ALL procurement paths that are legally permissible for this purchase type and amount. If P-Card is a valid option, list it FIRST. If P-Card is prohibited for this item type or the amount exceeds the card limit, do not include it. Always include at least one method."""