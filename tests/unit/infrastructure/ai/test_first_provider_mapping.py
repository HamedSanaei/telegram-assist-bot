"""Unit tests for mapping, capabilities, and URL validations of Z.AI provider."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.infrastructure.ai.z_ai import (
    ZAIProvider,
    _format_prompt,
    validate_base_url,
)
from telegram_assist_bot.shared.errors import ValidationError


class DummyContext(BaseModel):
    text: str
    allowed_categories: list[str]


def test_validate_base_url_valid() -> None:
    # Approved production and loopback URLs are valid
    validate_base_url("https://api.z.ai/api/paas/v4")
    validate_base_url("http://localhost:8000/v1")
    validate_base_url("http://127.0.0.1:8080")


def test_validate_base_url_invalid() -> None:
    # Non-HTTPS schemes for non-loopback are invalid
    with pytest.raises(ValidationError) as exc:
        validate_base_url("http://api.z.ai")
    assert "Only HTTPS scheme is allowed" in str(exc.value.__cause__)

    # Unapproved hosts are rejected
    with pytest.raises(ValidationError) as exc:
        validate_base_url("https://malicious-site.com")
    assert "Unapproved host" in str(exc.value.__cause__)

    with pytest.raises(ValidationError) as exc:
        validate_base_url("https://api.z.ai.attacker.com")
    assert "Unapproved host" in str(exc.value.__cause__)


def test_format_prompt_replacements() -> None:
    template = (
        "Hello {text}.\nAllowed: {allowed_categories}.\n"
        'Literal brackets: {"key": "val"}'
    )
    context = DummyContext(text="World", allowed_categories=["A", "B"])
    formatted = _format_prompt(template, context)

    assert "Hello World." in formatted
    assert "Allowed: A, B." in formatted
    assert 'Literal brackets: {"key": "val"}' in formatted


def test_provider_rejections_and_validations() -> None:
    # Blank API Key on init
    with pytest.raises(ValidationError) as exc:
        ZAIProvider(api_key="   ")
    assert "Z.AI API Key cannot be blank" in str(exc.value.__cause__)

    provider = ZAIProvider(api_key="valid-key")
    ctx = AdvertisementDetectionContext(text="Some text")

    async def scenario() -> None:
        # Unsupported provider
        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="other-provider",
                model_name="glm-4.7-flash",
                timeout_seconds=30.0,
            )
        assert "Unsupported provider" in str(exc.value.__cause__)

        # Unsupported model
        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="other-model",
                timeout_seconds=30.0,
            )
        assert "Unsupported model" in str(exc.value.__cause__)

        # Unsupported task type
        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=None,  # type: ignore[arg-type]
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=30.0,
            )
        assert "Unsupported task type" in str(exc.value.__cause__)

    asyncio.run(scenario())
