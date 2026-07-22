"""Application use case for fetching and versioning advertisement source posts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from telegram_assist_bot.application.ports import (
    AdvertisementRepository,
    AdvertisementSourceGroupDTO,
    AdvertisementSourceMessageDTO,
    AdvertisementSourceNotFoundError,
    AdvertisementSourcePermissionError,
    AdvertisementSourceTransientError,
    Clock,
    MediaSource,
    MediaStorage,
    TelegramAdvertisementSourceGateway,
    TelegramMediaReference,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceFetchPolicy,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
    FetchAdvertisementSourceOutcomeKind,
    compute_canonical_content_hash,
)
from telegram_assist_bot.domain.advertisements import (
    AdvertisementCampaign,
    SourceCachePolicy,
    SourceUnavailablePolicy,
)
from telegram_assist_bot.domain.media import MediaIdentity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.domain.posts import TelegramEntity


@dataclass(frozen=True, slots=True)
class FetchAdvertisementSourceResult:
    """Outcome descriptor of an advertisement source fetch/refresh attempt."""

    kind: FetchAdvertisementSourceOutcomeKind
    snapshot: AdvertisementSourceSnapshot | None
    error_reason: str | None = None


class FetchAdvertisementSource:
    """Execute advertisement source post resolution, versioning, and caching."""

    def __init__(
        self,
        *,
        repository: AdvertisementRepository,
        source_gateway: TelegramAdvertisementSourceGateway,
        clock: Clock,
        fetch_policy: AdvertisementSourceFetchPolicy,
        media_storage: MediaStorage | None = None,
        media_source: MediaSource | None = None,
        maximum_media_bytes: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initialize use case with required ports."""
        self._repository = repository
        self._gateway = source_gateway
        self._clock = clock
        self._fetch_policy = fetch_policy
        self._media_storage = media_storage
        self._media_source = media_source
        self._maximum_media_bytes = maximum_media_bytes
        self._sleep = sleep

    async def execute(
        self,
        campaign: AdvertisementCampaign,
        override_time: datetime | None = None,
    ) -> FetchAdvertisementSourceResult:
        """Resolve or refresh the advertisement source post for a campaign."""
        if not campaign.enabled:
            return FetchAdvertisementSourceResult(
                kind=FetchAdvertisementSourceOutcomeKind.DISABLED,
                snapshot=None,
            )

        source_id = AdvertisementSourceIdentity.create(
            campaign_id=campaign.campaign_id,
            source_channel_username=campaign.source_post.channel_username,
            source_message_id=campaign.source_post.message_id,
        )

        now = (
            override_time.astimezone(UTC)
            if override_time is not None
            else self._clock.utc_now().astimezone(UTC)
        )

        current = await self._repository.get_current_snapshot(
            campaign.campaign_id,
            source_id.source_identity_fingerprint,
        )

        # Evaluate Cache Policy
        if (
            campaign.source_cache_policy == SourceCachePolicy.CACHED
            and current is not None
        ):
            return FetchAdvertisementSourceResult(
                kind=FetchAdvertisementSourceOutcomeKind.CACHE_HIT,
                snapshot=current,
            )

        if (
            campaign.source_cache_policy == SourceCachePolicy.PERIODIC_REFRESH
            and current is not None
            and campaign.refresh_interval_seconds is not None
        ):
            interval = timedelta(seconds=campaign.refresh_interval_seconds)
            if now < current.last_successful_fetch_at + interval:
                return FetchAdvertisementSourceResult(
                    kind=FetchAdvertisementSourceOutcomeKind.CACHE_HIT,
                    snapshot=current,
                )

        # Perform Telegram Source Fetch
        try:
            dto = await self._fetch_with_retry(source_id)
        except AdvertisementSourceNotFoundError as err:
            return await self._handle_fetch_failure(
                campaign,
                source_id,
                current,
                now,
                "source_deleted",
                str(err),
            )
        except AdvertisementSourcePermissionError as err:
            return await self._handle_fetch_failure(
                campaign,
                source_id,
                current,
                now,
                "permanently_unavailable",
                str(err),
            )
        except AdvertisementSourceTransientError as err:
            return await self._handle_fetch_failure(
                campaign,
                source_id,
                current,
                now,
                "temporarily_unavailable",
                str(err),
            )

        # Extract content & media DTOs
        text: str | None = None
        caption: str | None = None
        text_entities: tuple[TelegramEntity, ...] = ()
        caption_entities: tuple[TelegramEntity, ...] = ()
        media_group_id: str | None = None
        media_references: tuple[AdvertisementMediaReference, ...] = ()
        source_published_at: datetime
        source_edited_at: datetime | None = None
        album_member_ids: tuple[int, ...] = ()

        if isinstance(dto, AdvertisementSourceGroupDTO):
            media_group_id = dto.media_group_id
            caption = dto.canonical_caption
            caption_entities = dto.canonical_caption_entities
            source_published_at = dto.members[0].source_published_at
            source_edited_at = dto.members[0].source_edited_at
            album_member_ids = tuple(m.source_message_id for m in dto.members)
            all_media: list[TelegramMediaReference] = []
            for member in dto.members:
                for reference in member.media:
                    all_media.append(
                        TelegramMediaReference(
                            media_type=reference.media_type,
                            item_index=len(all_media),
                            size_bytes=reference.size_bytes,
                            mime_type=reference.mime_type,
                            original_filename=reference.original_filename,
                            opaque_reference=reference.opaque_reference,
                            media_group_id=reference.media_group_id,
                        )
                    )
            media_references = await self._cache_media(tuple(all_media))
        else:
            text = dto.text
            caption = dto.caption
            text_entities = dto.text_entities
            caption_entities = dto.caption_entities
            media_group_id = dto.media_group_id
            media_references = await self._cache_media(dto.media)
            source_published_at = dto.source_published_at
            source_edited_at = dto.source_edited_at

        # Compute content hash
        content_hash = compute_canonical_content_hash(
            text=text,
            caption=caption,
            text_entities=text_entities,
            caption_entities=caption_entities,
            media_group_id=media_group_id,
            media_references=media_references,
            album_member_identities=album_member_ids,
        )

        # Check if content is unchanged
        if current is not None and current.content_hash == content_hash:
            await self._repository.record_unchanged_check(
                campaign.campaign_id,
                source_id.source_identity_fingerprint,
                now,
            )
            updated_snapshot = AdvertisementSourceSnapshot(
                snapshot_id=current.snapshot_id,
                campaign_id=current.campaign_id,
                source_identity=current.source_identity,
                snapshot_version=current.snapshot_version,
                snapshot_contract_version=current.snapshot_contract_version,
                content_hash=current.content_hash,
                text=current.text,
                caption=current.caption,
                text_entities=current.text_entities,
                caption_entities=current.caption_entities,
                media_group_id=current.media_group_id,
                media_references=current.media_references,
                source_published_at=current.source_published_at,
                source_edited_at=current.source_edited_at,
                fetched_at=now,
                last_successful_fetch_at=now,
                is_current=True,
                expires_at=current.expires_at,
                is_stale=False,
                stale_reason=None,
            )
            return FetchAdvertisementSourceResult(
                kind=FetchAdvertisementSourceOutcomeKind.REFRESH_UNCHANGED,
                snapshot=updated_snapshot,
            )

        # Content is initial or updated
        new_version = (current.snapshot_version + 1) if current is not None else 1
        new_snapshot = AdvertisementSourceSnapshot(
            snapshot_id=uuid4().hex,
            campaign_id=campaign.campaign_id,
            source_identity=source_id,
            snapshot_version=new_version,
            snapshot_contract_version="1.0.0",
            content_hash=content_hash,
            text=text,
            caption=caption,
            text_entities=text_entities,
            caption_entities=caption_entities,
            media_group_id=media_group_id,
            media_references=media_references,
            source_published_at=source_published_at,
            source_edited_at=source_edited_at,
            fetched_at=now,
            last_successful_fetch_at=now,
            is_current=True,
        )

        if current is None:
            saved = await self._repository.save_initial_snapshot(new_snapshot)
            return FetchAdvertisementSourceResult(
                kind=FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL,
                snapshot=saved,
            )

        if campaign.snapshot_retention_days is None:
            raise ValueError("enabled campaigns require snapshot_retention_days")
        saved = await self._repository.commit_changed_snapshot(
            new_snapshot,
            current.snapshot_version,
            campaign.snapshot_retention_days,
        )
        return FetchAdvertisementSourceResult(
            kind=FetchAdvertisementSourceOutcomeKind.REFRESH_UPDATED,
            snapshot=saved,
        )

    async def _fetch_with_retry(
        self, source_id: AdvertisementSourceIdentity
    ) -> AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO:
        """Fetch with the explicit bounded timeout and retry policy."""
        for attempt in range(1, self._fetch_policy.max_attempts + 1):
            try:
                async with asyncio.timeout(self._fetch_policy.timeout_seconds):
                    return await self._gateway.fetch_advertisement_post(
                        source_id.source_channel_username,
                        source_id.source_message_id,
                    )
            except TimeoutError as err:
                failure: Exception = AdvertisementSourceTransientError(
                    "Telegram source fetch timed out."
                )
                failure.__cause__ = err
            except AdvertisementSourceTransientError as err:
                failure = err
            if attempt >= self._fetch_policy.max_attempts:
                raise failure
            delay = self._fetch_policy.initial_backoff_seconds * (2 ** (attempt - 1))
            if delay:
                await self._sleep(float(delay))
        raise AssertionError("bounded fetch loop did not return or raise")

    async def _cache_media(
        self, references: tuple[TelegramMediaReference, ...]
    ) -> tuple[AdvertisementMediaReference, ...]:
        """Persist source media content-addressably while preserving album order."""
        if not references:
            return ()
        if (
            self._media_storage is None
            or self._media_source is None
            or self._maximum_media_bytes is None
            or self._maximum_media_bytes <= 0
        ):
            raise ValueError(
                "advertisement media caching requires explicit media ports and limit"
            )
        cached: list[AdvertisementMediaReference] = []
        for reference in sorted(references, key=lambda item: item.item_index):
            try:
                channel_text, message_text, item_text = (
                    reference.opaque_reference.split(":", 2)
                )
                identity = MediaIdentity(
                    source_channel_id=int(channel_text),
                    source_message_id=int(message_text),
                    item_index=int(item_text),
                )
            except (TypeError, ValueError):
                raise ValueError("advertisement media reference is invalid") from None
            stream = await self._media_source.open(reference.opaque_reference)
            storage_path, size_bytes, _digest = await self._media_storage.store(
                identity,
                stream,
                maximum_bytes=self._maximum_media_bytes,
            )
            cached.append(
                AdvertisementMediaReference(
                    media_type=reference.media_type,
                    item_index=reference.item_index,
                    size_bytes=size_bytes,
                    mime_type=reference.mime_type,
                    original_filename=reference.original_filename,
                    storage_path=storage_path,
                    media_group_id=reference.media_group_id,
                )
            )
        return tuple(cached)

    async def _handle_fetch_failure(
        self,
        campaign: AdvertisementCampaign,
        source_id: AdvertisementSourceIdentity,
        current: AdvertisementSourceSnapshot | None,
        now: datetime,
        reason_code: str,
        safe_message: str,
    ) -> FetchAdvertisementSourceResult:
        await self._repository.record_failed_check(
            campaign.campaign_id,
            source_id.source_identity_fingerprint,
            now,
            reason_code,
        )

        if (
            campaign.source_unavailable_policy
            == SourceUnavailablePolicy.USE_LAST_VALID_SNAPSHOT
            and current is not None
        ):
            stale_snapshot = AdvertisementSourceSnapshot(
                snapshot_id=current.snapshot_id,
                campaign_id=current.campaign_id,
                source_identity=current.source_identity,
                snapshot_version=current.snapshot_version,
                snapshot_contract_version=current.snapshot_contract_version,
                content_hash=current.content_hash,
                text=current.text,
                caption=current.caption,
                text_entities=current.text_entities,
                caption_entities=current.caption_entities,
                media_group_id=current.media_group_id,
                media_references=current.media_references,
                source_published_at=current.source_published_at,
                source_edited_at=current.source_edited_at,
                fetched_at=current.fetched_at,
                last_successful_fetch_at=current.last_successful_fetch_at,
                is_current=current.is_current,
                expires_at=current.expires_at,
                is_stale=True,
                stale_reason=reason_code,
            )
            return FetchAdvertisementSourceResult(
                kind=FetchAdvertisementSourceOutcomeKind.STALE_FALLBACK,
                snapshot=stale_snapshot,
                error_reason=reason_code,
            )

        return FetchAdvertisementSourceResult(
            kind=FetchAdvertisementSourceOutcomeKind.UNAVAILABLE,
            snapshot=None,
            error_reason=reason_code,
        )
