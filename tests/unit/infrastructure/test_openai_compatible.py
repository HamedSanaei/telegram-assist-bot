"""Unit tests for the OpenAI-compatible AI provider HTTP layer."""

from __future__ import annotations

import json

import httpx
import pytest

from src.infrastructure.ai.openai_compatible import OpenAiCompatibleProvider
from src.shared.errors import AiProviderError


def _provider(handler) -> OpenAiCompatibleProvider:
    """Build a provider whose HTTP layer is served by the given handler."""
    return OpenAiCompatibleProvider(
        name="fakeai",
        api_key="k",
        base_url="https://fake.example/v1",
        default_model="fake-model",
        transport=httpx.MockTransport(handler),
    )


class TestChatErrors:
    """Tests for error reporting of the chat-completions call."""

    async def test_http_400_error_includes_api_body_and_model(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400, json={"error": {"message": "Model Not Exist"}}
            )

        provider = _provider(handler)
        with pytest.raises(AiProviderError) as excinfo:
            await provider.classify_post("متن خبر")
        message = str(excinfo.value)
        assert "HTTP 400" in message
        assert "fake-model" in message
        assert "Model Not Exist" in message

    async def test_http_429_fails_fast_for_provider_chain_fallback(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, json={"error": {"message": "quota"}})

        provider = _provider(handler)
        with pytest.raises(AiProviderError) as excinfo:
            await provider.classify_post("متن خبر")

        assert calls == 1
        assert "HTTP 429" in str(excinfo.value)

    async def test_successful_classification_roundtrip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "fake-model"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"category": "technology"}'}}
                    ]
                },
            )

        provider = _provider(handler)
        result = await provider.classify_post("متن فناوری")
        assert result.category.value == "technology"
        assert result.provider == "fakeai"

    async def test_successful_combined_analysis_roundtrip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "fake-model"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"category": "breaking_news", '
                                    '"is_duplicate": true, '
                                    '"is_advertisement": false, '
                                    '"matched_index": 0, '
                                    '"reason": "خبر تکراری است"}'
                                )
                            }
                        }
                    ]
                },
            )

        provider = _provider(handler)
        result = await provider.analyze_post("خبر فوری", ["خبر فوری قبلی"])
        assert result.category.value == "breaking_news"
        assert result.is_duplicate is True
        assert result.is_advertisement is False
        assert result.matched_index == 0
        assert result.provider == "fakeai"

    async def test_successful_analysis_detects_advertisement(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"category": "irrelevant", '
                                    '"is_duplicate": false, '
                                    '"is_advertisement": true, '
                                    '"matched_index": null, '
                                    '"reason": "تبلیغ کانال است"}'
                                )
                            }
                        }
                    ]
                },
            )

        provider = _provider(handler)
        result = await provider.analyze_post("عضو کانال ما شوید", [])
        assert result.is_advertisement is True
        assert result.reason == "تبلیغ کانال است"

    async def test_successful_quality_score_roundtrip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "fake-model"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"score": 82, "reason": "خبر ارزشمند است"}'
                            }
                        }
                    ]
                },
            )

        provider = _provider(handler)
        result = await provider.score_post("متن خبر", None, {"views": 100})
        assert result.score == 82
        assert result.reason == "خبر ارزشمند است"
        assert result.provider == "fakeai"
