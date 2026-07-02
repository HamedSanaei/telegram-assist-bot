"""z.ai provider (primary AI provider)."""

from __future__ import annotations

from src.infrastructure.ai.openai_compatible import OpenAiCompatibleProvider
from src.shared.config import DEFAULT_ZAI_BASE_URL

_DEFAULT_MODEL = "glm-4.6"


class ZaiProvider(OpenAiCompatibleProvider):
    """
    z.ai chat-completions provider.

    Example:
        provider = ZaiProvider(api_key="...")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_ZAI_BASE_URL,
        model: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        """
        Args:
            api_key: z.ai API key.
            base_url: API base URL (override for proxies or region endpoints).
            model: Optional z.ai model override (``ai.zai_model``);
                defaults to glm-4.6. Must be a model that exists on z.ai —
                model names of other providers are not valid here.
            timeout_seconds: HTTP timeout for every request.
        """
        super().__init__(
            name="zai",
            api_key=api_key,
            base_url=base_url,
            default_model=_DEFAULT_MODEL,
            classification_model=model,
            deduplication_model=model,
            timeout_seconds=timeout_seconds,
        )
