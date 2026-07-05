"""Unit tests for AI provider chain fallback logic."""

from __future__ import annotations

import pytest

from src.application.ai_service import AiService
from src.domain.enums import PostCategory
from src.shared.errors import (
    DuplicateDetectionError,
    InvalidPostError,
    PostClassificationError,
    QualityScoringError,
)
from tests.unit.application.fakes import FakeAiProvider


class TestClassifyFallback:
    """Fallback behavior for classification."""

    async def test_primary_used_when_healthy(self) -> None:
        primary = FakeAiProvider(name="zai", category=PostCategory.TECHNOLOGY)
        fallback = FakeAiProvider(name="deepseek")
        service = AiService(primary, fallback)
        result = await service.classify_post("خبر تکنولوژی")
        assert result.provider == "zai"
        assert result.category == PostCategory.TECHNOLOGY
        assert fallback.classify_calls == 0

    async def test_falls_back_when_primary_fails(self) -> None:
        primary = FakeAiProvider(name="zai", fail=True)
        fallback = FakeAiProvider(name="deepseek", category=PostCategory.VPN)
        service = AiService(primary, fallback)
        result = await service.classify_post("مطلب وی‌پی‌ان")
        assert result.provider == "deepseek"
        assert result.category == PostCategory.VPN

    async def test_raises_when_all_fail(self) -> None:
        service = AiService(
            FakeAiProvider(name="zai", fail=True),
            FakeAiProvider(name="deepseek", fail=True),
        )
        with pytest.raises(PostClassificationError):
            await service.classify_post("متن")

    async def test_raises_without_fallback(self) -> None:
        service = AiService(FakeAiProvider(name="zai", fail=True), fallback=None)
        with pytest.raises(PostClassificationError):
            await service.classify_post("متن")

    async def test_empty_text_rejected(self) -> None:
        service = AiService(FakeAiProvider())
        with pytest.raises(InvalidPostError):
            await service.classify_post("   ")

    async def test_rate_limited_provider_is_skipped_on_next_call(self) -> None:
        primary = FakeAiProvider(
            name="google_ai_studio",
            fail=True,
            fail_message="google_ai_studio: HTTP 429 for model 'gemini': quota",
        )
        fallback = FakeAiProvider(name="deepseek", category=PostCategory.BREAKING_NEWS)
        service = AiService([primary, fallback])

        first = await service.classify_post("خبر اول")
        second = await service.classify_post("خبر دوم")

        assert first.provider == "deepseek"
        assert second.provider == "deepseek"
        assert primary.classify_calls == 1
        assert fallback.classify_calls == 2

    async def test_quota_forbidden_provider_is_skipped_on_next_call(self) -> None:
        primary = FakeAiProvider(
            name="openrouter",
            fail=True,
            fail_message="openrouter: HTTP 403 for model 'm': quota exceeded",
        )
        fallback = FakeAiProvider(name="deepseek", category=PostCategory.TECHNOLOGY)
        service = AiService([primary, fallback])

        first = await service.classify_post("خبر اول")
        second = await service.classify_post("خبر دوم")

        assert first.provider == "deepseek"
        assert second.provider == "deepseek"
        assert primary.classify_calls == 1


class TestDuplicateFallback:
    """Fallback behavior for duplicate detection."""

    async def test_primary_used_when_healthy(self) -> None:
        primary = FakeAiProvider(name="zai", duplicate=True)
        service = AiService(primary, FakeAiProvider(name="deepseek"))
        result = await service.is_duplicate("متن جدید", ["متن قدیمی"])
        assert result.is_duplicate is True
        assert result.provider == "zai"

    async def test_falls_back_when_primary_fails(self) -> None:
        primary = FakeAiProvider(name="zai", fail=True)
        fallback = FakeAiProvider(name="deepseek", duplicate=False)
        service = AiService(primary, fallback)
        result = await service.is_duplicate("متن جدید", ["متن قدیمی"])
        assert result.provider == "deepseek"

    async def test_raises_when_all_fail(self) -> None:
        service = AiService(
            FakeAiProvider(fail=True), FakeAiProvider(name="deepseek", fail=True)
        )
        with pytest.raises(DuplicateDetectionError):
            await service.is_duplicate("متن", ["قدیمی"])

    async def test_empty_existing_short_circuits(self) -> None:
        primary = FakeAiProvider(name="zai")
        service = AiService(primary)
        result = await service.is_duplicate("متن", [])
        assert result.is_duplicate is False
        assert primary.duplicate_calls == 0


class TestCombinedAnalysisFallback:
    """Fallback behavior for combined classification and duplicate checks."""

    async def test_analyzes_with_first_healthy_provider(self) -> None:
        primary = FakeAiProvider(name="google_ai_studio", fail=True)
        fallback = FakeAiProvider(
            name="deepseek", category=PostCategory.WAR, duplicate=True
        )
        service = AiService([primary, fallback])

        result = await service.analyze_post("خبر جنگ", ["خبر مشابه"])

        assert result.provider == "deepseek"
        assert result.category == PostCategory.WAR
        assert result.is_duplicate is True


class TestQualityScoringFallback:
    """Fallback behavior for quality scoring."""

    async def test_scores_with_first_healthy_provider_in_chain(self) -> None:
        google = FakeAiProvider(name="google_ai_studio", fail=True)
        groq = FakeAiProvider(name="groq", score=8.5)
        deepseek = FakeAiProvider(name="deepseek", score=6.0)
        service = AiService([google, groq, deepseek])
        result = await service.score_post(
            "خبر مهم",
            PostCategory.GENERAL_NEWS,
            {"views": 1000},
        )
        assert result.provider == "groq"
        assert result.score == 8.5
        assert deepseek.score_calls == 0

    async def test_raises_when_all_score_providers_fail(self) -> None:
        service = AiService(
            [
                FakeAiProvider(name="google_ai_studio", fail=True),
                FakeAiProvider(name="groq", fail=True),
            ]
        )
        with pytest.raises(QualityScoringError):
            await service.score_post("متن", PostCategory.GENERAL_NEWS, {})
