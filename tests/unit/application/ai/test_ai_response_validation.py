"""Unit tests for AI response parsing and validation."""

from __future__ import annotations

import json
from typing import cast

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.exceptions import (
    AIEmptyResponseError,
    AIInvalidJSONError,
    AIRepairFailedError,
    AISchemaValidationError,
    AIValidationConstraintError,
)
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionOutput,
    CategorizationOutput,
    ScoringOutput,
    SemanticDuplicateOutput,
)


@pytest.fixture
def parser() -> ResponseParser:
    return ResponseParser()


@pytest.fixture
def validator() -> ResponseValidator:
    return ResponseValidator()


def make_envelope(content_str: str) -> RawResponseEnvelope:
    """Helper to build a RawResponseEnvelope with given message content."""
    raw_body = json.dumps(
        {
            "choices": [{"message": {"role": "assistant", "content": content_str}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
    )
    return RawResponseEnvelope(
        raw_content=raw_body,
        status_code=200,
        headers=None,
        latency_seconds=1.5,
        input_tokens=10,
        output_tokens=20,
    )


def test_valid_outputs_all_tasks(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    # 1. Advertisement Detection
    env = make_envelope(
        '{"is_advertisement": true, "confidence": 0.95, "reason": "تبلیغ کالا"}'
    )
    parsed, rep = parser.parse(env)
    assert not rep
    validated = validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")
    assert isinstance(validated, AdvertisementDetectionOutput)
    assert validated.is_advertisement is True
    assert validated.confidence == 0.95
    assert validated.reason == "تبلیغ کالا"

    # 2. Semantic Duplicate
    env = make_envelope(
        '{"is_duplicate": false, "confidence": 0.88, "reason": "متن متفاوت است"}'
    )
    parsed, rep = parser.parse(env)
    assert not rep
    validated = validator.validate(parsed, AITaskType.SEMANTIC_DUPLICATE, "1")
    assert isinstance(validated, SemanticDuplicateOutput)
    assert validated.is_duplicate is False
    assert validated.confidence == 0.88
    assert validated.reason == "متن متفاوت است"

    # 3. Categorization
    env = make_envelope(
        '{"category": "ورزشی", "confidence": 0.9, "reason": "درباره فوتبال"}'
    )
    parsed, rep = parser.parse(env)
    assert not rep
    validated = validator.validate(parsed, AITaskType.CATEGORIZATION, "1")
    assert isinstance(validated, CategorizationOutput)
    assert validated.category == "ورزشی"
    assert validated.confidence == 0.9
    assert validated.reason == "درباره فوتبال"

    # 4. Scoring
    env = make_envelope('{"score": 8, "confidence": 0.75, "reason": "کیفیت خوب"}')
    parsed, rep = parser.parse(env)
    assert not rep
    validated = validator.validate(parsed, AITaskType.SCORING, "1")
    assert isinstance(validated, ScoringOutput)
    assert validated.score == 8
    assert validated.confidence == 0.75
    assert validated.reason == "کیفیت خوب"


def test_persian_zwnj_emoji_preservation(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    text_with_special = "تبلیغاتی با نیم‌فاصله و ایموجی 🚀"
    env = make_envelope(
        f'{{"is_advertisement": true, "confidence": 0.9, '
        f'"reason": "{text_with_special}"}}'
    )
    parsed, _ = parser.parse(env)
    validated = cast(
        "AdvertisementDetectionOutput",
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1"),
    )
    assert validated.reason == text_with_special


def test_empty_and_invalid_json(parser: ResponseParser) -> None:
    # Empty envelope body
    env_empty = RawResponseEnvelope(
        raw_content="",
        status_code=None,
        headers=None,
        latency_seconds=None,
        input_tokens=None,
        output_tokens=None,
    )
    with pytest.raises(AIEmptyResponseError):
        parser.parse(env_empty)

    # Invalid outer JSON
    env_invalid_outer = RawResponseEnvelope(
        raw_content="{invalid",
        status_code=None,
        headers=None,
        latency_seconds=None,
        input_tokens=None,
        output_tokens=None,
    )
    with pytest.raises(AIInvalidJSONError):
        parser.parse(env_invalid_outer)

    # Missing choices
    env_missing_choices = RawResponseEnvelope(
        raw_content="{}",
        status_code=None,
        headers=None,
        latency_seconds=None,
        input_tokens=None,
        output_tokens=None,
    )
    with pytest.raises(AISchemaValidationError):
        parser.parse(env_missing_choices)

    # Empty choices list
    env_empty_choices = RawResponseEnvelope(
        raw_content='{"choices": []}',
        status_code=None,
        headers=None,
        latency_seconds=None,
        input_tokens=None,
        output_tokens=None,
    )
    with pytest.raises(AIEmptyResponseError):
        parser.parse(env_empty_choices)

    # Missing content inside choice message
    env_missing_content = RawResponseEnvelope(
        raw_content='{"choices": [{"message": {"role": "assistant"}}]}',
        status_code=None,
        headers=None,
        latency_seconds=None,
        input_tokens=None,
        output_tokens=None,
    )
    with pytest.raises(AISchemaValidationError):
        parser.parse(env_missing_content)

    # Inner content is empty/null/whitespace
    env_null_content = make_envelope("")
    with pytest.raises(AIEmptyResponseError):
        parser.parse(env_null_content)

    # Inner content is invalid JSON
    env_invalid_inner = make_envelope("{malformed")
    with pytest.raises(AIInvalidJSONError):
        parser.parse(env_invalid_inner)


def test_missing_and_additional_fields(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    # Missing required field "confidence"
    env_missing_field = make_envelope(
        '{"is_advertisement": true, "reason": "Missing confidence"}'
    )
    parsed, _ = parser.parse(env_missing_field)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")

    # Extra/unknown field in strict mode
    env_extra_field = make_envelope(
        '{"is_advertisement": true, '
        '"confidence": 0.9, '
        '"reason": "ok", '
        '"extra_field": 123}'
    )
    parsed, _ = parser.parse(env_extra_field)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")


def test_wrong_types_enums_ranges(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    # Wrong type for is_advertisement (string instead of bool in strict mode)
    env_wrong_type = make_envelope(
        '{"is_advertisement": "true", "confidence": 0.9, "reason": "string type"}'
    )
    parsed, _ = parser.parse(env_wrong_type)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")

    # Confidence out of range (> 1.0)
    env_high_confidence = make_envelope(
        '{"is_advertisement": true, "confidence": 1.5, "reason": "too high"}'
    )
    parsed, _ = parser.parse(env_high_confidence)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")

    # Confidence out of range (< 0.0)
    env_low_confidence = make_envelope(
        '{"is_advertisement": true, "confidence": -0.1, "reason": "too low"}'
    )
    parsed, _ = parser.parse(env_low_confidence)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")

    # Score out of range (> 10)
    env_high_score = make_envelope(
        '{"score": 11, "confidence": 0.9, "reason": "too high score"}'
    )
    parsed, _ = parser.parse(env_high_score)
    with pytest.raises(AISchemaValidationError):
        validator.validate(parsed, AITaskType.SCORING, "1")


def test_unsupported_schema_versions(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    env = make_envelope(
        '{"is_advertisement": true, "confidence": 0.9, "reason": "version mismatch"}'
    )
    parsed, _ = parser.parse(env)
    with pytest.raises(AISchemaValidationError):
        # schema_version = "2" should be rejected since SCHEMA_VERSION is "1"
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "2")


def test_deterministic_repair_successful(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    # JSON enclosed in a code fence, with whitespace
    env_fence = make_envelope(
        "\n```json\n"
        '{"is_advertisement": true, "confidence": 0.9, "reason": "repaired block"}'
        "\n```\n"
    )
    parsed, rep = parser.parse(env_fence)
    assert rep is True
    validated = cast(
        "AdvertisementDetectionOutput",
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1"),
    )
    assert validated.reason == "repaired block"

    # Non-json-specified fence
    env_fence_no_lang = make_envelope(
        "```\n"
        '{"is_advertisement": false, "confidence": 0.8, "reason": "no lang block"}'
        "\n```"
    )
    parsed, rep = parser.parse(env_fence_no_lang)
    assert rep is True
    validated = cast(
        "AdvertisementDetectionOutput",
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1"),
    )
    assert validated.reason == "no lang block"


def test_repair_failure_scenarios(parser: ResponseParser) -> None:
    # 1. Malformed JSON inside the fence
    env_malformed_inside = make_envelope("```json\n{invalid json}\n```")
    with pytest.raises(AIRepairFailedError):
        parser.parse(env_malformed_inside)

    # 2. Unrelated prose before the fence
    env_prose_before = make_envelope(
        "Here is the result:\n```json\n"
        '{"is_advertisement": true, "confidence": 0.9, "reason": "prose"}\n```'
    )
    with pytest.raises(AIInvalidJSONError):
        parser.parse(env_prose_before)

    # 3. Double/multiple code blocks
    env_double_blocks = make_envelope(
        "```json\n"
        '{"is_advertisement": true, "confidence": 0.9, "reason": "first"}\n```\n'
        "```json\n"
        '{"is_advertisement": false, "confidence": 0.8, "reason": "second"}\n```'
    )
    with pytest.raises(AIInvalidJSONError):
        parser.parse(env_double_blocks)


def test_excessively_deep_json(parser: ResponseParser) -> None:
    # 6 levels of nesting (limit is 5)
    deep_json = '{"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}}'
    env = make_envelope(deep_json)
    with pytest.raises(AIValidationConstraintError):
        parser.parse(env)


def test_oversized_content_rejection(parser: ResponseParser) -> None:
    # Create content larger than 1MB
    large_reason = "x" * (1024 * 1024 + 100)
    env = make_envelope(
        f'{{"is_advertisement": true, "confidence": 0.9, "reason": "{large_reason}"}}'
    )
    with pytest.raises(AIValidationConstraintError):
        parser.parse(env)


def test_malicious_string_safety(
    parser: ResponseParser, validator: ResponseValidator
) -> None:
    # Payload with system command injection or standard code injection strings
    env = make_envelope(
        '{"is_advertisement": true, "confidence": 0.9, '
        "\"reason\": \"__import__('os').system('echo hacked')\"}"
    )
    parsed, _ = parser.parse(env)
    validated = cast(
        "AdvertisementDetectionOutput",
        validator.validate(parsed, AITaskType.ADVERTISEMENT_DETECTION, "1"),
    )
    # Verified: it is parsed as a regular string, not executed or parsed dynamically
    assert validated.reason == "__import__('os').system('echo hacked')"
