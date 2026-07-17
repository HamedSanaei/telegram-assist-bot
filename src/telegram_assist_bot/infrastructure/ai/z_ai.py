"""Z.AI adapter implementing the AIProvider application port using aiohttp."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
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
    from pydantic import BaseModel


def validate_base_url(url_str: str) -> None:
    """Validate that the base URL has an approved host and secure scheme."""
    try:
        parsed = urlparse(url_str)
    except Exception as e:
        raise ValidationError(cause=ValueError("Invalid base URL")) from e

    if parsed.scheme != "https":
        if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
            pass
        else:
            raise ValidationError(cause=ValueError("Only HTTPS scheme is allowed"))

    host = parsed.hostname
    if not host or host not in {"api.z.ai", "localhost", "127.0.0.1"}:
        raise ValidationError(cause=ValueError(f"Unapproved host: {host}"))


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


def _handle_error_status(
    status_code: int, raw_content: str, headers: Mapping[str, str]
) -> None:
    """Classify and raise domain exception based on status code."""
    if status_code == 401:
        raise AuthorizationError(cause=ValueError("Authentication failed"))
    if status_code == 403:
        raise PermissionDeniedError(cause=ValueError("Permission denied"))
    if status_code == 429:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        err = RateLimitError(
            cause=ValueError(f"Rate limited. Retry after: {retry_after}")
        )
        if retry_after:
            object.__setattr__(err, "retry_after", retry_after)
        raise err
    if 500 <= status_code < 600:
        raise TransientOperationError(cause=ValueError(f"Server error: {status_code}"))

    # Check for unavailable model error messages
    if "model_not_found" in raw_content or "model" in raw_content:
        raise PermanentOperationError(
            cause=ValueError(f"Model unavailable or unsupported: {status_code}")
        )

    raise PermanentOperationError(
        cause=ValueError(f"Request failed with status code {status_code}")
    )


class ZAIProvider(AIProvider):
    """Z.AI adapter implementing the AIProvider port contract using aiohttp."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize Z.AI provider adapter."""
        if not api_key or not api_key.strip():
            raise ValidationError(cause=ValueError("Z.AI API Key cannot be blank"))

        self._api_key = api_key
        self._base_url = base_url or "https://api.z.ai/api/paas/v4"
        validate_base_url(self._base_url)
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
        """Execute exactly one attempt to the Z.AI chat completions endpoint."""
        if provider_name != "z-ai":
            raise ValidationError(
                cause=ValueError(f"Unsupported provider: {provider_name}")
            )
        if model_name != "glm-4.7-flash":
            raise ValidationError(cause=ValueError(f"Unsupported model: {model_name}"))
        if task_type not in {
            AITaskType.ADVERTISEMENT_DETECTION,
            AITaskType.SEMANTIC_DUPLICATE,
            AITaskType.CATEGORIZATION,
            AITaskType.SCORING,
        }:
            raise ValidationError(
                cause=ValueError(f"Unsupported task type: {task_type}")
            )

        validate_base_url(self._base_url)

        formatted_prompt = _format_prompt(prompt, request_context)

        payload = {
            "model": "glm-4.7-flash",
            "messages": [
                {
                    "role": "user",
                    "content": formatted_prompt,
                }
            ],
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        endpoint = f"{self._base_url.rstrip('/')}/chat/completions"
        max_response_bytes = 1024 * 1024
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
                            response, max_response_bytes
                        )
                    except Exception as e:
                        if isinstance(e, PermanentOperationError):
                            raise
                        raise PermanentOperationError(cause=e) from e

                    raw_content = content_bytes.decode("utf-8")

                    if response.status != 200:
                        _handle_error_status(
                            response.status, raw_content, response.headers
                        )

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

                    envelope_headers = dict(response.headers)
                    for key in list(envelope_headers.keys()):
                        if key.lower() == "authorization":
                            envelope_headers[key] = "[REDACTED]"

                    return RawResponseEnvelope(
                        raw_content=raw_content,
                        status_code=response.status,
                        headers=envelope_headers,
                        latency_seconds=latency,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

            except TimeoutError as e:
                raise OperationTimeoutError(cause=e) from e
            except (aiohttp.ClientConnectorError, aiohttp.ClientOSError) as e:
                raise TransientOperationError(cause=e) from e
            except aiohttp.ClientError as e:
                raise TransientOperationError(cause=e) from e
            except Exception as e:
                if isinstance(
                    e,
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
                raise PermanentOperationError(cause=e) from e

        if self._session:
            return await _do_request(self._session)
        async with aiohttp.ClientSession(trust_env=False) as session:
            return await _do_request(session)
