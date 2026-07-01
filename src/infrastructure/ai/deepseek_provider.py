"""DeepSeek provider (fallback AI provider)."""

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
        classification_model: str = "",
        deduplication_model: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """
        Args:
            api_key: DeepSeek API key.
            base_url: API base URL.
            classification_model: Optional model override; defaults to deepseek-chat.
            deduplication_model: Optional model override; defaults to deepseek-chat.
            timeout_seconds: HTTP timeout for every request.
        """
        super().__init__(
            name="deepseek",
            api_key=api_key,
            base_url=base_url,
            default_model=_DEFAULT_MODEL,
            classification_model=classification_model,
            deduplication_model=deduplication_model,
            timeout_seconds=timeout_seconds,
        )
