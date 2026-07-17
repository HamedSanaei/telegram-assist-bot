"""Integration tests for ZAIProvider adapter with a fake local HTTP server."""

from __future__ import annotations

from collections.abc import Generator
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.infrastructure.ai.z_ai import ZAIProvider
from telegram_assist_bot.shared.errors import (
    AuthorizationError,
    OperationTimeoutError,
    PermanentOperationError,
    PermissionDeniedError,
    RateLimitError,
    TransientOperationError,
)

_URI_ENV = "TEST_MONGODB_URI"


class MockZAIHandler(BaseHTTPRequestHandler):
    """Fake local HTTP server handler for Z.AI API completions."""

    def log_message(self, format: str, *args: object) -> None:
        # Suppress logging to stdout/stderr during tests
        pass

    def do_POST(self) -> None:
        # Validate Authorization header presence
        auth = self.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized: Missing Bearer Token"}')
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

        if self.path.startswith("/model_unavailable"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "model_not_found"}')
            return

        # Default success response
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"choices": [{"message": {"role": "assistant", '
            b'"content": "{\\"is_advertisement\\": false}"}}], '
            b'"usage": {"prompt_tokens": 5, "completion_tokens": 10}}'
        )


@pytest.fixture(scope="module")
def mock_server() -> Generator[str, None, None]:
    """Fixture to start and stop the local thread-based HTTP mock server."""
    server = HTTPServer(("127.0.0.1", 0), MockZAIHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()
    thread.join()


def test_successful_request_mapping(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(api_key="dummy-key", base_url=mock_server)
        ctx = AdvertisementDetectionContext(text="Some post text")

        envelope = await provider.execute_attempt(
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt="Is this an ad? {text}",
            request_context=ctx,
            provider_name="z-ai",
            model_name="glm-4.7-flash",
            timeout_seconds=5.0,
        )

        assert envelope.status_code == 200
        assert envelope.input_tokens == 5
        assert envelope.output_tokens == 10
        assert "choices" in envelope.raw_content
        assert envelope.headers is not None

        headers_lower = {k.lower(): v for k, v in envelope.headers.items()}
        assert "content-type" in headers_lower

    asyncio.run(scenario())


def test_timeout_handling(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(api_key="dummy-key", base_url=f"{mock_server}/timeout")
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(OperationTimeoutError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=0.1,  # Short timeout
            )

    asyncio.run(scenario())


def test_connection_failure() -> None:
    async def scenario() -> None:
        # Invalid loopback port (no listener)
        provider = ZAIProvider(api_key="dummy-key", base_url="http://127.0.0.1:55432")
        ctx = AdvertisementDetectionContext(text="Some text")

        with pytest.raises(TransientOperationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_401_auth_fail(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(api_key="dummy-key", base_url=f"{mock_server}/auth_fail")
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(AuthorizationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_403_permission_fail(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/permission_fail"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermissionDeniedError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_http_429_rate_limit(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/rate_limit"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(RateLimitError) as exc_info:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )

        assert getattr(exc_info.value, "retry_after", None) == "60"

    asyncio.run(scenario())


def test_http_5xx_server_error(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/server_error"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(TransientOperationError):
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )

    asyncio.run(scenario())


def test_model_unavailable(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/model_unavailable"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )
        assert "Model unavailable" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_malformed_json(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/malformed_json"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )
        assert "Malformed response envelope: invalid JSON" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_malformed_envelope(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(
            api_key="dummy-key", base_url=f"{mock_server}/malformed_envelope"
        )
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )
        assert "Malformed response envelope" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_oversized_response(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(api_key="dummy-key", base_url=f"{mock_server}/oversized")
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )
        assert "Response size limit exceeded" in str(exc.value.__cause__)

    asyncio.run(scenario())


def test_redirect_rejection(mock_server: str) -> None:
    async def scenario() -> None:
        provider = ZAIProvider(api_key="dummy-key", base_url=f"{mock_server}/redirect")
        ctx = AdvertisementDetectionContext(text="Some post text")

        with pytest.raises(PermanentOperationError) as exc:
            await provider.execute_attempt(
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt="prompt",
                request_context=ctx,
                provider_name="z-ai",
                model_name="glm-4.7-flash",
                timeout_seconds=5.0,
            )
        assert "Redirects are rejected" in str(exc.value.__cause__)

    asyncio.run(scenario())
