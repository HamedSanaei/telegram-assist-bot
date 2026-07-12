"""Download one media item through bounded provider-neutral ports."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import (
    ContentPreparationRepository,
    MediaDownloadSpec,
    MediaRateLimitError,
    MediaSource,
    MediaStorage,
    MediaTransientError,
)
from telegram_assist_bot.domain.media import StoredMedia

if TYPE_CHECKING:
    from telegram_assist_bot.shared.retry.executor import AsyncSleeper

_UNSAFE_FILENAME = re.compile(r"[\\/\x00-\x1f<>:\"|?*]+")


def sanitize_filename(value: str | None) -> str | None:
    """Keep a bounded display-only filename without path semantics."""
    if value is None:
        return None
    cleaned = _UNSAFE_FILENAME.sub("_", value).strip(" .")
    return cleaned[:255] or None


class DownloadPostMedia:
    """Coordinate bounded, idempotent streaming and metadata persistence."""

    def __init__(
        self,
        source: MediaSource,
        storage: MediaStorage,
        repository: ContentPreparationRepository,
        *,
        maximum_bytes: int,
        timeout_seconds: float,
        maximum_attempts: int = 3,
        maximum_rate_limit_delay_seconds: float = 60,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        """Initialize bounded download policy and external ports."""
        if (
            maximum_bytes <= 0
            or timeout_seconds <= 0
            or not 1 <= maximum_attempts <= 10
            or maximum_rate_limit_delay_seconds < 0
        ):
            raise ValueError("Media download bounds are invalid.")
        self._source = source
        self._storage = storage
        self._repository = repository
        self._maximum_bytes = maximum_bytes
        self._timeout = timeout_seconds
        self._attempts = maximum_attempts
        self._maximum_rate_limit_delay = maximum_rate_limit_delay_seconds
        self._sleeper = sleeper

    async def execute(self, spec: MediaDownloadSpec) -> StoredMedia:
        """Return an existing healthy item or atomically download it once."""
        existing = await self._repository.get_media(spec.identity)
        if existing is not None and await self._storage.exists(existing.storage_path):
            await self._ensure_preview(existing)
            return existing
        last_error: BaseException | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                async with asyncio.timeout(self._timeout):
                    stream = await self._source.open(spec.opaque_reference)
                    path, size, content_hash = await self._storage.store(
                        spec.identity, stream, maximum_bytes=self._maximum_bytes
                    )
                media = StoredMedia(
                    identity=spec.identity,
                    media_type=spec.media_type,
                    content_hash=content_hash,
                    size_bytes=size,
                    mime_type=spec.mime_type,
                    original_filename=sanitize_filename(spec.original_filename),
                    storage_path=path,
                    expires_at=spec.expires_at,
                )
                stored = await self._repository.save_media_if_absent(media)
                await self._ensure_preview(stored)
                return stored
            except asyncio.CancelledError:
                raise
            except (TimeoutError, MediaTransientError) as error:
                last_error = error
                if attempt == self._attempts:
                    raise
                if isinstance(error, MediaRateLimitError):
                    await self._sleeper(
                        min(
                            float(error.retry_after_seconds),
                            self._maximum_rate_limit_delay,
                        )
                    )
        if last_error is None:
            raise RuntimeError("Media retry loop ended unexpectedly.")
        raise last_error

    async def _ensure_preview(self, media: StoredMedia) -> None:
        preview = getattr(self._storage, "ensure_preview", None)
        if preview is not None:
            await preview(media)
