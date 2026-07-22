"""Unit tests for deterministic versioned AI cache identities."""

# ruff: noqa: RUF001

from __future__ import annotations

from telegram_assist_bot.application.ai.cache_key import (
    AICacheIdentity,
    build_ai_cache_identity,
)
from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.shared.config import AiCachePolicyConfig


def _identity(
    text: str,
    *,
    task: AITaskType = AITaskType.ADVERTISEMENT_DETECTION,
    prompt: str = "1",
    schema: str = "1",
    language: str = "fa-IR",
    version: int = 1,
) -> AICacheIdentity:
    return build_ai_cache_identity(
        task_type=task,
        request_context=AdvertisementDetectionContext(text=text),
        prompt_version=prompt,
        schema_version=schema,
        language=language,
        key_version=version,
    )


def test_cache_key_is_deterministic_and_uses_approved_exact_normalization() -> None:
    first = _identity("سلام\u200cدنیا  \r\n🙂")
    second = _identity("سلام\u200cدنیا\n🙂")

    assert first == second
    assert len(first.cache_key) == 64
    assert len(first.input_hash) == 64
    assert first.language == "fa-ir"


def test_persian_zwnj_emoji_and_utf8_remain_identity_significant() -> None:
    joined = _identity("سلام\u200cدنیا 🙂")
    separated = _identity("سلام دنیا 🙂")
    different_emoji = _identity("سلام\u200cدنیا 🚀")

    assert joined.input_hash != separated.input_hash
    assert joined.input_hash != different_emoji.input_hash


def test_every_cache_dimension_produces_a_miss() -> None:
    baseline = _identity("متن")
    variants = {
        _identity("متن دیگر").cache_key,
        _identity("متن", task=AITaskType.SCORING).cache_key,
        _identity("متن", prompt="2").cache_key,
        _identity("متن", schema="2").cache_key,
        _identity("متن", language="en-US").cache_key,
        _identity("متن", version=2).cache_key,
    }
    assert baseline.cache_key not in variants
    assert len(variants) == 6


def test_configuration_alias_maps_once_to_canonical_task() -> None:
    policy = AiCachePolicyConfig(
        task="duplicate_detection",  # type: ignore[arg-type]
        enabled=True,
        ttl_seconds=60,
    )
    assert policy.task is AITaskType.SEMANTIC_DUPLICATE


def test_ai_result_cache_metadata_is_additive_and_backward_compatible() -> None:
    result = AIResult(
        success=True,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="provider",
        model_name="model",
        result={"is_advertisement": False},
        confidence=None,
        reason=None,
        prompt_version="1",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
        latency=None,
        input_tokens=None,
        output_tokens=None,
    )
    legacy = result.model_dump(
        exclude={"cache_hit", "cache_age_seconds", "side_effect_warnings"}
    )
    restored = AIResult.model_validate(legacy)

    assert restored.cache_hit is False
    assert restored.cache_age_seconds is None
    assert restored.side_effect_warnings == ()
