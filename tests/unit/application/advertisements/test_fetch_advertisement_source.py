"""Unit tests for FetchAdvertisementSource use case and advertisement source models."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram_assist_bot.application.advertisements.fetch_advertisement_source import (
    FetchAdvertisementSource,
)
from telegram_assist_bot.application.ports import (
    AdvertisementSourceGroupDTO,
    AdvertisementSourceMessageDTO,
    AdvertisementSourceNotFoundError,
    AdvertisementSourceTransientError,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceFetchPolicy,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
    FetchAdvertisementSourceOutcomeKind,
    compute_canonical_content_hash,
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
from telegram_assist_bot.domain.posts import TelegramEntity


class FakeClock:
    """Deterministic clock for unit tests."""

    def __init__(self, current_time: datetime) -> None:
        self._now = current_time.astimezone(UTC)

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeAdvertisementRepository:
    """In-memory advertisement repository for unit testing."""

    def __init__(self) -> None:
        self.snapshots: dict[str, AdvertisementSourceSnapshot] = {}
        self.history: list[AdvertisementSourceSnapshot] = []
        self.unchanged_checks: list[tuple[str, str, datetime]] = []
        self.failed_checks: list[tuple[str, str, datetime, str]] = []

    async def get_current_snapshot(
        self, campaign_id: str, source_identity_fingerprint: str
    ) -> AdvertisementSourceSnapshot | None:
        key = f"{campaign_id}:{source_identity_fingerprint}"
        return self.snapshots.get(key)

    async def get_snapshot_by_id(
        self, snapshot_id: str
    ) -> AdvertisementSourceSnapshot | None:
        return next(
            (
                snapshot
                for snapshot in self.history
                if snapshot.snapshot_id == snapshot_id
            ),
            None,
        )

    async def save_initial_snapshot(
        self, snapshot: AdvertisementSourceSnapshot
    ) -> AdvertisementSourceSnapshot:
        fingerprint = snapshot.source_identity.source_identity_fingerprint
        key = f"{snapshot.campaign_id}:{fingerprint}"
        self.snapshots[key] = snapshot
        self.history.append(snapshot)
        return snapshot

    async def commit_changed_snapshot(
        self,
        new_snapshot: AdvertisementSourceSnapshot,
        expected_current_version: int,
        retention_days: int,
    ) -> AdvertisementSourceSnapshot:
        fingerprint = new_snapshot.source_identity.source_identity_fingerprint
        key = f"{new_snapshot.campaign_id}:{fingerprint}"
        old = self.snapshots.get(key)
        if old is not None:
            old_superseded = AdvertisementSourceSnapshot(
                snapshot_id=old.snapshot_id,
                campaign_id=old.campaign_id,
                source_identity=old.source_identity,
                snapshot_version=old.snapshot_version,
                snapshot_contract_version=old.snapshot_contract_version,
                content_hash=old.content_hash,
                text=old.text,
                caption=old.caption,
                text_entities=old.text_entities,
                caption_entities=old.caption_entities,
                media_group_id=old.media_group_id,
                media_references=old.media_references,
                source_published_at=old.source_published_at,
                source_edited_at=old.source_edited_at,
                fetched_at=old.fetched_at,
                last_successful_fetch_at=old.last_successful_fetch_at,
                is_current=False,
                expires_at=new_snapshot.fetched_at + timedelta(days=retention_days),
                is_stale=old.is_stale,
                stale_reason=old.stale_reason,
            )
            self.history.append(old_superseded)
        self.snapshots[key] = new_snapshot
        self.history.append(new_snapshot)
        return new_snapshot

    async def record_unchanged_check(
        self, campaign_id: str, source_identity_fingerprint: str, fetched_at: datetime
    ) -> None:
        key = f"{campaign_id}:{source_identity_fingerprint}"
        current = self.snapshots.get(key)
        if current is not None:
            updated = AdvertisementSourceSnapshot(
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
                fetched_at=fetched_at,
                last_successful_fetch_at=fetched_at,
                is_current=True,
                expires_at=current.expires_at,
                is_stale=False,
                stale_reason=None,
            )
            self.snapshots[key] = updated
        self.unchanged_checks.append(
            (campaign_id, source_identity_fingerprint, fetched_at)
        )

    async def record_failed_check(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
        failed_at: datetime,
        error_reason: str,
    ) -> None:
        self.failed_checks.append(
            (campaign_id, source_identity_fingerprint, failed_at, error_reason)
        )

    async def initialize_indexes(self) -> None:
        pass


class FakeSourceGateway:
    """Fake Telegram advertisement source gateway."""

    def __init__(self) -> None:
        self.responses: dict[
            tuple[str, int],
            AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO | Exception,
        ] = {}
        self.call_count = 0

    async def fetch_advertisement_post(
        self, channel_username: str, message_id: int
    ) -> AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO:
        self.call_count += 1
        key = (channel_username.strip().lower(), message_id)
        if key not in self.responses:
            raise AdvertisementSourceNotFoundError("Source post not found")
        val = self.responses[key]
        if isinstance(val, Exception):
            raise val
        return val


def _make_campaign(
    *,
    enabled: bool = True,
    cache_policy: SourceCachePolicy = SourceCachePolicy.CACHED,
    unavail_policy: SourceUnavailablePolicy = SourceUnavailablePolicy.FAIL_CLOSED,
    retention_days: int = 30,
    refresh_interval: int | None = None,
    username: str = "advertisement_example",
    message_id: int = 100,
) -> AdvertisementCampaign:
    return AdvertisementCampaign(
        campaign_id="test-campaign",
        name="آگهی ویژه 🌟",
        enabled=enabled,
        source_post=SourceAdvertisementPost(
            url=f"https://t.me/{username}/{message_id}",
            channel_username=username,
            message_id=message_id,
        ),
        destination_names=("dest-1",),
        weekdays=(Weekday.MONDAY,),
        times=(time(10, 0),),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        timezone=ZoneInfo("Asia/Tehran"),
        publication_mode=AdvertisementPublicationMode.COPY,
        priority=10,
        minimum_gap_seconds=300,
        error_policy=AdvertisementErrorPolicy.RETRY_THEN_FAIL,
        max_retries=3,
        source_cache_policy=cache_policy,
        source_unavailable_policy=unavail_policy,
        snapshot_retention_days=retention_days,
        refresh_interval_seconds=refresh_interval,
    )


def test_disabled_campaign_fetch_returns_disabled_without_contact() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        clock = FakeClock(datetime(2026, 8, 1, 10, 0, tzinfo=UTC))
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(enabled=False)
        result = await use_case.execute(campaign)

        assert result.kind == FetchAdvertisementSourceOutcomeKind.DISABLED
        assert result.snapshot is None
        assert gateway.call_count == 0

    asyncio.run(scenario())


def test_cached_policy_returns_cache_hit_when_snapshot_exists() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        clock = FakeClock(datetime(2026, 8, 1, 10, 0, tzinfo=UTC))
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(cache_policy=SourceCachePolicy.CACHED)
        source_id = AdvertisementSourceIdentity.create(
            "test-campaign", "advertisement_example", 100
        )
        existing = AdvertisementSourceSnapshot(
            snapshot_id="snap-1",
            campaign_id="test-campaign",
            source_identity=source_id,
            snapshot_version=1,
            snapshot_contract_version="1.0.0",
            content_hash="hash1",
            text="سلام آگهی!",
            caption=None,
            text_entities=(),
            caption_entities=(),
            media_group_id=None,
            media_references=(),
            source_published_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
            source_edited_at=None,
            fetched_at=datetime(2026, 8, 1, 9, 30, tzinfo=UTC),
            last_successful_fetch_at=datetime(2026, 8, 1, 9, 30, tzinfo=UTC),
        )
        await repo.save_initial_snapshot(existing)

        result = await use_case.execute(campaign)
        assert result.kind == FetchAdvertisementSourceOutcomeKind.CACHE_HIT
        assert result.snapshot is not None
        assert result.snapshot.text == "سلام آگهی!"
        assert gateway.call_count == 0

    asyncio.run(scenario())


def test_periodic_refresh_due_triggers_telegram_fetch() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        start_time = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeClock(start_time)
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(
            cache_policy=SourceCachePolicy.PERIODIC_REFRESH,
            refresh_interval=900,
        )

        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=start_time,
                source_edited_at=None,
                text="سلام متن تبلیغ ⚡️",
                caption=None,
            )
        )
        res1 = await use_case.execute(campaign)
        assert res1.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert gateway.call_count == 1

        clock.advance(timedelta(seconds=300))
        res2 = await use_case.execute(campaign)
        assert res2.kind == FetchAdvertisementSourceOutcomeKind.CACHE_HIT
        assert gateway.call_count == 1

        clock.advance(timedelta(seconds=601))
        res3 = await use_case.execute(campaign)
        assert res3.kind == FetchAdvertisementSourceOutcomeKind.REFRESH_UNCHANGED
        assert gateway.call_count == 2

    asyncio.run(scenario())


def test_latest_policy_always_checks_telegram() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        clock = FakeClock(datetime(2026, 8, 1, 10, 0, tzinfo=UTC))
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(cache_policy=SourceCachePolicy.LATEST)
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
                source_edited_at=None,
                text="متن روز",
                caption=None,
            )
        )

        r1 = await use_case.execute(campaign)
        assert r1.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert gateway.call_count == 1

        r2 = await use_case.execute(campaign)
        assert r2.kind == FetchAdvertisementSourceOutcomeKind.REFRESH_UNCHANGED
        assert gateway.call_count == 2

    asyncio.run(scenario())


def test_source_edited_content_creates_new_version() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeClock(t0)
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(cache_policy=SourceCachePolicy.LATEST)
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="نسخه اول متن تبلیغ",
                caption=None,
            )
        )
        res1 = await use_case.execute(campaign)
        assert res1.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert res1.snapshot is not None
        assert res1.snapshot.snapshot_version == 1

        t1 = t0 + timedelta(minutes=10)
        clock.advance(timedelta(minutes=10))
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=t1,
                text="نسخه دوم ویرایش‌شده متن تبلیغ ✨",
                caption=None,
            )
        )

        res2 = await use_case.execute(campaign)
        assert res2.kind == FetchAdvertisementSourceOutcomeKind.REFRESH_UPDATED
        assert res2.snapshot is not None
        assert res2.snapshot.snapshot_version == 2
        assert res2.snapshot.text == "نسخه دوم ویرایش‌شده متن تبلیغ ✨"

    asyncio.run(scenario())


def test_transient_failure_uses_stale_snapshot_when_configured() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeClock(t0)
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(
            cache_policy=SourceCachePolicy.LATEST,
            unavail_policy=SourceUnavailablePolicy.USE_LAST_VALID_SNAPSHOT,
        )

        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="پست معتبر اولیه",
                caption=None,
            )
        )
        r1 = await use_case.execute(campaign)
        assert r1.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL

        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceTransientError("Network down")
        )
        clock.advance(timedelta(minutes=5))

        r2 = await use_case.execute(campaign)
        assert r2.kind == FetchAdvertisementSourceOutcomeKind.STALE_FALLBACK
        assert r2.snapshot is not None
        assert r2.snapshot.is_stale is True
        assert r2.snapshot.stale_reason == "temporarily_unavailable"
        assert r2.snapshot.text == "پست معتبر اولیه"

    asyncio.run(scenario())


def test_source_deleted_with_fail_closed_returns_unavailable() -> None:
    async def scenario() -> None:
        repo = FakeAdvertisementRepository()
        gateway = FakeSourceGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeClock(t0)
        use_case = FetchAdvertisementSource(
            repository=repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_campaign(
            cache_policy=SourceCachePolicy.LATEST,
            unavail_policy=SourceUnavailablePolicy.FAIL_CLOSED,
        )

        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="پست اولیه",
                caption=None,
            )
        )
        await use_case.execute(campaign)

        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceNotFoundError("Deleted")
        )
        res = await use_case.execute(campaign)

        assert res.kind == FetchAdvertisementSourceOutcomeKind.UNAVAILABLE
        assert res.snapshot is None
        assert res.error_reason == "source_deleted"

    asyncio.run(scenario())


def test_canonical_content_hash_preserves_persian_zwnj_and_emoji() -> None:
    h1 = compute_canonical_content_hash(
        text="سلام‌علیکم ✨ نیم‌فاصله‌دار",
        caption=None,
        text_entities=(
            TelegramEntity(offset_utf16=0, length_utf16=4, entity_type="bold"),
        ),
    )
    h2 = compute_canonical_content_hash(
        text="سلام‌علیکم ✨ نیم‌فاصله‌دار",
        caption=None,
        text_entities=(
            TelegramEntity(offset_utf16=0, length_utf16=4, entity_type="bold"),
        ),
    )
    h3 = compute_canonical_content_hash(
        text="سلام علیکم ✨ بدون نیم فاصله",
        caption=None,
        text_entities=(
            TelegramEntity(offset_utf16=0, length_utf16=4, entity_type="bold"),
        ),
    )

    assert h1 == h2
    assert h1 != h3


_FETCH_POLICY = AdvertisementSourceFetchPolicy(
    timeout_seconds=20,
    max_attempts=3,
    initial_backoff_seconds=0,
)
