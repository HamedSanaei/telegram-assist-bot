"""Unit tests for AI response normalization."""

from __future__ import annotations

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.response_normalizer import ResponseNormalizer
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionOutput


@pytest.fixture
def normalizer() -> ResponseNormalizer:
    return ResponseNormalizer()


def test_successful_normalization(normalizer: ResponseNormalizer) -> None:
    envelope = RawResponseEnvelope(
        raw_content="{}",
        status_code=200,
        headers=None,
        latency_seconds=0.75,
        input_tokens=150,
        output_tokens=300,
    )

    validated_model = AdvertisementDetectionOutput(
        is_advertisement=True,
        confidence=0.92,
        reason="Has promotional links",
    )

    result = normalizer.normalize(
        envelope=envelope,
        validated_model=validated_model,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        prompt_version="1.0.0",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
    )

    assert result.success is True
    assert result.task_type == AITaskType.ADVERTISEMENT_DETECTION
    assert result.provider_name == "deepseek"
    assert result.model_name == "deepseek-v4-flash"
    assert result.confidence == 0.92
    assert result.reason == "Has promotional links"
    assert result.prompt_version == "1.0.0"
    assert result.schema_version == "1"
    assert result.latency == 0.75
    assert result.input_tokens == 150
    assert result.output_tokens == 300
    assert result.attempt_number == 1
    assert result.fallback_count == 0
    assert result.result == {
        "is_advertisement": True,
        "confidence": 0.92,
        "reason": "Has promotional links",
    }
    assert result.created_at is not None
