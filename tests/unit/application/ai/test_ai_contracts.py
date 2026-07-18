"""Unit tests for AI contracts and schemas."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import (
    AIResult,
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionContext,
    AdvertisementDetectionOutput,
    CategorizationContext,
    CategorizationOutput,
    ScoringContext,
    ScoringOutput,
    SemanticDuplicateContext,
    SemanticDuplicateOutput,
)


def test_advertisement_detection_schemas() -> None:
    # Valid input
    ctx = AdvertisementDetectionContext(text="سلام! این یک پیام آزمایشی است.")
    assert ctx.text == "سلام! این یک پیام آزمایشی است."

    # Invalid input (empty text)
    with pytest.raises(ValidationError):
        AdvertisementDetectionContext(text="")

    # Valid output
    out = AdvertisementDetectionOutput(
        is_advertisement=True,
        confidence=0.95,
        reason="این یک پست تبلیغاتی برای خرید محصول است.",
    )
    assert out.is_advertisement is True
    assert out.confidence == 0.95
    assert out.reason == "این یک پست تبلیغاتی برای خرید محصول است."

    # Invalid output (confidence out of range)
    with pytest.raises(ValidationError):
        AdvertisementDetectionOutput(
            is_advertisement=True, confidence=1.1, reason="Test"
        )

    with pytest.raises(ValidationError):
        AdvertisementDetectionOutput(
            is_advertisement=True, confidence=-0.1, reason="Test"
        )

    # Invalid output (empty reason)
    with pytest.raises(ValidationError):
        AdvertisementDetectionOutput(is_advertisement=True, confidence=0.5, reason="")


def test_semantic_duplicate_schemas() -> None:
    # Valid input
    ctx = SemanticDuplicateContext(
        text="متن اول",
        compare_text="متن دوم",
        similarity_threshold=0.85,
    )
    assert ctx.text == "متن اول"
    assert ctx.compare_text == "متن دوم"
    assert ctx.similarity_threshold == 0.85

    # Invalid similarity_threshold
    with pytest.raises(ValidationError):
        SemanticDuplicateContext(text="A", compare_text="B", similarity_threshold=1.5)

    # Valid output
    out = SemanticDuplicateOutput(
        is_duplicate=False,
        similarity=0.1,
        confidence=0.1,
        reason="متن‌ها متفاوت هستند.",  # noqa: RUF001
    )
    assert out.is_duplicate is False

    # Invalid confidence
    with pytest.raises(ValidationError):
        SemanticDuplicateOutput(
            is_duplicate=False, similarity=0.1, confidence=-0.5, reason="Test"
        )


def test_categorization_schemas() -> None:
    # Valid input
    ctx = CategorizationContext(
        text="اخبار فناوری جدید",
        allowed_categories=["فناوری", "ورزش"],
    )
    assert ctx.text == "اخبار فناوری جدید"
    assert ctx.allowed_categories == ["فناوری", "ورزش"]

    # Invalid input (empty categories)
    with pytest.raises(ValidationError):
        CategorizationContext(text="A", allowed_categories=[])

    # Valid output
    out = CategorizationOutput(
        category_id="فناوری", confidence=0.8, reason="مربوط به تکنولوژی است."
    )
    assert out.category_id == "فناوری"

    # Invalid output
    with pytest.raises(ValidationError):
        CategorizationOutput(category_id="", confidence=0.8, reason="Test")


def test_scoring_schemas() -> None:
    # Valid input
    ctx = ScoringContext(text="یک پست عالی")
    assert ctx.text == "یک پست عالی"

    # Valid output
    out = ScoringOutput(score=8, confidence=0.9, reason="محتوای مناسب")
    assert out.score == 8

    # Invalid score (out of range 1-10)
    with pytest.raises(ValidationError):
        ScoringOutput(score=0, confidence=0.9, reason="Test")

    with pytest.raises(ValidationError):
        ScoringOutput(score=11, confidence=0.9, reason="Test")


def test_raw_response_envelope() -> None:
    envelope = RawResponseEnvelope(
        raw_content="{}",
        status_code=200,
        headers={"content-type": "application/json"},
        latency_seconds=1.2,
        input_tokens=100,
        output_tokens=50,
    )
    assert envelope.raw_content == "{}"
    assert envelope.status_code == 200


def test_ai_result() -> None:
    result = AIResult(
        success=True,
        task_type=AITaskType.SCORING,
        provider_name="provider-a",
        model_name="model-a",
        result={"score": 8},
        confidence=0.9,
        reason="Good",
        prompt_version="1.0.0",
        schema_version="1",
        latency=1.2,
        input_tokens=100,
        output_tokens=50,
        attempt_number=1,
        fallback_count=0,
    )
    assert result.success is True
    assert result.task_type == AITaskType.SCORING
    assert result.result == {"score": 8}


def test_no_infrastructure_imports_in_contracts_or_schemas() -> None:
    """Verifies that application contracts and schemas do not import from

    infrastructure or presentation layers.
    """
    ai_dir = (
        Path(__file__).parent.parent.parent.parent
        / "src"
        / "telegram_assist_bot"
        / "application"
        / "ai"
    )
    files_to_check = [
        ai_dir / "contracts.py",
        ai_dir / "schemas.py",
        ai_dir / "prompt_registry.py",
    ]

    for file_path in files_to_check:
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    assert not name.startswith("telegram_assist_bot.infrastructure")
                    assert not name.startswith("telegram_assist_bot.presentation")
                    assert not name.startswith("telegram_assist_bot.workers")
                    assert "telethon" not in name
                    assert "aiogram" not in name
                    assert "pymongo" not in name
            elif isinstance(node, ast.ImportFrom):
                module = node.module
                if module:
                    assert not module.startswith("telegram_assist_bot.infrastructure")
                    assert not module.startswith("telegram_assist_bot.presentation")
                    assert not module.startswith("telegram_assist_bot.workers")
                    assert "telethon" not in module
                    assert "aiogram" not in module
                    assert "pymongo" not in module
