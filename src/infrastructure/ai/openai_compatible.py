"""Base implementation for OpenAI-compatible chat-completion providers.

Both z.ai and DeepSeek expose OpenAI-compatible ``/chat/completions``
endpoints, so the shared HTTP and prompt logic lives here and concrete
providers only supply their name, base URL, key, and default models.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx

from src.domain.enums import PostCategory
from src.domain.interfaces import AiClassificationResult, DuplicateCheckResult
from src.shared.errors import AiProviderError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_CLASSIFY_SYSTEM_PROMPT = (
    "You are a strict classifier for Telegram posts written in Persian or English. "
    "Classify the post into exactly one category:\n"
    "- general_news: general news content\n"
    "- breaking_news: urgent/breaking news\n"
    "- technology: technology news or articles\n"
    "- vpn: VPN-related discussion, apps, or news without connection configs\n"
    "- vpn_config: posts containing vmess/vless connection configurations\n"
    "- irrelevant: advertising, spam, or anything else\n"
    'Respond ONLY with JSON: {"category": "<one of the values above>"}'
)

_DUPLICATE_SYSTEM_PROMPT = (
    "You compare a NEW Telegram post against EXISTING posts and decide whether the "
    "new post is a duplicate or near-duplicate (same news/content, possibly "
    "reworded). Respond ONLY with JSON: "
    '{"is_duplicate": true|false, "matched_index": <index of matched existing post or null>}'
)

_MAX_COMPARE_TEXT_CHARS = 800
_MAX_HTTP_ATTEMPTS = 3


class OpenAiCompatibleProvider:
    """
    AI provider speaking the OpenAI chat-completions protocol.

    Example:
        provider = OpenAiCompatibleProvider(
            name="zai",
            api_key="...",
            base_url="https://api.z.ai/api/paas/v4",
            default_model="glm-4.6",
        )
        result = await provider.classify_post("خبر فوری ...")
    """

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        default_model: str,
        classification_model: str = "",
        deduplication_model: str = "",
        timeout_seconds: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            name: Provider name used in logs and results (e.g. ``"zai"``).
            api_key: Bearer API key. Never logged.
            base_url: API base URL without the ``/chat/completions`` suffix.
            default_model: Model used when no override is configured.
            classification_model: Optional model override for classification.
            deduplication_model: Optional model override for duplicate checks.
            timeout_seconds: HTTP timeout for every request.
            transport: Optional httpx transport, used by unit tests to
                mock the API without real network access.
        """
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._classification_model = classification_model or default_model
        self._deduplication_model = deduplication_model or default_model
        self._timeout = timeout_seconds
        self._transport = transport

    async def classify_post(self, text: str) -> AiClassificationResult:
        """
        Classify a post via the chat-completions endpoint.

        Args:
            text: Raw post text.

        Returns:
            The classification result.

        Raises:
            AiProviderError: On HTTP failure, timeout, or invalid response.
        """
        content = await self._chat(
            self._classification_model,
            [
                {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": text[:4000]},
            ],
        )
        data = self._extract_json(content)
        try:
            category = PostCategory(str(data["category"]).strip().lower())
        except (KeyError, ValueError) as exc:
            raise AiProviderError(
                f"{self.name}: invalid classification response: {content[:200]}"
            ) from exc
        return AiClassificationResult(category=category, provider=self.name)

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        """
        Check for duplicates via the chat-completions endpoint.

        Args:
            new_text: New post text.
            existing_texts: Recent post texts to compare against.

        Returns:
            The duplicate check result.

        Raises:
            AiProviderError: On HTTP failure, timeout, or invalid response.
        """
        numbered = "\n".join(
            f"[{i}] {t[:_MAX_COMPARE_TEXT_CHARS]}" for i, t in enumerate(existing_texts)
        )
        user_prompt = (
            f"NEW POST:\n{new_text[:_MAX_COMPARE_TEXT_CHARS * 2]}\n\n"
            f"EXISTING POSTS:\n{numbered}"
        )
        content = await self._chat(
            self._deduplication_model,
            [
                {"role": "system", "content": _DUPLICATE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        data = self._extract_json(content)
        if "is_duplicate" not in data:
            raise AiProviderError(
                f"{self.name}: invalid duplicate response: {content[:200]}"
            )
        matched = data.get("matched_index")
        return DuplicateCheckResult(
            is_duplicate=bool(data["is_duplicate"]),
            provider=self.name,
            matched_index=int(matched) if isinstance(matched, int) else None,
        )

    async def _chat(self, model: str, messages: list[dict[str, str]]) -> str:
        """
        Call ``POST {base_url}/chat/completions`` and return the reply text.

        Raises:
            AiProviderError: On any transport or protocol failure.
        """
        url = f"{self._base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "temperature": 0}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_http_error: httpx.HTTPError | None = None
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(1, _MAX_HTTP_ATTEMPTS + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    body = response.json()
                    break
                except httpx.HTTPStatusError as exc:
                    last_http_error = exc
                    if exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise AiProviderError(
                            self._status_error_message(model, exc)
                        ) from exc
                    if attempt == _MAX_HTTP_ATTEMPTS:
                        raise AiProviderError(
                            self._status_error_message(model, exc)
                        ) from exc
                    delay = self._retry_delay_seconds(exc.response, attempt)
                    logger.warning(
                        "AI provider=%s status=%s retrying attempt=%d delay=%ss",
                        self.name,
                        exc.response.status_code,
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                except httpx.HTTPError as exc:
                    last_http_error = exc
                    if attempt == _MAX_HTTP_ATTEMPTS:
                        raise AiProviderError(f"{self.name}: HTTP error: {exc}") from exc
                    delay = attempt * 2
                    logger.warning(
                        "AI provider=%s transport error retrying attempt=%d delay=%ss",
                        self.name,
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                except json.JSONDecodeError as exc:
                    raise AiProviderError(f"{self.name}: non-JSON response") from exc
            else:
                raise AiProviderError(f"{self.name}: HTTP error: {last_http_error}")
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiProviderError(f"{self.name}: unexpected response shape") from exc

    def _status_error_message(self, model: str, exc: httpx.HTTPStatusError) -> str:
        """
        Build a diagnostic message for an HTTP error response.

        Includes the model name and the API's own error body so the root
        cause (wrong model name, invalid key, quota) is visible in logs.

        Args:
            model: Model name sent in the failing request.
            exc: The raised status error.

        Returns:
            A single-line error message safe for logging (no secrets).
        """
        detail = exc.response.text[:300].replace("\n", " ").strip()
        return (
            f"{self.name}: HTTP {exc.response.status_code} for model "
            f"'{model}': {detail}"
        )

    @staticmethod
    def _retry_delay_seconds(response: httpx.Response, attempt: int) -> int:
        """
        Return a conservative retry delay for a retryable HTTP response.

        Args:
            response: HTTP response that failed.
            attempt: Current attempt number, starting at 1.

        Returns:
            Delay in seconds before retrying.
        """
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(1, int(float(retry_after)))
            except ValueError:
                pass
        return attempt * 3

    def _extract_json(self, content: str) -> dict[str, object]:
        """
        Extract the first JSON object from a model reply.

        Handles replies wrapped in Markdown code fences or prose.

        Raises:
            AiProviderError: When no valid JSON object is found.
        """
        match = _JSON_BLOCK_RE.search(content)
        if match is None:
            raise AiProviderError(f"{self.name}: no JSON in response: {content[:200]}")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AiProviderError(f"{self.name}: malformed JSON in response") from exc
        if not isinstance(data, dict):
            raise AiProviderError(f"{self.name}: JSON response is not an object")
        return data
