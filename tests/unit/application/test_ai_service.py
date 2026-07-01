"""Unit tests for AI provider fallback logic (z.ai -> DeepSeek)."""

from __future__ import annotations

import pytest

from src.application.ai_service import AiService
from src.domain.enums import PostCategory
from src.shared.errors import (
    DuplicateDetectionError,
    InvalidPostError,
    PostClassificationError,
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
