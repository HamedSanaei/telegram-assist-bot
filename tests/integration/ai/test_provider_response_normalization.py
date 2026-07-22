"""Integration tests verifying RawResponseEnvelope to AIResult normalization."""

from __future__ import annotations

import json

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.response_normalizer import ResponseNormalizer
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator


def test_equivalent_normalization_z_ai_and_deepseek() -> None:
    # Sanitized response from Z.AI for advertisement_detection task
    # Z.AI payload has standard chat completion format with JSON content.
    z_ai_raw_content = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"is_advertisement": true, '
                            '"confidence": 0.9, '
                            '"reason": "تبلیغ"}'
                        ),
                    }
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 22},
        }
    )

    z_ai_envelope = RawResponseEnvelope(
        raw_content=z_ai_raw_content,
        status_code=200,
        headers=None,
        latency_seconds=1.2,
        input_tokens=12,
        output_tokens=22,
    )

    # Sanitized response from DeepSeek for same task
    # DeepSeek payload structure is identical to Z.AI.
    deepseek_raw_content = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"is_advertisement": true, '
                            '"confidence": 0.9, '
                            '"reason": "تبلیغ"}'
                        ),
                    }
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25},
        }
    )

    deepseek_envelope = RawResponseEnvelope(
        raw_content=deepseek_raw_content,
        status_code=200,
        headers=None,
        latency_seconds=0.8,
        input_tokens=15,
        output_tokens=25,
    )

    parser = ResponseParser()
    validator = ResponseValidator()
    normalizer = ResponseNormalizer()

    # Normalize Z.AI
    z_parsed, _ = parser.parse(z_ai_envelope)
    z_val = validator.validate(z_parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")
    z_result = normalizer.normalize(
        envelope=z_ai_envelope,
        validated_model=z_val,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="z-ai",
        model_name="glm-4.7-flash",
        prompt_version="1.0.0",
        schema_version="1",
    )

    # Normalize DeepSeek
    ds_parsed, _ = parser.parse(deepseek_envelope)
    ds_val = validator.validate(ds_parsed, AITaskType.ADVERTISEMENT_DETECTION, "1")
    ds_result = normalizer.normalize(
        envelope=deepseek_envelope,
        validated_model=ds_val,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        prompt_version="1.0.0",
        schema_version="1",
    )

    # Assert equivalent outcomes and data formats
    assert z_result.success is True
    assert ds_result.success is True

    assert z_result.task_type == ds_result.task_type
    assert z_result.schema_version == ds_result.schema_version

    # Ensure actual result payloads are identical dictionaries
    assert z_result.result == ds_result.result
    assert z_result.result == {
        "is_advertisement": True,
        "confidence": 0.9,
        "reason": "تبلیغ",
    }

    # Ensure provider-specific details are correctly preserved
    assert z_result.provider_name == "z-ai"
    assert z_result.model_name == "glm-4.7-flash"
    assert z_result.latency == 1.2
    assert z_result.input_tokens == 12
    assert z_result.output_tokens == 22

    assert ds_result.provider_name == "deepseek"
    assert ds_result.model_name == "deepseek-v4-flash"
    assert ds_result.latency == 0.8
    assert ds_result.input_tokens == 15
    assert ds_result.output_tokens == 25
