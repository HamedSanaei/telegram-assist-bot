"""AI orchestration across a configurable provider chain.

The service only knows the :class:`AiProvider` interface. Providers are
assembled from configuration in priority order, so classification,
duplicate detection, and quality scoring can move to the next enabled
provider when the current one is unavailable.
"""

from __future__ import annotations

import time

from src.domain.interfaces import (
    AiClassificationResult,
    AiPostAnalysisResult,
    AiProvider,
    DuplicateCheckResult,
    QualityScoreResult,
)
from src.shared.errors import (
    AiProviderError,
    DuplicateDetectionError,
    InvalidPostError,
    PostClassificationError,
    QualityScoringError,
)
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class AiService:
    """
    Coordinates AI classification, duplicate detection, and quality scoring.

    Calls configured providers in priority order. When one provider fails,
    times out, or returns an invalid response, the next enabled provider is
    used automatically. Failure of the whole chain raises explicit errors.

    Example:
        service = AiService([google_ai_studio, groq, openrouter, deepseek])
        result = await service.classify_post("خبر فوری ...")
    """

    def __init__(
        self,
        primary: AiProvider | list[AiProvider],
        fallback: AiProvider | None = None,
    ) -> None:
        """
        Args:
            primary: A single provider or the full priority-ordered provider
                chain.
            fallback: Optional fallback provider for legacy two-provider
                construction.
        """
        if isinstance(primary, list):
            self._providers = primary
        else:
            self._providers = [primary]
            if fallback is not None:
                self._providers.append(fallback)
        if not self._providers:
            raise ValueError("AiService requires at least one provider")
        self._provider_cooldowns: dict[str, float] = {}
        self._rate_limit_cooldown_seconds = 60.0

    async def classify_post(self, text: str) -> AiClassificationResult:
        """
        Classify a post through the provider chain.

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
        last_error: AiProviderError | None = None
        providers = self._available_providers()
        if not providers:
            raise PostClassificationError("All AI providers are in cooldown")
        for provider in providers:
            try:
                return await provider.classify_post(text)
            except AiProviderError as exc:
                last_error = exc
                self._cool_down_if_exhausted(provider, exc)
                logger.warning(
                    "Classification failed on provider=%s error=%s",
                    provider.name,
                    exc,
                    extra={"event_kind": "ai_error"},
                )
        raise PostClassificationError(str(last_error)) from last_error

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        """
        Run duplicate detection through the provider chain.

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
        last_error: AiProviderError | None = None
        providers = self._available_providers()
        if not providers:
            raise DuplicateDetectionError("All AI providers are in cooldown")
        for provider in providers:
            try:
                return await provider.is_duplicate(new_text, existing_texts)
            except AiProviderError as exc:
                last_error = exc
                self._cool_down_if_exhausted(provider, exc)
                logger.warning(
                    "Duplicate check failed on provider=%s error=%s",
                    provider.name,
                    exc,
                    extra={"event_kind": "ai_error"},
                )
        raise DuplicateDetectionError(str(last_error)) from last_error

    async def analyze_post(
        self, new_text: str, existing_texts: list[str]
    ) -> AiPostAnalysisResult:
        """
        Classify and duplicate-check one post through the provider chain.

        Args:
            new_text: New post text.
            existing_texts: Recent stored post texts.

        Returns:
            Combined analysis result from the first healthy provider.

        Raises:
            InvalidPostError: When the new text is empty.
            PostClassificationError: When all providers fail.
        """
        if not new_text.strip():
            raise InvalidPostError("Cannot analyze an empty post")
        last_error: AiProviderError | None = None
        providers = self._available_providers()
        if not providers:
            raise PostClassificationError("All AI providers are in cooldown")
        for provider in providers:
            try:
                return await provider.analyze_post(new_text, existing_texts)
            except AiProviderError as exc:
                last_error = exc
                self._cool_down_if_exhausted(provider, exc)
                logger.warning(
                    "Post analysis failed on provider=%s error=%s",
                    provider.name,
                    exc,
                    extra={"event_kind": "ai_error"},
                )
        raise PostClassificationError(str(last_error)) from last_error

    async def score_post(
        self,
        text: str,
        category: object | None,
        metrics: dict[str, object],
    ) -> QualityScoreResult:
        """
        Score a post's repost value from 0 to 100 using the AI chain.

        Args:
            text: Raw post text. May be empty for media-only posts.
            category: Classification category, if available.
            metrics: Source engagement and timing metrics.

        Returns:
            Quality score result from the first healthy provider.

        Raises:
            QualityScoringError: When all providers fail.
        """
        last_error: AiProviderError | None = None
        providers = self._available_providers()
        if not providers:
            raise QualityScoringError("All AI providers are in cooldown")
        for provider in providers:
            try:
                return await provider.score_post(text, category, metrics)
            except AiProviderError as exc:
                last_error = exc
                self._cool_down_if_exhausted(provider, exc)
                logger.warning(
                    "Quality scoring failed on provider=%s error=%s",
                    provider.name,
                    exc,
                    extra={"event_kind": "ai_error"},
                )
        raise QualityScoringError(str(last_error)) from last_error

    def _available_providers(self) -> list[AiProvider]:
        """
        Return providers that are not in temporary rate-limit cooldown.

        Side effects:
            Logs skipped providers at debug level so backfill behavior can be
            diagnosed without flooding normal INFO logs.
        """
        now = time.monotonic()
        available: list[AiProvider] = []
        for provider in self._providers:
            until = self._provider_cooldowns.get(provider.name, 0)
            if until > now:
                logger.debug(
                    "Skipping AI provider=%s cooldown_remaining=%.1fs",
                    provider.name,
                    until - now,
                )
                continue
            available.append(provider)
        return available

    def _cool_down_if_exhausted(
        self, provider: AiProvider, exc: AiProviderError
    ) -> None:
        """
        Temporarily skip a provider after quota, rate, or temporary failure.

        Args:
            provider: Provider that raised the error.
            exc: Provider error.

        Side effects:
            Records a short in-memory cooldown so bursts of backfilled posts do
            not keep retrying the same exhausted or temporarily broken provider.
        """
        message = str(exc).lower()
        cooldown_markers = (
            "http 429",
            "http 402",
            "http 403",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "timeout",
            "timed out",
            "quota",
            "rate limit",
        )
        if not any(marker in message for marker in cooldown_markers):
            return
        until = time.monotonic() + self._rate_limit_cooldown_seconds
        self._provider_cooldowns[provider.name] = until
        logger.warning(
            "AI provider=%s temporarily unavailable; cooling down for %.0fs",
            provider.name,
            self._rate_limit_cooldown_seconds,
            extra={"event_kind": "ai_error"},
        )
