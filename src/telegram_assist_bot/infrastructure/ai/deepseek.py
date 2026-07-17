"""DeepSeek adapter implementing the AIProvider application port using aiohttp."""

from __future__ import annotations

import json
import math
import time
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp

from telegram_assist_bot.application.ai.contracts import (
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.application.ports.ai_provider import AIProvider
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
    from collections.abc import Mapping

    from pydantic import BaseModel


DEEPSEEK_PROVIDER_NAME = "deepseek"
DEEPSEEK_PRODUCTION_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MAX_RESPONSE_BYTES = 1024 * 1024
DEEPSEEK_MODEL_CAPABILITIES: Mapping[str, frozenset[AITaskType]] = MappingProxyType(
    {
        "deepseek-v4-flash": frozenset(AITaskType),
        "deepseek-v4-pro": frozenset(AITaskType),
    }
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_SAFE_RESPONSE_HEADERS = frozenset({"content-type"})


def validate_deepseek_base_url(url_str: str) -> None:
    """Validate that the DeepSeek base URL has an approved host and secure scheme."""
    if not url_str.strip():
        raise ValidationError(cause=ValueError("DeepSeek base URL cannot be blank"))
    try:
        parsed = urlparse(url_str)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        cause = ValueError("DeepSeek base URL is invalid")
        raise ValidationError(cause=cause) from cause

    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(
            cause=ValueError("DeepSeek base URL must not contain credentials")
        )
    if parsed.query or parsed.fragment:
        raise ValidationError(
            cause=ValueError("DeepSeek base URL must not contain query or fragment")
        )
    if host == "api.deepseek.com":
        if parsed.scheme != "https":
            raise ValidationError(cause=ValueError("Only HTTPS scheme is allowed"))
        if port not in {None, 443} or parsed.path not in {"", "/"}:
            raise ValidationError(
                cause=ValueError("DeepSeek production base URL is not approved")
            )
        return
    if host in _LOOPBACK_HOSTS and parsed.scheme in {"http", "https"}:
        return
    raise ValidationError(cause=ValueError("DeepSeek base URL host is not approved"))


def _format_prompt(prompt_template: str, context: BaseModel) -> str:
    """Safely format prompt template with context fields."""
    res = prompt_template
    context_dict = context.model_dump()
    for k, v in context_dict.items():
        placeholder = f"{{{k}}}"
        if placeholder in res:
            if isinstance(v, list):
                val_str = ", ".join(str(item) for item in v)
            else:
                val_str = str(v)
            res = res.replace(placeholder, val_str)
    return res


async def read_response_with_limit(
    response: aiohttp.ClientResponse, max_bytes: int
) -> bytes:
    """Read streaming response bytes up to a hard limit."""
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise PermanentOperationError(
                    cause=ValueError("Response size limit exceeded")
                )
        except ValueError:
            pass

    content = bytearray()
    async for chunk in response.content.iter_chunked(4096):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise PermanentOperationError(
                cause=ValueError("Response size limit exceeded")
            )
    return bytes(content)


def _handle_error_status(status_code: int, headers: Mapping[str, str]) -> None:
    """Classify and raise domain exception based on status code."""
    if status_code == 401:
        raise AuthorizationError(cause=ValueError("Authentication failed"))
    if status_code == 403:
        raise PermissionDeniedError(cause=ValueError("Permission denied"))
    if status_code == 429:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        err = RateLimitError(cause=ValueError("Provider rate limit was reached"))
        if retry_after is not None and retry_after.isascii() and retry_after.isdigit():
            object.__setattr__(err, "retry_after", retry_after)
        raise err
    if 500 <= status_code < 600:
        raise TransientOperationError(cause=ValueError("Provider server failed"))

    raise PermanentOperationError(cause=ValueError("Provider rejected the request"))


def _safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return only non-sensitive response metadata owned by this adapter."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in _SAFE_RESPONSE_HEADERS
    }


class DeepSeekProvider(AIProvider):
    """DeepSeek adapter implementing the AIProvider port contract using aiohttp."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize DeepSeek provider adapter."""
        if not api_key or not api_key.strip():
            raise ValidationError(cause=ValueError("DeepSeek API Key cannot be blank"))

        self._api_key = api_key
        self._base_url = base_url or DEEPSEEK_PRODUCTION_BASE_URL
        validate_deepseek_base_url(self._base_url)
        self._session = session

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        """Execute exactly one attempt to the DeepSeek chat completions endpoint."""
        if provider_name != DEEPSEEK_PROVIDER_NAME:
            raise ValidationError(cause=ValueError("Unsupported provider identifier"))
        capabilities = DEEPSEEK_MODEL_CAPABILITIES.get(model_name)
        if capabilities is None:
            raise ValidationError(cause=ValueError("Unsupported model"))
        if task_type not in capabilities:
            raise ValidationError(cause=ValueError("Unsupported task type"))
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValidationError(
                cause=ValueError("Timeout must be positive and finite")
            )

        validate_deepseek_base_url(self._base_url)

        formatted_prompt = _format_prompt(prompt, request_context)

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": formatted_prompt,
                }
            ],
            "stream": False,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        endpoint = f"{self._base_url.rstrip('/')}/chat/completions"
        start_time = time.monotonic()

        async def _do_request(session: aiohttp.ClientSession) -> RawResponseEnvelope:
            try:
                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with session.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                    ssl=self._base_url.startswith("https"),
                ) as response:
                    latency = time.monotonic() - start_time

                    if 300 <= response.status < 400:
                        raise PermanentOperationError(
                            cause=ValueError("Redirects are rejected")
                        )

                    try:
                        content_bytes = await read_response_with_limit(
                            response, DEEPSEEK_MAX_RESPONSE_BYTES
                        )
                    except PermanentOperationError:
                        raise
                    except aiohttp.ClientError:
                        transport_cause = OSError("Provider response transport failed")
                        raise TransientOperationError(
                            cause=transport_cause
                        ) from transport_cause

                    try:
                        raw_content = content_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        encoding_cause = ValueError(
                            "Malformed response envelope: invalid UTF-8"
                        )
                        raise PermanentOperationError(
                            cause=encoding_cause
                        ) from encoding_cause

                    if response.status != 200:
                        _handle_error_status(response.status, response.headers)

                    try:
                        data = json.loads(raw_content)
                    except json.JSONDecodeError:
                        cause_err = ValueError(
                            "Malformed response envelope: invalid JSON"
                        )
                        raise PermanentOperationError(cause=cause_err) from cause_err

                    if not isinstance(data, dict) or "choices" not in data:
                        raise PermanentOperationError(
                            cause=ValueError(
                                "Malformed response envelope: missing choices"
                            )
                        )

                    choices = data["choices"]
                    if not isinstance(choices, list) or not choices:
                        raise PermanentOperationError(
                            cause=ValueError(
                                "Malformed response envelope: empty choices"
                            )
                        )

                    first_choice = choices[0]
                    if (
                        not isinstance(first_choice, dict)
                        or "message" not in first_choice
                    ):
                        raise PermanentOperationError(
                            cause=ValueError(
                                "Malformed response envelope: missing message"
                            )
                        )

                    message = first_choice["message"]
                    if (
                        not isinstance(message, dict)
                        or "content" not in message
                        or message["content"] is None
                    ):
                        raise PermanentOperationError(
                            cause=ValueError(
                                "Malformed response envelope: missing content"
                            )
                        )

                    usage = data.get("usage") or {}
                    input_tokens = usage.get("prompt_tokens")
                    output_tokens = usage.get("completion_tokens")

                    return RawResponseEnvelope(
                        raw_content=raw_content,
                        status_code=response.status,
                        headers=_safe_response_headers(response.headers),
                        latency_seconds=latency,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

            except TimeoutError:
                timeout_cause = TimeoutError("Provider request timed out")
                raise OperationTimeoutError(cause=timeout_cause) from timeout_cause
            except (aiohttp.ClientConnectorError, aiohttp.ClientOSError):
                connection_cause = OSError("Provider connection failed")
                raise TransientOperationError(
                    cause=connection_cause
                ) from connection_cause
            except aiohttp.ClientError:
                http_cause = OSError("Provider HTTP transport failed")
                raise TransientOperationError(cause=http_cause) from http_cause
            except Exception as error:
                if isinstance(
                    error,
                    (
                        OperationTimeoutError,
                        TransientOperationError,
                        AuthorizationError,
                        PermissionDeniedError,
                        RateLimitError,
                        ValidationError,
                        PermanentOperationError,
                    ),
                ):
                    raise
                unexpected_cause = ValueError("Unexpected provider adapter failure")
                raise PermanentOperationError(
                    cause=unexpected_cause
                ) from unexpected_cause

        if self._session:
            return await _do_request(self._session)
        async with aiohttp.ClientSession(trust_env=False) as session:
            return await _do_request(session)


__all__ = (
    "DEEPSEEK_MAX_RESPONSE_BYTES",
    "DEEPSEEK_MODEL_CAPABILITIES",
    "DEEPSEEK_PRODUCTION_BASE_URL",
    "DEEPSEEK_PROVIDER_NAME",
    "DeepSeekProvider",
    "read_response_with_limit",
    "validate_deepseek_base_url",
)
