"""Unit tests for deterministic advertisement slot expansion."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.application.advertisements.expand_advertisement_slots import (
    ExpandAdvertisementSlots,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.advertisements import (
    AdvertisementCampaign,
    AdvertisementErrorPolicy,
    AdvertisementPublicationMode,
    SourceAdvertisementPost,
    SourceCachePolicy,
    SourceUnavailablePolicy,
    Weekday,
)

if TYPE_CHECKING:
    from telegram_assist_bot.domain.advertisement_slot import (
        AdvertisementSlot,
        AdvertisementSlotAudit,
    )


class FixedClock:
    def utc_now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class MemorySlotRepository:
    def __init__(self) -> None:
        self.slots: tuple[AdvertisementSlot, ...] = ()
        self.audits: tuple[AdvertisementSlotAudit, ...] = ()
        self.calls = 0

    async def initialize_indexes(self) -> None:
        return None

    async def reconcile_campaign_slots(
        self,
        campaign_id: str,
        desired_slots: tuple[AdvertisementSlot, ...],
        audits: tuple[AdvertisementSlotAudit, ...],
        *,
        now: datetime,
    ) -> tuple[AdvertisementSlot, ...]:
        del campaign_id, now
        self.calls += 1
        by_id = {item.slot_id: item for item in self.slots}
        by_id.update({item.slot_id: item for item in desired_slots})
        self.slots = tuple(
            sorted(by_id.values(), key=lambda item: (item.due_at, item.destination_id))
        )
        self.audits = audits
        return self.slots

    async def list_campaign_slots(
        self, campaign_id: str
    ) -> tuple[AdvertisementSlot, ...]:
        return tuple(item for item in self.slots if item.campaign_id == campaign_id)

    async def claim_due_slot(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> AdvertisementSlot | None:
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError


def campaign(
    *,
    start: date = date(2026, 1, 5),
    end: date = date(2026, 1, 5),
    weekdays: tuple[Weekday, ...] = (Weekday.MONDAY,),
    times: tuple[time, ...] = (time(9, 0),),
    timezone: str = "Asia/Tehran",
    enabled: bool = True,
) -> AdvertisementCampaign:
    return AdvertisementCampaign(
        campaign_id="campaign-a",
        name="کمپین نمونه ✨",
        enabled=enabled,
        source_post=SourceAdvertisementPost(
            "https://t.me/sample_ads/42", "sample_ads", 42
        ),
        destination_names=("one", "two"),
        weekdays=weekdays,
        times=times,
        start_date=start,
        end_date=end,
        timezone=ZoneInfo(timezone),
        publication_mode=AdvertisementPublicationMode.COPY,
        priority=2,
        minimum_gap_seconds=300,
        error_policy=AdvertisementErrorPolicy.RETRY_THEN_FAIL,
        max_retries=3,
        source_cache_policy=SourceCachePolicy.CACHED,
        source_unavailable_policy=SourceUnavailablePolicy.FAIL_CLOSED,
        snapshot_retention_days=30,
    )


def snapshot() -> AdvertisementSourceSnapshot:
    identity = AdvertisementSourceIdentity.create("campaign-a", "sample_ads", 42)
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    return AdvertisementSourceSnapshot(
        snapshot_id="snapshot-a-v1",
        campaign_id="campaign-a",
        source_identity=identity,
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="hash-a",
        text="تبلیغ با نیم‌فاصله و ایموجی ✨",
        caption=None,
        text_entities=(),
        caption_entities=(),
        media_group_id=None,
        media_references=(),
        source_published_at=instant,
        source_edited_at=None,
        fetched_at=instant,
        last_successful_fetch_at=instant,
    )


def test_expands_inclusive_dates_times_and_destinations_idempotently() -> None:
    repository = MemorySlotRepository()
    use_case = ExpandAdvertisementSlots(repository, FixedClock())
    configured = campaign(
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        weekdays=(Weekday.MONDAY, Weekday.TUESDAY),
        times=(time(9), time(18, 30)),
    )

    first = asyncio.run(
        use_case.execute(configured, snapshot(), {"one": -1001, "two": -1002})
    )
    second = asyncio.run(
        use_case.execute(configured, snapshot(), {"one": -1001, "two": -1002})
    )

    assert len(first.slots) == 8
    assert len(second.slots) == 8
    assert len({item.slot_id for item in second.slots}) == 8
    assert all(item.due_at.tzinfo is UTC for item in second.slots)
    assert all(item.timezone_name == "Asia/Tehran" for item in second.slots)
    assert all(item.source_snapshot_version == 1 for item in second.slots)


def test_nonexistent_dst_time_is_skipped_with_sanitized_audit() -> None:
    repository = MemorySlotRepository()
    use_case = ExpandAdvertisementSlots(repository, FixedClock())
    configured = campaign(
        start=date(2026, 3, 8),
        end=date(2026, 3, 8),
        weekdays=(Weekday.SUNDAY,),
        times=(time(2, 30),),
        timezone="America/New_York",
    )

    result = asyncio.run(
        use_case.execute(configured, snapshot(), {"one": -1001, "two": -1002})
    )

    assert result.slots == ()
    assert result.skipped_nonexistent_times == 1
    assert repository.audits[0].reason == "nonexistent_local_time"
    assert repository.audits[0].local_scheduled_value == "2026-03-08T02:30"


def test_ambiguous_dst_time_uses_first_occurrence_once_per_destination() -> None:
    repository = MemorySlotRepository()
    configured = campaign(
        start=date(2026, 11, 1),
        end=date(2026, 11, 1),
        weekdays=(Weekday.SUNDAY,),
        times=(time(1, 30),),
        timezone="America/New_York",
    )

    result = asyncio.run(
        ExpandAdvertisementSlots(repository, FixedClock()).execute(
            configured, snapshot(), {"one": -1001, "two": -1002}
        )
    )

    assert len(result.slots) == 2
    assert {item.due_at for item in result.slots} == {
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    }


def test_disabled_campaign_has_no_repository_or_slot_activity() -> None:
    repository = MemorySlotRepository()
    result = asyncio.run(
        ExpandAdvertisementSlots(repository, FixedClock()).execute(
            replace(campaign(), enabled=False),
            snapshot(),
            {"one": -1001, "two": -1002},
        )
    )
    assert result.slots == ()
    assert repository.calls == 0


def test_invalid_destination_or_snapshot_is_rejected_before_persistence() -> None:
    repository = MemorySlotRepository()
    use_case = ExpandAdvertisementSlots(repository, FixedClock())
    with pytest.raises(ValueError, match="destination"):
        asyncio.run(use_case.execute(campaign(), snapshot(), {"one": -1001}))
    with pytest.raises(ValueError, match="current campaign snapshot"):
        asyncio.run(
            use_case.execute(
                campaign(),
                replace(snapshot(), is_current=False),
                {"one": -1001, "two": -1002},
            )
        )
    assert repository.calls == 0
