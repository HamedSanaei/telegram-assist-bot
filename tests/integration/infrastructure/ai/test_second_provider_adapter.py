"""Integration tests for DeepSeekProvider adapter with a fake local HTTP server."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionContext,
    CategorizationContext,
    ScoringContext,
    SemanticDuplicateContext,
)
from telegram_assist_bot.infrastructure.ai.deepseek import DeepSeekProvider
from telegram_assist_bot.infrastructure.ai.z_ai import ZAIProvider
from telegram_assist_bot.shared.errors import (
    AuthorizationError,
    OperationTimeoutError,
    PermanentOperationError,
    PermissionDeniedError,
    RateLimitError,
    TransientOperationError,
    ValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Generator

_REQUEST_COUNTER = 0
_REQUEST_COUNTER_LOCK = threading.Lock()


class MockDeepSeekHandler(BaseHTTPRequestHandler):
    """Fake local HTTP server handler for DeepSeek API completions."""

    def log_message(self, format: str, *args: object) -> None:
        """Suppress HTTP server access logs during tests."""

    def do_POST(self) -> None:
        global _REQUEST_COUNTER
        with _REQUEST_COUNTER_LOCK:
            _REQUEST_COUNTER += 1

        auth = self.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized: Missing Bearer Token"}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid JSON payload"}')
            return

        if payload.get("thinking") != {"type": "disabled"}:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "thinking mode must be disabled"}')
            return

        if payload.get("response_format") != {"type": "json_object"}:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "response_format must be json_object"}')
            return

        if self.path.startswith("/timeout"):
            time.sleep(0.5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            return

        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()
            return

        if self.path.startswith("/oversized"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (1024 * 1024 + 10))
            return

        if self.path.startswith("/malformed_json"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{invalid_json}")
            return

        if self.path.startswith("/invalid_utf8"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"\xff\xfe")
            return

        if self.path.startswith("/malformed_envelope"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices": []}')
            return

        if self.path.startswith("/auth_fail"):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return

        if self.path.startswith("/permission_fail"):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"error": "Forbidden"}')
            return

        if self.path.startswith("/rate_limit"):
            self.send_response(429)
            self.send_header("Retry-After", "60")
            self.end_headers()
            self.wfile.write(b'{"error": "Rate limit exceeded"}')
            return

        if self.path.startswith("/server_error"):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "Internal Server Error"}')
            return

        if self.path.startswith("/rejected_with_sensitive_body"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "sensitive-provider-payload"}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"choices": [{"message": {"role": "assistant", '
            b'"content": "{\\"result\\": \\"success\\"}"}}], '
            b'"usage": {"prompt_tokens": 15, "completion_tokens": 20}}'
        )


@pytest.fixture(scope="module")
def mock_server() -> Generator[str, None, None]:
    """Fixture to start and stop the local thread-based HTTP mock server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockDeepSeekHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(autouse=True)
def reset_request_counter() -> None:
    global _REQUEST_COUNTER
    with _REQUEST_COUNTER_LOCK:
        _REQUEST_COUNTER = 0


def test_successful_request_mapping_all_tasks_and_models(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="synthetic-" + "credential", base_url=mock_server
        )
        models = ["deepseek-v4-flash", "deepseek-v4-pro"]
        tasks = [
            (
                AITaskType.ADVERTISEMENT_DETECTION,
                AdvertisementDetectionContext(text="text"),
            ),
            (
                AITaskType.SEMANTIC_DUPLICATE,
                SemanticDuplicateContext(
                    text="a", compare_text="b", similarity_threshold=0.8
                ),
            ),
            (
                AITaskType.CATEGORIZATION,
                CategorizationContext(text="text", allowed_categories=["cat"]),
            ),
            (AITaskType.SCORING, ScoringContext(text="text")),
        ]

        for model in models:
            for task_type, ctx in tasks:
                envelope = await provider.execute_attempt(
                    task_type=task_type,
                    prompt="Some prompt {text}",
                    request_context=ctx,
                    provider_name="deepseek",
                    model_name=model,
                    timeout_seconds=5.0,
                )
                assert envelope.status_code == 200
                assert envelope.input_tokens == 15
                assert envelope.output_tokens == 20
                assert "choices" in envelope.raw_content
                assert envelope.headers is not None
                headers_lower = {k.lower(): v for k, v in envelope.headers.items()}
                assert "content-type" in headers_lower

    asyncio.run(scenario())


def test_thinking_disabled_and_json_format_passed_to_server(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=mock_server)
        ctx = AdvertisementDetectionContext(text="text")
        envelope = await provider.execute_attempt(
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt="Is ad? {text}",
            request_context=ctx,
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
            timeout_seconds=5.0,
        )
        assert envelope.status_code == 200

    asyncio.run(scenario())


def test_unsupported_model_and_task_rejected_before_http(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=mock_server)
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(ValidationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-chat",
                timeout_seconds=5.0,
            )
        assert "Unsupported model" in str(exc.value.__cause__)
        global _REQUEST_COUNTER
        assert _REQUEST_COUNTER == 0

    asyncio.run(scenario())


def test_timeout_handling(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=f"{mock_server}/timeout")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(OperationTimeoutError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=0.1,
            )

    asyncio.run(scenario())


def test_connection_failure() -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url="http://127.0.0.1:55433")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(TransientOperationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_401_auth_fail(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=f"{mock_server}/auth_fail")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(AuthorizationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_403_permission_fail(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/permission_fail"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermissionDeniedError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_429_rate_limit(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=f"{mock_server}/rate_limit")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(RateLimitError) as exc_info:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

        assert getattr(exc_info.value, "retry_after", None) == "60"

    asyncio.run(scenario())


def test_http_5xx_server_error(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/server_error"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(TransientOperationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_malformed_json(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/malformed_json"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        assert "Malformed response envelope: invalid JSON" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_malformed_envelope(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/malformed_envelope"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        assert "Malformed response envelope" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_invalid_utf8_response_is_safely_rejected(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/invalid_utf8"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        assert "invalid UTF-8" in str(exc.value.__cause__)
        assert "\\xff" not in repr(exc.value.__cause__)

    asyncio.run(scenario())


def test_oversized_response(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=f"{mock_server}/oversized")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        assert "Response size limit exceeded" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_redirect_rejection(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=f"{mock_server}/redirect")
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        assert "Redirects are rejected" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_production_host_allowlist() -> None:
    provider_prod = DeepSeekProvider(api_key="key", base_url="https://api.deepseek.com")
    assert provider_prod._base_url == "https://api.deepseek.com"

    provider_local = DeepSeekProvider(api_key="key", base_url="http://localhost:8000")
    assert provider_local._base_url == "http://localhost:8000"

    with pytest.raises(ValidationError) as exc:
        DeepSeekProvider(api_key="key", base_url="https://api.deepseek-fake.com")
    assert "not approved" in str(exc.value.__cause__)

    with pytest.raises(ValidationError) as exc:
        DeepSeekProvider(api_key="key", base_url="http://api.deepseek.com")
    assert "Only HTTPS scheme is allowed" in str(exc.value.__cause__)


def test_secret_redaction(mock_server: str) -> None:
    async def scenario() -> None:
        secret_key = "deepseek_" + "credential_to_be_hidden_987654321"
        sensitive_prompt = "sensitive prompt that must stay private"
        provider = DeepSeekProvider(
            api_key=secret_key, base_url=f"{mock_server}/auth_fail"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(AuthorizationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt=sensitive_prompt,
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

        rendered_error = " ".join(
            (str(exc.value), repr(exc.value), str(exc.value.__cause__))
        )
        assert secret_key not in rendered_error
        assert sensitive_prompt not in rendered_error

        provider_success = DeepSeekProvider(api_key=secret_key, base_url=mock_server)
        envelope = await provider_success.execute_attempt(
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt="prompt",
            request_context=ctx,
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
            timeout_seconds=5.0,
        )
        assert envelope.headers is not None
        for val in envelope.headers.values():
            assert secret_key not in val
        assert {key.lower() for key in envelope.headers} == {"content-type"}

    asyncio.run(scenario())


def test_rejected_raw_response_does_not_escape_exception(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key",
            base_url=f"{mock_server}/rejected_with_sensitive_body",
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="private prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )
        rendered_error = " ".join(
            (str(exc.value), repr(exc.value), str(exc.value.__cause__))
        )
        assert "sensitive-provider-payload" not in rendered_error
        assert "private prompt" not in rendered_error

    asyncio.run(scenario())


def test_exactly_one_http_request(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(api_key="key", base_url=mock_server)
        ctx = AdvertisementDetectionContext(text="text")

        envelope = await provider.execute_attempt(
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt="prompt",
            request_context=ctx,
            provider_name="deepseek",
            model_name="deepseek-v4-flash",
            timeout_seconds=5.0,
        )
        assert envelope.status_code == 200

        global _REQUEST_COUNTER
        assert _REQUEST_COUNTER == 1

    asyncio.run(scenario())


def test_independent_lifecycle_of_z_ai_and_deepseek() -> None:
    z_provider = ZAIProvider(api_key="z-key", base_url="https://api.z.ai/api/paas/v4")
    ds_provider = DeepSeekProvider(
        api_key="ds-key", base_url="https://api.deepseek.com"
    )

    assert z_provider._api_key == "z-key"
    assert ds_provider._api_key == "ds-key"


def test_no_automatic_fallback_or_routing(mock_server: str) -> None:
    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key="key", base_url=f"{mock_server}/server_error"
        )
        ctx = AdvertisementDetectionContext(text="text")

        with pytest.raises(TransientOperationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                timeout_seconds=5.0,
            )

        global _REQUEST_COUNTER
        assert _REQUEST_COUNTER == 1

    asyncio.run(scenario())
