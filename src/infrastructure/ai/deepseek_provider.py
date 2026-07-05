"""Compatibility wrapper for the DeepSeek OpenAI-compatible provider."""

from __future__ import annotations

from src.infrastructure.ai.openai_compatible import OpenAiCompatibleProvider
from src.shared.config import DEFAULT_DEEPSEEK_BASE_URL

_DEFAULT_MODEL = "deepseek-chat"


class DeepSeekProvider(OpenAiCompatibleProvider):
    """
    DeepSeek chat-completions provider.

    Example:
        provider = DeepSeekProvider(api_key="...")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        model: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """
        Args:
            api_key: DeepSeek API key.
            base_url: API base URL.
            model: Optional DeepSeek model override. Defaults to
                deepseek-chat. Must be a model that exists on DeepSeek;
                model names of other providers are not valid here.
            timeout_seconds: HTTP timeout for every request.
        """
        super().__init__(
            name="deepseek",
            api_key=api_key,
            base_url=base_url,
            default_model=_DEFAULT_MODEL,
            classification_model=model,
            deduplication_model=model,
            timeout_seconds=timeout_seconds,
        )
