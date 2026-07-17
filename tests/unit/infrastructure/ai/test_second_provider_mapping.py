"""Unit tests for mapping, capabilities, and URL validations of DeepSeek provider."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.application.ports.ai_provider import AIProvider
from telegram_assist_bot.infrastructure.ai.deepseek import (
    DeepSeekProvider,
    _format_prompt,
    validate_deepseek_base_url,
)
from telegram_assist_bot.shared.errors import ValidationError


class DummyContext(BaseModel):
    text: str
    allowed_categories: list[str]


def test_validate_deepseek_base_url_valid() -> None:
    validate_deepseek_base_url("https://api.deepseek.com")
    validate_deepseek_base_url("https://api.deepseek.com:443/")
    validate_deepseek_base_url("http://localhost:8000/v1")
    validate_deepseek_base_url("http://127.0.0.1:8080")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.deepseek.com",
        "https://malicious-site.example",
        "https://api.deepseek.com.attacker.example",
        "https://api.deepseek.com:8443",
        "https://api.deepseek.com/v1",
        "https://user:password@api.deepseek.com",
        "https://api.deepseek.com?credential=value",
        "https://api.deepseek.com#fragment",
        "ftp://127.0.0.1/resource",
        "",
    ],
)
def test_validate_deepseek_base_url_invalid(base_url: str) -> None:
    with pytest.raises(ValidationError):
        validate_deepseek_base_url(base_url)


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
    with pytest.raises(ValidationError) as exc:
        DeepSeekProvider(api_key="   ")
    assert "DeepSeek API Key cannot be blank" in str(exc.value.__cause__)

    provider = DeepSeekProvider(api_key="valid-key")
    ctx = AdvertisementDetectionContext(text="Some text")

    async def scenario() -> None:
        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="deepseek-v4-flash",
                timeout_seconds=30.0,
            )
        assert "Unsupported provider" in str(exc.value.__cause__)

        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-chat",
                timeout_seconds=30.0,
            )
        assert "Unsupported model" in str(exc.value.__cause__)

        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-reasoner",
                timeout_seconds=30.0,
            )
        assert "Unsupported model" in str(exc.value.__cause__)

        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=None,  # type: ignore[arg-type]
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=30.0,
            )
        assert "Unsupported task type" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_deepseek_provider_implements_application_port() -> None:
    provider = DeepSeekProvider(api_key="synthetic-credential")

    assert isinstance(provider, AIProvider)


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected_before_http(timeout_seconds: float) -> None:
    provider = DeepSeekProvider(
        api_key="synthetic-credential",
        base_url="http://127.0.0.1:1",
    )

    async def scenario() -> None:
        with pytest.raises(ValidationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=AdvertisementDetectionContext(text="text"),
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=timeout_seconds,
            )

    asyncio.run(scenario())
