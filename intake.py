# intake.py
import json

import policy_rag
from procurement_config import cfg


def _policy_queries(data: dict) -> list[str]:
    """Build targeted queries to retrieve relevant policy passages."""
    item_type = data.get("item_type", "other")
    queries   = [f"{item_type} procurement purchasing policy requirements"]

    if data.get("pcard") == "yes":
        queries.append("purchasing card P-Card policy prohibited allowed categories")
    if data.get("sole_source") == "yes":
        queries.append("sole source justification requirements single vendor")
    if data.get("federal_funds") == "yes":
        queries.append("federal funds procurement requirements Buy American CFR 200")
    if data.get("faa_governed") == "yes":
        queries.append("FAA airport improvement program AIP procurement DBE Buy American")
    if item_type == "professional_services":
        queries.append("professional services insurance certificate conflict of interest")
    if item_type in ("it_equipment", "it_software"):
        queries.append("information technology IT equipment software approval")

    queries.append("conflict of interest purchase splitting prohibited")
    return queries


def build_prompt(data: dict) -> str:
    item_type = data.get("item_type", "other")
    amount    = float(data.get("amount", 0))

    threshold_rules = json.dumps(cfg.procurement_methods(item_type), indent=2, default=str)
    is_bid          = cfg.requires_competitive_bid(item_type, amount)
    threshold_rule  = cfg.get_procurement_method(item_type, amount)
    city_name       = cfg.city_name() or "the City"

    sole_source_block = ""
    if data.get("sole_source") == "yes":
        sole_source_block = (
            f"- Proposed Vendor: {data.get('vendor_name', '')}\n"
            f"- Sole Source Justification: {data.get('sole_source_justification', '')}"
        )

    attachment_lines = "\n".join(
        f"    * {f}" for f in data.get("attachments_list", [])
    ) or "    (none)"

    threshold_range = f"${threshold_rule['min']:,.2f}-${threshold_rule['max']:,.2f}"

    bid_note = (
        f"NOTE: This request is AT OR ABOVE the ${cfg.bid_threshold(item_type):,.0f} "
        f"competitive bid threshold for {item_type}. "
        f"Procurement will manage the formal bid process — "
        f"do NOT flag this as an error or missing item. "
        f"APPROVE the request and note the bid requirement in next_steps only."
        if is_bid else
        f"NOTE: This request is BELOW the competitive bid threshold. "
        f"The required method for this amount range ({threshold_range}) is: {threshold_rule['method']}."
    )

    # Retrieve relevant policy passages from the vector store
    policy_context = policy_rag.query(_policy_queries(data))
    if policy_context:
        policy_section = f"RELEVANT POLICY FROM CITY DOCUMENTS:\n\n{policy_context}"
    else:
        policy_section = (
            "POLICY GUIDANCE:\n"
            "Apply standard California public procurement law and general best practices "
            "for municipal purchasing. Key principles: competitive bidding above thresholds, "
            "conflict of interest avoidance, purchase splitting prohibition, insurance "
            "requirements for professional services, IT Director approval for technology."
        )

    is_demo = data.get("demo", False)
    summary_instruction = (
        "2-3 sentences. If the purchase is P-Card eligible, assume it WILL be paid by P-Card — "
        "say: 'This purchase can be made using a P-Card, and no vendor quote is required at this "
        "stage. The estimated amount is below the threshold for formal bidding requirements. If the "
        "purchase exceeds your individual P-Card transaction or available credit limit, contact "
        "Procurement before proceeding.' "
        "If the purchase is NOT P-Card eligible, state what procurement method applies. "
        "If vendor quotes are required, say 'You will need [number] vendor quote(s) when you are "
        "ready to submit this request to Procurement.' "
        "Do NOT mention approvals, approval chains, or approving officials — those happen after "
        "submission and are handled separately. Do NOT list approvals as documents needed. "
        "Do NOT use the word 'missing'. Do NOT say the request is incomplete."
        if is_demo else
        "2-3 sentence plain English summary of the decision"
    )

    return f"""You are a procurement compliance officer for {city_name}.
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

{policy_section}

PROCESSING RULES — apply these regardless of policy source:

IT MISCATEGORIZATION: If the item name or description strongly suggests IT equipment or
software (computers, laptops, tablets, phones, monitors, servers, software licenses,
subscriptions, etc.) but the stated type is NOT it_equipment or it_software, add a flag:
"This item appears to be IT equipment or software. If so, it should be recategorized as
IT Equipment or IT Software — P-Card is prohibited and IT Director approval is required
regardless of amount."
Do NOT explain what applies under the current (potentially wrong) categorization in the flag.
The flag text should only describe the consequence of correct categorization.

P-CARD: P-Card is a PAYMENT METHOD, not a procurement method. Never include "P-Card"
in the procurement_method field. Apply these rules strictly in order:
- If pcard=no: set pcard_eligible=false, pcard_note="P-Card not requested".
- If the stated item type is prohibited for P-Card: set pcard_eligible=false, explain why.
- If pcard=yes AND stated type is P-Card eligible AND amount <= ${cfg.pcard_transaction_limit():,.0f}:
  set pcard_eligible=true, pcard_note="P-Card approved within the ${cfg.pcard_transaction_limit():,.0f} single-transaction limit".
- If pcard=yes AND stated type is P-Card eligible AND amount > ${cfg.pcard_transaction_limit():,.0f}:
  set pcard_eligible=true.
  pcard_note="Exceeds the standard ${cfg.pcard_transaction_limit():,.0f} single-transaction limit.
  A temporary limit increase requires prior Procurement approval before purchasing."
  Add to next_steps: "Contact Procurement to request a temporary P-Card limit increase before purchasing."
  Include in summary: state that P-Card can be used but a temporary limit increase must be
  approved by Procurement before the purchase is made.

SOLE SOURCE: If sole_source=yes, note that a sole source justification document will
be required at submission. Do NOT change the verdict or flag as non-compliant now.

FAA: If faa_governed=yes, populate faa_notes with a plain-English compliance summary
of applicable FAA requirements (Buy American, DBE, contract clauses, thresholds, etc.).
Do NOT change the verdict or procurement method. If faa_governed=no, set faa_notes="".

FEDERAL FUNDS: If federal_funds=yes, apply additional requirements from the policy
above to flags and next_steps. Do NOT change the procurement method.

EMAIL: Do not flag or comment on the requester's email domain — notification only.

ACCOUNT CODE: Optional at intake — never flag a missing account code.

DOCUMENT MATCHING — check each attached file using fuzzy matching:
- quote, quotation, bid, proposal, estimate → vendor quote PROVIDED
- insurance, cert, coi, acord, liability    → insurance cert PROVIDED
- sole, source, letter                      → sole source letter PROVIDED
- contract, agreement, scope, sow           → contract PROVIDED
- w9, w-9, vendor-form                      → vendor form PROVIDED
Mark as provided generously. Only mark required if NO file could reasonably match.

REQUIRED DOCUMENTS BY PURCHASE TYPE:
- supplies/equipment under ${cfg.bid_threshold('supplies'):,.0f}: vendor quote (if not sole source)
- supplies/equipment over that threshold: three vendor quotes
- it_equipment/it_software: IT Director approval form (next step, not blocking)
- professional_services: insurance certificate
- construction: engineering plans (next step for large projects)
- travel: travel authorization form
- bid path (at/above threshold): do NOT require quotes — Procurement solicits bids formally

{"DEMO MODE — override normal verdict logic:" if is_demo else ""}
{"""- Policy preview only. No documents submitted yet.
- NEVER return RETURNED for missing documents — they are not expected at this stage.
- Only return RETURNED for a genuine policy violation (e.g. sole source with no justification).
- Return APPROVED for all standard, compliant requests regardless of attachment count.""" if is_demo else ""}

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

For valid_methods: each tier in APPLICABLE PROCUREMENT THRESHOLDS above covers a distinct, non-overlapping dollar range (its "min" to its "max"). Return ONLY the ONE tier whose range contains the Estimated Amount (${amount:,.2f}) — that is "{threshold_rule['method']}" ({threshold_range}). Do NOT include tiers for other amount ranges — e.g. do not show a higher-tier formal bid process alongside a lower-tier method that already applies to this request's actual amount. Do NOT mention P-Card anywhere in valid_methods — not in the method name, description, or documents_needed. P-Card is a payment method handled separately via pcard_eligible and pcard_note. Always include exactly one method."""
