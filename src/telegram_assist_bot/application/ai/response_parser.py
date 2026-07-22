"""AI response parser module."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ai.exceptions import (
    AIEmptyResponseError,
    AIInvalidJSONError,
    AIRepairFailedError,
    AISchemaValidationError,
    AIValidationConstraintError,
)

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.contracts import RawResponseEnvelope


class ResponseParser:
    """Parses raw provider response envelopes and extracts the task payload."""

    @staticmethod
    def check_nesting_depth(
        data: object, max_depth: int = 5, current_depth: int = 1
    ) -> None:
        """Ensures that the JSON structure is not excessively nested."""
        if current_depth > max_depth:
            raise ValueError("Excessive JSON nesting depth")
        if isinstance(data, dict):
            for val in data.values():
                ResponseParser.check_nesting_depth(val, max_depth, current_depth + 1)
        elif isinstance(data, list):
            for item in data:
                ResponseParser.check_nesting_depth(item, max_depth, current_depth + 1)

    @staticmethod
    def attempt_repair(content: str) -> tuple[str, bool]:
        """Attempts a single deterministic repair pass to extract JSON.

        Permitted repairs:
        - Removing one Markdown JSON code fence block (```json ... ``` or ``` ... ```)
        - Stripping leading/trailing whitespace
        """
        stripped = content.strip()
        if not stripped:
            return content, False

        # If it already looks like a JSON object, no code fence repair is needed.
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped, False

        # Ensure there are exactly two code fence lines/indicators
        if stripped.count("```") != 2:
            return content, False

        # Match single Markdown code fence containing a JSON object block
        pattern = r"^```(?:json)?\s*(\{.*\})\s*```$"
        match = re.match(pattern, stripped, re.DOTALL)
        if match:
            repaired = match.group(1).strip()
            if repaired.startswith("{") and repaired.endswith("}"):
                return repaired, True

        return content, False

    def parse(self, envelope: RawResponseEnvelope) -> tuple[dict[str, Any], bool]:
        """Parses the raw content of the envelope and returns the parsed JSON dict.

        Returns a tuple of (parsed_dict, was_repaired).
        Raises AI response validation/parser exceptions on failure.
        """
        raw_body = envelope.raw_content
        if not raw_body or not raw_body.strip():
            raise AIEmptyResponseError(cause=ValueError("Envelope body is empty"))

        # 1. Parse outer envelope JSON
        try:
            envelope_data = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise AIInvalidJSONError(cause=e) from e

        if not isinstance(envelope_data, dict):
            raise AISchemaValidationError(
                cause=TypeError("Outer envelope is not a JSON object")
            )

        if "choices" not in envelope_data:
            raise AISchemaValidationError(
                cause=ValueError("Envelope is missing 'choices' key")
            )

        choices = envelope_data["choices"]
        if not isinstance(choices, list) or not choices:
            raise AIEmptyResponseError(
                cause=ValueError("Envelope 'choices' is empty or not a list")
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict) or "message" not in first_choice:
            raise AISchemaValidationError(
                cause=ValueError("Envelope choice is missing 'message' key")
            )

        message = first_choice["message"]
        if not isinstance(message, dict) or "content" not in message:
            raise AISchemaValidationError(
                cause=ValueError("Choice message is missing 'content' key")
            )

        content = message["content"]
        if content is None:
            raise AIEmptyResponseError(cause=ValueError("Message content is null"))

        content_str = str(content)
        if not content_str.strip():
            raise AIEmptyResponseError(cause=ValueError("Message content is empty"))

        # Safety Check: Oversized payload length.
        if len(content_str.encode("utf-8")) > 1024 * 1024:
            raise AIValidationConstraintError(
                cause=ValueError("Inner response content size exceeds 1 MiB limit")
            )

        # 2. Try parsing the inner content JSON
        was_repaired = False
        parsed_inner: Any = None

        try:
            parsed_inner = json.loads(content_str)
        except json.JSONDecodeError as initial_err:
            # Try single deterministic repair pass
            repaired_str, repair_success = self.attempt_repair(content_str)
            if repair_success:
                try:
                    parsed_inner = json.loads(repaired_str)
                    was_repaired = True
                except json.JSONDecodeError as repair_err:
                    raise AIRepairFailedError(cause=repair_err) from repair_err
            else:
                # No repair succeeded, raise original parse failure
                raise AIInvalidJSONError(cause=initial_err) from initial_err

        if not isinstance(parsed_inner, dict):
            raise AISchemaValidationError(
                cause=TypeError("Inner JSON payload is not a dictionary object")
            )

        # 3. Check nesting depth
        try:
            self.check_nesting_depth(parsed_inner, max_depth=5)
        except ValueError as depth_err:
            raise AIValidationConstraintError(cause=depth_err) from depth_err

        return parsed_inner, was_repaired
