# classify_request_type.py
"""
Advisory goods/service/mixed classification for the intake flow.

Single stateless LLM call — no retrieval, no embeddings, no vector store.
Same pattern as the sole-source justification helper in app.py. Purely
advisory: sets requester expectations about likely documentation, never
gates or routes the request.
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from procurement_config import cfg

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_SYSTEM_PROMPT = """You classify a municipal procurement request as GOODS, SERVICE, or MIXED, based only on its description.

- goods: a physical item is being purchased, with no labor or service component described.
- service: labor, expertise, or an ongoing service is being purchased, with no physical item transferred.
- mixed: the description plausibly involves both goods and services, or is too ambiguous to tell which.

Use "low" confidence whenever the description could reasonably support more than one category.

EXAMPLES:
Description: "12 foot ladder"
{"type": "goods", "confidence": "high", "reasoning": "A single physical item with no labor or service component."}

Description: "consultant will evaluate our sprinkler system"
{"type": "service", "confidence": "high", "reasoning": "Paying for a consultant's expertise and evaluation work, not a physical item."}

Description: "replace the sprinkler system"
{"type": "mixed", "confidence": "low", "reasoning": "Could mean parts only, labor only, or both, and the description doesn't say which."}

Description: "laptop for new hire"
{"type": "goods", "confidence": "high", "reasoning": "A single physical device with no service component mentioned."}

Description: "annual landscaping maintenance contract"
{"type": "service", "confidence": "high", "reasoning": "An ongoing service contract, not a physical item."}

Description: "software license with implementation support"
{"type": "mixed", "confidence": "high", "reasoning": "Explicitly names both a goods component (the license) and a service component (implementation support)."}

Description: "portable gas powered compressor and commercial paint spraying equipment for outdoor large area painting"
{"type": "goods", "confidence": "high", "reasoning": "Physical equipment being purchased, with no labor or service component described."}

Description: "squad car for PSPD to replace one that was totalled"
{"type": "goods", "confidence": "high", "reasoning": "A single vehicle purchase with no service component described."}

Description: "HVAC unit replacement including installation"
{"type": "mixed", "confidence": "high", "reasoning": "Explicitly names both a goods component (the unit) and a service component (installation)."}

Return ONLY this JSON — no markdown, no text outside the JSON:
{"type": "goods" | "service" | "mixed", "confidence": "high" | "low", "reasoning": "one sentence"}
"""


def classify_request_type(description: str) -> dict:
    """
    Classify a procurement request description as goods, service, or mixed.
    Advisory only — sets requester expectations, does not gate or route.
    """
    response = client.chat.completions.create(
        model=cfg.ai_model(),
        max_tokens=150,
        temperature=0.1,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f'Description: "{description}"'},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    result = json.loads(content)
    return {
        "type": result.get("type"),
        "confidence": result.get("confidence"),
        "reasoning": result.get("reasoning", ""),
    }
