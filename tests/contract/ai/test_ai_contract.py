"""Contract tests for AI providers, schemas, and normalized formats."""

from __future__ import annotations

import json

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.exceptions import AISchemaValidationError
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionOutput,
    CategorizationOutput,
    ScoringOutput,
    SemanticDuplicateOutput,
)


def test_raw_response_envelope_mapping() -> None:
    """Verify RawResponseEnvelope matches provider contracting formats."""
    raw_body = json.dumps({"choices": [{"message": {"content": "raw content"}}]})
    envelope = RawResponseEnvelope(
        raw_content=raw_body,
        status_code=200,
        headers={"content-type": "application/json"},
        latency_seconds=1.25,
        input_tokens=150,
        output_tokens=40,
    )
    assert envelope.status_code == 200
    assert envelope.latency_seconds == 1.25
    assert envelope.input_tokens == 150
    assert envelope.output_tokens == 40
    assert "choices" in envelope.raw_content


def test_schema_contracts() -> None:
    """Verify fields and types of standard AI task output schemas."""
    # 1. Advertisement
    ad_out = AdvertisementDetectionOutput(
        is_advertisement=True, confidence=0.99, reason="ad detected"
    )
    assert ad_out.is_advertisement is True
    assert ad_out.confidence == 0.99

    # 2. Semantic duplicate
    dup_out = SemanticDuplicateOutput(
        is_duplicate=True, similarity=0.87, confidence=0.9, reason="looks similar"
    )
    assert dup_out.similarity == 0.87
    assert dup_out.is_duplicate is True

    # 3. Categorization
    cat_out = CategorizationOutput(
        category_id="news", confidence=0.85, reason="category fits"
    )
    assert cat_out.category_id == "news"
    assert cat_out.confidence == 0.85

    # 4. Scoring
    score_out = ScoringOutput(score=92, confidence=0.90, reason="high quality")
    assert score_out.score == 92
    assert score_out.confidence == 0.90
    assert score_out.reason == "high quality"


def test_response_parser_contract() -> None:
    """Verify that ResponseParser correctly extracts and parses JSON content."""
    json_data = '{"is_advertisement": true, "confidence": 0.95, "reason": "ad"}'
    envelope = RawResponseEnvelope(
        raw_content=json.dumps({"choices": [{"message": {"content": json_data}}]}),
        status_code=200,
        headers={},
        latency_seconds=0.1,
        input_tokens=10,
        output_tokens=5,
    )
    parser = ResponseParser()
    parsed, was_repaired = parser.parse(envelope)
    assert parsed == {"is_advertisement": True, "confidence": 0.95, "reason": "ad"}
    assert was_repaired is False


def test_response_validator_contract() -> None:
    """Verify that ResponseValidator strictly validates fields against schema."""
    valid_ad = {"is_advertisement": True, "confidence": 0.95, "reason": "ad"}
    validator = ResponseValidator()
    res_ad = validator.validate(valid_ad, AITaskType.ADVERTISEMENT_DETECTION, "1")
    assert isinstance(res_ad, AdvertisementDetectionOutput)
    assert res_ad.is_advertisement is True

    # Invalid scoring score (out of 0..100) should fail
    invalid_scoring = {"score": 150, "confidence": 0.9, "reason": "invalid"}
    with pytest.raises(AISchemaValidationError):
        validator.validate(invalid_scoring, AITaskType.SCORING, "2")
