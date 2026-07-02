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
