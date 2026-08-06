"""
Unit tests for classify_request_type().

The OpenAI client is mocked, matching the pattern in tests/test_routes.py —
these tests confirm the function is wired correctly (prompt sent, JSON
response parsed into the expected shape), not that the classification
itself is accurate. Classification quality is a judgment call, not
something a mocked unit test can verify.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from classify_request_type import classify_request_type


def make_response(result: dict):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(result)
    return resp


# Few-shot examples from the system prompt, plus a couple of held-out
# ambiguous descriptions not seen in the prompt.
CASES = [
    ("12 foot ladder",
     {"type": "goods", "confidence": "high", "reasoning": "A physical item."}),
    ("consultant will evaluate our sprinkler system",
     {"type": "service", "confidence": "high", "reasoning": "Expertise, not a physical item."}),
    ("replace the sprinkler system",
     {"type": "mixed", "confidence": "low", "reasoning": "Could be parts, labor, or both."}),
    ("laptop for new hire",
     {"type": "goods", "confidence": "high", "reasoning": "A physical device."}),
    ("annual landscaping maintenance contract",
     {"type": "service", "confidence": "high", "reasoning": "An ongoing service."}),
    ("software license with implementation support",
     {"type": "mixed", "confidence": "high", "reasoning": "Both a license and support."}),
    # Held-out, not in the few-shot prompt
    ("catering for the employee appreciation event",
     {"type": "service", "confidence": "low", "reasoning": "Could include food goods and service staffing."}),
    ("network switch with 3-year support plan",
     {"type": "mixed", "confidence": "high", "reasoning": "Hardware plus a support service."}),
]


class TestClassifyRequestType:

    @pytest.mark.parametrize("description,mock_result", CASES)
    def test_returns_well_formed_output(self, description, mock_result):
        with patch(
            "classify_request_type.client.chat.completions.create",
            return_value=make_response(mock_result),
        ) as mock_create:
            result = classify_request_type(description)

        assert result["type"] in ("goods", "service", "mixed")
        assert result["confidence"] in ("high", "low")
        assert isinstance(result["reasoning"], str) and result["reasoning"]

        # The description was actually sent to the model
        sent_messages = mock_create.call_args.kwargs["messages"]
        assert description in sent_messages[-1]["content"]

    def test_reasoning_defaults_to_empty_string_when_missing(self):
        with patch(
            "classify_request_type.client.chat.completions.create",
            return_value=make_response({"type": "goods", "confidence": "high"}),
        ):
            result = classify_request_type("a shovel")
        assert result["reasoning"] == ""

    def test_uses_low_temperature(self):
        with patch(
            "classify_request_type.client.chat.completions.create",
            return_value=make_response(
                {"type": "goods", "confidence": "high", "reasoning": "x"}
            ),
        ) as mock_create:
            classify_request_type("a shovel")
        assert mock_create.call_args.kwargs["temperature"] <= 0.2
