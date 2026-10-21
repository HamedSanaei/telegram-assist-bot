"""Download one media item through bounded provider-neutral ports."""

from __future__ import annotations

import asyncio
import math
import random
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from telegram_assist_bot.application.ports import (
    Clock,
    ContentPreparationRepository,
    MediaDownloadSpec,
    MediaRateLimitError,
    MediaSource,
    MediaStorage,
    MediaTransientError,
)
from telegram_assist_bot.application.ports.media import (
    TERMINAL_MEDIA_FAILURE_CATEGORIES,
    MediaFailure,
    MediaPermanentError,
    MediaTooLargeError,
)
from telegram_assist_bot.domain.media import StoredMedia

if TYPE_CHECKING:
    from telegram_assist_bot.shared.retry.executor import AsyncSleeper

_UNSAFE_FILENAME = re.compile(r"[\\/\x00-\x1f<>:\"|?*]+")


def calculate_media_expiration(
    registered_at: datetime, retention: timedelta
) -> datetime:
    """Return the exact UTC media-retention boundary."""
    if (
        registered_at.tzinfo is None
        or registered_at.utcoffset() is None
        or retention <= timedelta(0)
        or retention > timedelta(days=3650)
    ):
        raise ValueError("Media retention boundary is invalid.")
    return registered_at.astimezone(UTC) + retention


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
        clock: Clock | None = None,
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
        self._now = clock.utc_now if clock is not None else lambda: datetime.now(UTC)
        self.retry_count = 0
        self.reused_count = 0
        self._operation_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )

    async def execute(self, spec: MediaDownloadSpec) -> StoredMedia:
        """Coalesce concurrent callers without caching mutable completion state."""
        key = spec.identity.key
        lock = self._operation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._operation_locks[key] = lock
        async with lock:
            return await self._execute(spec)

    async def _execute(self, spec: MediaDownloadSpec) -> StoredMedia:
        """Resume one operation; durable rejections require authorized reset."""
        failure = await self._repository.get_media_failure(spec.identity)
        now = self._now()
        if failure is not None:
            if failure.category in TERMINAL_MEDIA_FAILURE_CATEGORIES:
                error_type = (
                    MediaTooLargeError
                    if failure.category == "oversized"
                    else MediaPermanentError
                )
                raise error_type(
                    "Media was durably rejected; explicit retry is required."
                )
            if failure.retry_at is not None and failure.retry_at > now:
                raise MediaRateLimitError(
                    math.ceil((failure.retry_at - now).total_seconds())
                )
        existing = await self._repository.get_media(spec.identity)
        if existing is not None and await self._storage.exists(existing.storage_path):
            self.reused_count += 1
            await self._ensure_preview(existing)
            if failure is not None:
                await self._repository.clear_media_failure(spec.identity)
            return existing
        if spec.size_bytes is not None and spec.size_bytes > self._maximum_bytes:
            await self._record_failure(spec, "oversized", attempts=0)
            raise MediaTooLargeError("Media exceeds the configured size limit.")
        attempts_used = 0 if failure is None else failure.attempts
        if attempts_used >= self._attempts:
            await self._record_failure(
                spec,
                "retry_exhausted",
                attempts=attempts_used,
                last_failure_category=failure.category if failure is not None else None,
            )
            raise MediaPermanentError("Media retry budget is exhausted.")
        pending_media: StoredMedia | None = None
        for attempt in range(attempts_used + 1, self._attempts + 1):
            try:
                # Consume the durable budget before an external side effect, so a
                # crash cannot renew attempts when this operation resumes.
                await self._record_failure(
                    spec, "attempting", attempts=attempt, retry_delay=self._timeout
                )
                if pending_media is None:
                    async with asyncio.timeout(self._timeout):
                        stream = await self._source.open(spec.opaque_reference)
                        try:
                            path, size, content_hash = await self._storage.store(
                                spec.identity, stream, maximum_bytes=self._maximum_bytes
                            )
                        finally:
                            primary_error = sys.exception()
                            close = getattr(stream, "aclose", None)
                            if close is not None:
                                try:
                                    await close()
                                except Exception as cleanup_error:
                                    if primary_error is not None:
                                        primary_error.add_note(
                                            "Media stream cleanup also failed."
                                        )
                                        raise primary_error from cleanup_error
                                    if isinstance(
                                        cleanup_error,
                                        (MediaPermanentError, MediaTransientError),
                                    ):
                                        raise
                                    raise MediaPermanentError(
                                        "Media stream cleanup failed."
                                    ) from cleanup_error
                    pending_media = StoredMedia(
                        identity=spec.identity,
                        media_type=spec.media_type,
                        content_hash=content_hash,
                        size_bytes=size,
                        mime_type=spec.mime_type,
                        original_filename=sanitize_filename(spec.original_filename),
                        storage_path=path,
                        expires_at=spec.expires_at,
                        post_id=None if spec.post_id is None else spec.post_id.value,
                    )
                stored = await self._repository.save_media_if_absent(pending_media)
                await self._ensure_preview(stored)
                await self._repository.clear_media_failure(spec.identity)
                return stored
            except asyncio.CancelledError:
                raise
            except MediaPermanentError as error:
                await self._record_failure(
                    spec,
                    "oversized"
                    if isinstance(error, MediaTooLargeError)
                    else "permanent",
                    attempts=attempt,
                    observed_size=(
                        error.size_bytes
                        if isinstance(error, MediaTooLargeError)
                        else None
                    ),
                )
                raise
            except (TimeoutError, MediaTransientError) as error:
                delay = (
                    float(error.retry_after_seconds)
                    if isinstance(error, MediaRateLimitError)
                    else min(2 ** (attempt - 1), 30) + random.uniform(0, 0.25)  # noqa: S311 - Non-security retry jitter.
                )
                category = getattr(error, "error_category", "timeout")
                exhausted = attempt == self._attempts
                await self._record_failure(
                    spec,
                    "retry_exhausted" if exhausted else category,
                    attempts=attempt,
                    retry_delay=(
                        delay
                        if not exhausted or isinstance(error, MediaRateLimitError)
                        else None
                    ),
                    last_failure_category=category,
                )
                if attempt == self._attempts or (
                    isinstance(error, MediaRateLimitError)
                    and delay > self._maximum_rate_limit_delay
                ):
                    raise
                self.retry_count += 1
                await self._sleeper(delay)
        raise RuntimeError("Media retry loop ended unexpectedly.")

    async def _record_failure(
        self,
        spec: MediaDownloadSpec,
        category: str,
        *,
        attempts: int,
        retry_delay: float | None = None,
        observed_size: int | None = None,
        last_failure_category: str | None = None,
    ) -> None:
        now = self._now()
        await self._repository.record_media_failure(
            spec,
            MediaFailure(
                category=category,
                attempts=attempts,
                failed_at=now,
                retry_at=(
                    None
                    if retry_delay is None
                    else now + timedelta(seconds=retry_delay)
                ),
                maximum_bytes=self._maximum_bytes,
                size_bytes=spec.size_bytes if observed_size is None else observed_size,
                last_failure_category=last_failure_category,
            ),
        )

    async def _ensure_preview(self, media: StoredMedia) -> None:
        preview = getattr(self._storage, "ensure_preview", None)
        if preview is not None:
            await preview(media)


__all__ = ("DownloadPostMedia", "calculate_media_expiration", "sanitize_filename")
