"""Unit tests for idempotent advertisement slot publication."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.advertisements.publish_advertisement_slot import (
    AdvertisementPublicationContext,
    PublishAdvertisementSlot,
    PublishAdvertisementSlotStatus,
)
from telegram_assist_bot.application.ports import (
    PublicationClaimOutcome,
    PublicationClaimResult,
    PublicationPayload,
    PublisherError,
)
from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementSlot,
    AdvertisementSlotStatus,
    advertisement_slot_identity,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.domain.publication import (
    Publication,
    PublicationFailureCategory,
    PublicationState,
    PublishedMessage,
    publication_identity,
)
from telegram_assist_bot.domain.publication_collision import CollisionResolutionState

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AdvertisementRepository,
        AdvertisementSlotRepository,
        PublicationRepository,
    )

NOW = datetime(2026, 7, 22, 10, tzinfo=UTC)
DESTINATION_ID = -100200


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


class SlotRepository:
    """Keep one slot and apply owner/version transitions like MongoDB CAS."""

    def __init__(self, slot: AdvertisementSlot) -> None:
        self.slot = slot

    async def claim_due_slot(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> AdvertisementSlot | None:
        if self.slot.status not in {
            AdvertisementSlotStatus.SCHEDULED,
            AdvertisementSlotStatus.WAITING_FOR_RETRY,
        }:
            return None
        if self.slot.due_at > now or (
            self.slot.next_attempt_at is not None and self.slot.next_attempt_at > now
        ):
            return None
        self.slot = replace(
            self.slot,
            status=AdvertisementSlotStatus.CLAIMED,
            claim_owner=owner,
            lease_until=lease_until,
            claim_count=self.slot.claim_count + 1,
            version=self.slot.version + 1,
        )
        return self.slot

    async def complete_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        publication_id: str,
        publication_attempt_count: int,
        message_ids: tuple[int, ...],
        published_at: datetime,
    ) -> AdvertisementSlot | None:
        if not self._owns(slot_id, owner, expected_version):
            return None
        self.slot = replace(
            self.slot,
            status=AdvertisementSlotStatus.COMPLETED,
            publication_id=publication_id,
            publication_attempt_count=publication_attempt_count,
            message_ids=message_ids,
            published_at=published_at,
            execution_delay_seconds=(published_at - self.slot.due_at).total_seconds(),
            claim_owner=None,
            lease_until=None,
            version=self.slot.version + 1,
        )
        return self.slot

    async def defer_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        next_attempt_at: datetime,
        category: str,
        failure_type: str | None,
        reason_code: str | None,
    ) -> AdvertisementSlot | None:
        if not self._owns(slot_id, owner, expected_version):
            return None
        self.slot = replace(
            self.slot,
            status=AdvertisementSlotStatus.WAITING_FOR_RETRY,
            next_attempt_at=next_attempt_at,
            last_error_category=category,
            last_failure_type=failure_type,
            last_failure_reason_code=reason_code,
            claim_owner=None,
            lease_until=None,
            version=self.slot.version + 1,
        )
        return self.slot

    async def fail_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        publication_attempt_count: int,
        category: str,
        failure_type: str | None,
        reason_code: str | None,
        outcome_unknown: bool,
    ) -> AdvertisementSlot | None:
        if not self._owns(slot_id, owner, expected_version):
            return None
        self.slot = replace(
            self.slot,
            status=(
                AdvertisementSlotStatus.OUTCOME_UNKNOWN
                if outcome_unknown
                else AdvertisementSlotStatus.PERMANENT_FAILED
            ),
            publication_attempt_count=publication_attempt_count,
            last_error_category=category,
            last_failure_type=failure_type,
            last_failure_reason_code=reason_code,
            claim_owner=None,
            lease_until=None,
            version=self.slot.version + 1,
        )
        return self.slot

    def _owns(self, slot_id: str, owner: str, version: int) -> bool:
        return (
            self.slot.slot_id == slot_id
            and self.slot.status is AdvertisementSlotStatus.CLAIMED
            and self.slot.claim_owner == owner
            and self.slot.version == version
        )


class SnapshotRepository:
    def __init__(self, snapshot: AdvertisementSourceSnapshot | None) -> None:
        self.snapshot = snapshot

    async def get_snapshot_by_id(
        self, snapshot_id: str
    ) -> AdvertisementSourceSnapshot | None:
        if self.snapshot is not None and self.snapshot.snapshot_id == snapshot_id:
            return self.snapshot
        return None


class PublicationRepositoryFake:
    """Model terminal identity and bounded retry attempts."""

    def __init__(self, value: Publication | None = None) -> None:
        self.value = value

    async def claim(self, **values: object) -> PublicationClaimResult:
        if self.value is not None and self.value.state in {
            PublicationState.SUCCEEDED,
            PublicationState.PERMANENT_FAILED,
            PublicationState.OUTCOME_UNKNOWN,
        }:
            return PublicationClaimResult(PublicationClaimOutcome.TERMINAL, self.value)
        attempt = 1 if self.value is None else self.value.attempt_count + 1
        maximum = cast("int", values["max_attempts"])
        if attempt > maximum:
            assert self.value is not None
            return PublicationClaimResult(PublicationClaimOutcome.EXHAUSTED, self.value)
        self.value = Publication(
            publication_id=cast("str", values["publication_id"]),
            post_id=cast("str", values["post_id"]),
            destination_id=cast("int", values["destination_id"]),
            state=PublicationState.CLAIMED,
            attempt_count=attempt,
            claim_owner=cast("str", values["owner"]),
            attempted_at=NOW,
        )
        return PublicationClaimResult(PublicationClaimOutcome.CLAIMED, self.value)

    async def complete(
        self, _publication_id: str, *, owner: str, result: PublishedMessage
    ) -> Publication:
        assert self.value is not None
        assert self.value.claim_owner == owner
        self.value = replace(
            self.value,
            state=PublicationState.SUCCEEDED,
            message_ids=result.message_ids,
            published_at=result.published_at,
            claim_owner=None,
        )
        return self.value

    async def fail(
        self,
        _publication_id: str,
        *,
        owner: str,
        category: PublicationFailureCategory,
        now: datetime,
        next_attempt_at: datetime | None,
        outcome_unknown: bool,
        failure_type: str | None = None,
        failure_reason_code: str | None = None,
    ) -> Publication:
        del owner, now
        assert self.value is not None
        self.value = replace(
            self.value,
            state=(
                PublicationState.OUTCOME_UNKNOWN
                if outcome_unknown
                else (
                    PublicationState.WAITING_FOR_RETRY
                    if next_attempt_at is not None
                    else PublicationState.PERMANENT_FAILED
                )
            ),
            error_category=category.value,
            failure_type=failure_type,
            failure_reason_code=failure_reason_code,
            next_attempt_at=next_attempt_at,
            claim_owner=None,
        )
        return self.value


class Publisher:
    def __init__(self, errors: list[PublisherError] | None = None) -> None:
        self.errors = errors or []
        self.payloads: list[PublicationPayload] = []

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        assert timeout_seconds == 10
        self.payloads.append(payload)
        if self.errors:
            raise self.errors.pop(0)
        return PublishedMessage((701, 702), NOW + timedelta(seconds=35))


def snapshot() -> AdvertisementSourceSnapshot:
    identity = AdvertisementSourceIdentity.create("campaign-a", "sample_ads", 42)
    return AdvertisementSourceSnapshot(
        snapshot_id="snapshot-a-v1",
        campaign_id="campaign-a",
        source_identity=identity,
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="hash-a",
        text=None,
        caption="تبلیغ با نیم\u200cفاصله و ایموجی ✨",  # noqa: RUF001
        text_entities=(),
        caption_entities=(TelegramEntity(22, 2, "custom_emoji", "987654"),),
        media_group_id="album-a",
        media_references=(
            AdvertisementMediaReference(
                MediaType.PHOTO, 1, 20, "image/jpeg", "two.jpg", "cache/two"
            ),
            AdvertisementMediaReference(
                MediaType.PHOTO, 0, 10, "image/jpeg", "one.jpg", "cache/one"
            ),
        ),
        source_published_at=NOW - timedelta(days=1),
        source_edited_at=None,
        fetched_at=NOW - timedelta(hours=1),
        last_successful_fetch_at=NOW - timedelta(hours=1),
    )


def slot(*, max_retries: int = 2) -> AdvertisementSlot:
    due_at = NOW - timedelta(seconds=35)
    return AdvertisementSlot(
        slot_id=advertisement_slot_identity("campaign-a", DESTINATION_ID, due_at),
        campaign_id="campaign-a",
        destination_name="news",
        destination_id=DESTINATION_ID,
        due_at=due_at,
        local_scheduled_at=due_at,
        timezone_name="UTC",
        source_snapshot_id="snapshot-a-v1",
        source_snapshot_version=1,
        config_fingerprint="config-a",
        priority=1,
        minimum_gap_seconds=300,
        max_retries=max_retries,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        collision_state=CollisionResolutionState.RESOLVED,
    )


def service(
    slots: SlotRepository,
    snapshots: SnapshotRepository,
    publications: PublicationRepositoryFake,
    publisher: Publisher,
) -> PublishAdvertisementSlot:
    async def no_sleep(_delay: float) -> None:
        return None

    return PublishAdvertisementSlot(
        cast("AdvertisementSlotRepository", slots),
        cast("AdvertisementRepository", snapshots),
        cast("PublicationRepository", publications),
        publisher,
        owner="worker-a",
        clock=lambda: NOW,
        sleeper=no_sleep,
        timeout_seconds=10,
        lease_seconds=30,
        retry_initial_delay_seconds=1,
        retry_maximum_delay_seconds=5,
        busy_retry_delay_seconds=10,
    )


def context(*, allowed: bool = True) -> AdvertisementPublicationContext:
    return AdvertisementPublicationContext(
        allowed_destination_ids=(DESTINATION_ID,) if allowed else (),
        session_valid=True,
        account_premium=True,
    )


@async_test
async def test_success_preserves_caption_entities_album_order_and_audit() -> None:
    slots, publisher = SlotRepository(slot()), Publisher()
    result = await service(
        slots, SnapshotRepository(snapshot()), PublicationRepositoryFake(), publisher
    ).execute_once(context())

    assert result is PublishAdvertisementSlotStatus.COMPLETED
    assert (
        publisher.payloads[0].text == "تبلیغ با نیم\u200cفاصله و ایموجی ✨"  # noqa: RUF001
    )
    assert publisher.payloads[0].entities[0].custom_emoji_id == "987654"
    assert [item.storage_path for item in publisher.payloads[0].media] == [
        "cache/one",
        "cache/two",
    ]
    assert slots.slot.status is AdvertisementSlotStatus.COMPLETED
    assert slots.slot.message_ids == (701, 702)
    assert slots.slot.execution_delay_seconds == 70
    assert slots.slot.publication_attempt_count == 1


@async_test
async def test_transient_failure_retries_within_campaign_limit() -> None:
    transient = PublisherError(
        PublicationFailureCategory.TRANSIENT,
        reason_code="telegram_temporarily_unavailable",
    )
    slots, publisher = SlotRepository(slot(max_retries=1)), Publisher([transient])
    result = await service(
        slots, SnapshotRepository(snapshot()), PublicationRepositoryFake(), publisher
    ).execute_once(context())

    assert result is PublishAdvertisementSlotStatus.COMPLETED
    assert len(publisher.payloads) == 2
    assert slots.slot.publication_attempt_count == 2


@async_test
async def test_ambiguous_send_is_terminal_and_never_retried() -> None:
    ambiguous = PublisherError(
        PublicationFailureCategory.TIMEOUT,
        request_may_have_reached_telegram=True,
        reason_code="send_outcome_unknown",
    )
    slots, publisher = SlotRepository(slot()), Publisher([ambiguous])
    result = await service(
        slots, SnapshotRepository(snapshot()), PublicationRepositoryFake(), publisher
    ).execute_once(context())

    assert result is PublishAdvertisementSlotStatus.OUTCOME_UNKNOWN
    assert len(publisher.payloads) == 1
    assert slots.slot.status is AdvertisementSlotStatus.OUTCOME_UNKNOWN
    assert slots.slot.last_failure_reason_code == "send_outcome_unknown"


@async_test
@pytest.mark.parametrize(
    ("missing_snapshot", "allowed"), [(True, True), (False, False)]
)
async def test_pre_send_guards_fail_without_external_call(
    missing_snapshot: bool, allowed: bool
) -> None:
    slots, publisher = SlotRepository(slot()), Publisher()
    source = SnapshotRepository(None if missing_snapshot else snapshot())
    result = await service(
        slots, source, PublicationRepositoryFake(), publisher
    ).execute_once(context(allowed=allowed))

    assert result is PublishAdvertisementSlotStatus.FAILED
    assert publisher.payloads == []
    assert slots.slot.status is AdvertisementSlotStatus.PERMANENT_FAILED


@async_test
async def test_prior_publication_success_completes_slot_without_second_send() -> None:
    value = Publication(
        publication_id=publication_identity(
            slot().slot_id, DESTINATION_ID, action="scheduled"
        ),
        post_id=slot().slot_id,
        destination_id=DESTINATION_ID,
        state=PublicationState.SUCCEEDED,
        attempt_count=1,
        message_ids=(801,),
        published_at=NOW,
    )
    slots, publisher = SlotRepository(slot()), Publisher()
    result = await service(
        slots,
        SnapshotRepository(snapshot()),
        PublicationRepositoryFake(value),
        publisher,
    ).execute_once(context())

    assert result is PublishAdvertisementSlotStatus.ALREADY_PUBLISHED
    assert publisher.payloads == []
    assert slots.slot.status is AdvertisementSlotStatus.COMPLETED
    assert slots.slot.message_ids == (801,)


@async_test
async def test_cas_loser_returns_lease_lost() -> None:
    slots, publisher = SlotRepository(slot()), Publisher()
    original = slots.complete_slot

    async def lose(*args: object, **kwargs: object) -> AdvertisementSlot | None:
        del args, kwargs
        return None

    slots.complete_slot = lose  # type: ignore[method-assign]
    result = await service(
        slots, SnapshotRepository(snapshot()), PublicationRepositoryFake(), publisher
    ).execute_once(context())
    slots.complete_slot = original  # type: ignore[method-assign]

    assert result is PublishAdvertisementSlotStatus.LEASE_LOST
