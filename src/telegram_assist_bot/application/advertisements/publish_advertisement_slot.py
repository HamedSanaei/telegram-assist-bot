"""Claim and publish one due advertisement slot idempotently."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import PublicationMedia, PublicationPayload
from telegram_assist_bot.application.publication import (
    PublishImmediately,
    PublishRequest,
    PublishStatus,
)
from telegram_assist_bot.domain.publication import PublicationState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection

    from telegram_assist_bot.application.ports import (
        AdvertisementRepository,
        AdvertisementSlotRepository,
        PublicationRepository,
        TelegramPublisherGateway,
    )
    from telegram_assist_bot.domain.advertisement_slot import AdvertisementSlot
    from telegram_assist_bot.domain.advertisement_source import (
        AdvertisementSourceSnapshot,
    )
    from telegram_assist_bot.domain.publication import Publication


class PublishAdvertisementSlotStatus(StrEnum):
    """Stable result of one advertisement worker iteration."""

    IDLE = "idle"
    COMPLETED = "completed"
    ALREADY_PUBLISHED = "already_published"
    DEFERRED = "deferred"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class AdvertisementPublicationContext:
    """Trusted server-side authorization and Telegram account state."""

    allowed_destination_ids: Collection[int]
    session_valid: bool
    account_premium: bool


class PublishAdvertisementSlot:
    """Execute one durable due slot through the existing T029 publisher."""

    def __init__(
        self,
        slots: AdvertisementSlotRepository,
        snapshots: AdvertisementRepository,
        publications: PublicationRepository,
        publisher: TelegramPublisherGateway,
        *,
        owner: str,
        clock: Callable[[], datetime],
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_seconds: float,
        lease_seconds: float,
        retry_initial_delay_seconds: float,
        retry_maximum_delay_seconds: float,
        busy_retry_delay_seconds: float,
    ) -> None:
        """Validate and store explicit bounded publication policies."""
        if (
            not owner
            or owner.isspace()
            or timeout_seconds <= 0
            or lease_seconds < timeout_seconds
            or retry_initial_delay_seconds <= 0
            or retry_maximum_delay_seconds < retry_initial_delay_seconds
            or busy_retry_delay_seconds <= 0
        ):
            raise ValueError("advertisement publication configuration is invalid")
        self._slots = slots
        self._snapshots = snapshots
        self._publications = publications
        self._publisher = publisher
        self._owner = owner
        self._clock = clock
        self._sleeper = sleeper
        self._timeout_seconds = timeout_seconds
        self._lease_seconds = lease_seconds
        self._retry_initial_delay_seconds = retry_initial_delay_seconds
        self._retry_maximum_delay_seconds = retry_maximum_delay_seconds
        self._busy_retry_delay_seconds = busy_retry_delay_seconds

    async def execute_once(
        self, context: AdvertisementPublicationContext
    ) -> PublishAdvertisementSlotStatus:
        """Claim at most one due slot and persist one typed terminal/deferred result."""
        now = self._now()
        slot = await self._slots.claim_due_slot(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if slot is None:
            return PublishAdvertisementSlotStatus.IDLE
        snapshot = await self._snapshots.get_snapshot_by_id(slot.source_snapshot_id)
        if (
            snapshot is None
            or snapshot.snapshot_version != slot.source_snapshot_version
        ):
            return await self._fail_pre_send(slot, "source_snapshot_unavailable")
        if slot.destination_id not in context.allowed_destination_ids:
            return await self._fail_pre_send(slot, "destination_not_authorized")

        payload = self._payload(snapshot, slot.destination_id)
        publish = PublishImmediately(
            self._publications,
            self._publisher,
            clock=self._clock,
            sleeper=self._sleeper,
            timeout_seconds=self._timeout_seconds,
            lease_seconds=self._lease_seconds,
            max_attempts=slot.max_retries + 1,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            maximum_delay_seconds=self._retry_maximum_delay_seconds,
            jitter_ratio=0,
        )
        result = await publish.execute(
            PublishRequest(
                post_id=slot.slot_id,
                destination_id=slot.destination_id,
                payload=payload,
                owner=self._owner,
                correlation_id=f"advertisement-slot:{slot.slot_id}",
                authorized=True,
                post_publishable=True,
                immediate_selected=True,
                session_valid=context.session_valid,
                account_premium=context.account_premium,
                destination_accessible=True,
                action="scheduled",
            )
        )
        publication = result.publication
        if result.status in {PublishStatus.SUCCEEDED, PublishStatus.ALREADY_PUBLISHED}:
            if (
                publication is None
                or publication.state is not PublicationState.SUCCEEDED
                or publication.published_at is None
                or not publication.message_ids
            ):
                return await self._fail_pre_send(slot, "invalid_publication_result")
            completed = await self._slots.complete_slot(
                slot.slot_id,
                owner=self._owner,
                expected_version=slot.version,
                publication_id=publication.publication_id,
                publication_attempt_count=publication.attempt_count,
                message_ids=publication.message_ids,
                published_at=publication.published_at,
            )
            if completed is None:
                return PublishAdvertisementSlotStatus.LEASE_LOST
            return (
                PublishAdvertisementSlotStatus.ALREADY_PUBLISHED
                if result.status is PublishStatus.ALREADY_PUBLISHED
                else PublishAdvertisementSlotStatus.COMPLETED
            )
        if result.status in {PublishStatus.BUSY, PublishStatus.RETRY_PENDING}:
            if slot.claim_count > slot.max_retries:
                return await self._fail_publication(slot, publication, False)
            deferred = await self._slots.defer_slot(
                slot.slot_id,
                owner=self._owner,
                expected_version=slot.version,
                next_attempt_at=self._now()
                + timedelta(seconds=self._busy_retry_delay_seconds),
                category="publication_busy",
                failure_type=None,
                reason_code="publication_claim_busy",
            )
            return (
                PublishAdvertisementSlotStatus.DEFERRED
                if deferred is not None
                else PublishAdvertisementSlotStatus.LEASE_LOST
            )
        return await self._fail_publication(
            slot,
            publication,
            result.status is PublishStatus.OUTCOME_UNKNOWN,
        )

    async def _fail_pre_send(
        self, slot: AdvertisementSlot, reason_code: str
    ) -> PublishAdvertisementSlotStatus:
        failed = await self._slots.fail_slot(
            slot.slot_id,
            owner=self._owner,
            expected_version=slot.version,
            publication_attempt_count=0,
            category="preparation_failure",
            failure_type=None,
            reason_code=reason_code,
            outcome_unknown=False,
        )
        return (
            PublishAdvertisementSlotStatus.FAILED
            if failed is not None
            else PublishAdvertisementSlotStatus.LEASE_LOST
        )

    async def _fail_publication(
        self,
        slot: AdvertisementSlot,
        publication: Publication | None,
        outcome_unknown: bool,
    ) -> PublishAdvertisementSlotStatus:
        attempt_count = 0
        category = "publication_failed"
        failure_type = None
        reason_code = None
        if publication is not None:
            attempt_count = publication.attempt_count
            category = publication.error_category or category
            failure_type = publication.failure_type
            reason_code = publication.failure_reason_code
        failed = await self._slots.fail_slot(
            slot.slot_id,
            owner=self._owner,
            expected_version=slot.version,
            publication_attempt_count=attempt_count,
            category=category,
            failure_type=failure_type,
            reason_code=reason_code,
            outcome_unknown=outcome_unknown,
        )
        if failed is None:
            return PublishAdvertisementSlotStatus.LEASE_LOST
        return (
            PublishAdvertisementSlotStatus.OUTCOME_UNKNOWN
            if outcome_unknown
            else PublishAdvertisementSlotStatus.FAILED
        )

    @staticmethod
    def _payload(
        snapshot: AdvertisementSourceSnapshot, destination_id: int
    ) -> PublicationPayload:
        text = snapshot.caption if snapshot.caption is not None else snapshot.text
        entities = (
            snapshot.caption_entities
            if snapshot.caption is not None
            else snapshot.text_entities
        )
        far_future = datetime.max.replace(tzinfo=UTC)
        media = tuple(
            PublicationMedia(
                media_type=item.media_type,
                storage_path=item.storage_path,
                expires_at=far_future,
                ready=True,
                mime_type=item.mime_type,
                original_filename=item.original_filename,
            )
            for item in sorted(
                snapshot.media_references, key=lambda value: value.item_index
            )
        )
        return PublicationPayload(
            destination_id=destination_id,
            text=text,
            entities=entities,
            media=media,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("publication clock must return an aware instant")
        return value.astimezone(UTC)


__all__ = (
    "AdvertisementPublicationContext",
    "PublishAdvertisementSlot",
    "PublishAdvertisementSlotStatus",
)
