"""AI orchestration with primary/fallback provider logic.

The primary provider is z.ai and the fallback is DeepSeek, but this
service only knows the :class:`AiProvider` interface, so providers can
be swapped freely via configuration.
"""

from __future__ import annotations

from src.domain.interfaces import (
    AiClassificationResult,
    AiProvider,
    DuplicateCheckResult,
)
from src.shared.errors import (
    AiProviderError,
    DuplicateDetectionError,
    InvalidPostError,
    PostClassificationError,
)
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class AiService:
    """
    Coordinates AI classification and duplicate detection with fallback.

    Calls the primary provider first; when it fails, times out, or
    returns an invalid response, the fallback provider is used
    automatically. Failures of both providers raise explicit errors.

    Example:
        service = AiService(primary=zai, fallback=deepseek)
        result = await service.classify_post("خبر فوری ...")
    """

    def __init__(self, primary: AiProvider, fallback: AiProvider | None = None) -> None:
        """
        Args:
            primary: The primary AI provider (z.ai in production).
            fallback: Optional fallback provider (DeepSeek in production).
        """
        self._primary = primary
        self._fallback = fallback

    async def classify_post(self, text: str) -> AiClassificationResult:
        """
        Classify a post, falling back to the secondary provider on failure.

        Args:
            text: Raw post text.

        Returns:
            The classification result, including the provider name used.

        Raises:
            InvalidPostError: When the text is empty.
            PostClassificationError: When all providers fail.
        """
        if not text.strip():
            raise InvalidPostError("Cannot classify an empty post")
        try:
            return await self._primary.classify_post(text)
        except AiProviderError as exc:
            logger.warning(
                "Classification failed on provider=%s error=%s", self._primary.name, exc
            )
            if self._fallback is None:
                raise PostClassificationError(str(exc)) from exc
        try:
            return await self._fallback.classify_post(text)
        except AiProviderError as exc:
            logger.error(
                "Classification failed on fallback provider=%s error=%s",
                self._fallback.name,
                exc,
            )
            raise PostClassificationError(str(exc)) from exc

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        """
        Run duplicate detection, falling back on provider failure.

        Args:
            new_text: New post text.
            existing_texts: Recent stored post texts to compare against.

        Returns:
            The duplicate check result, including the provider name used.

        Raises:
            InvalidPostError: When the new text is empty.
            DuplicateDetectionError: When all providers fail.
        """
        if not new_text.strip():
            raise InvalidPostError("Cannot check duplicates for an empty post")
        if not existing_texts:
            return DuplicateCheckResult(is_duplicate=False, provider="none")
        try:
            return await self._primary.is_duplicate(new_text, existing_texts)
        except AiProviderError as exc:
            logger.warning(
                "Duplicate check failed on provider=%s error=%s", self._primary.name, exc
            )
            if self._fallback is None:
                raise DuplicateDetectionError(str(exc)) from exc
        try:
            return await self._fallback.is_duplicate(new_text, existing_texts)
        except AiProviderError as exc:
            logger.error(
                "Duplicate check failed on fallback provider=%s error=%s",
                self._fallback.name,
                exc,
            )
            raise DuplicateDetectionError(str(exc)) from exc
