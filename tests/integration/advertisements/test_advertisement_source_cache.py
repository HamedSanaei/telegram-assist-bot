"""Integration tests for advertisement source resolution and MongoDB caching."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.fetch_advertisement_source import (
    FetchAdvertisementSource,
)
from telegram_assist_bot.application.ports import (
    AdvertisementSourceGroupDTO,
    AdvertisementSourceMessageDTO,
    AdvertisementSourceNotFoundError,
    TelegramMediaReference,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceFetchPolicy,
    AdvertisementSourceIdentity,
    FetchAdvertisementSourceOutcomeKind,
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
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.infrastructure.persistence.mongodb.advertisement_repository import (  # noqa: E501
    MongoAdvertisementRepository,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

_FETCH_POLICY = AdvertisementSourceFetchPolicy(
    timeout_seconds=20,
    max_attempts=3,
    initial_backoff_seconds=0,
)


class FakeIntegrationClock:
    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(UTC)

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeIntegrationGateway:
    def __init__(self) -> None:
        self.responses: dict[
            tuple[str, int],
            AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO | Exception,
        ] = {}

    async def fetch_advertisement_post(
        self, channel_username: str, message_id: int
    ) -> AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO:
        key = (channel_username.strip().lower(), message_id)
        if key not in self.responses:
            raise AdvertisementSourceNotFoundError("Source post missing")
        val = self.responses[key]
        if isinstance(val, Exception):
            raise val
        return val


class FakeMediaSource:
    """Return deterministic bytes for opaque Telegram media references."""

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        async def stream() -> AsyncIterator[bytes]:
            yield opaque_reference.encode("utf-8")

        return stream()


class FakeMediaStorage:
    """Content-address fake implementing the MediaStorage write contract."""

    async def store(
        self,
        identity: MediaIdentity,
        stream: AsyncIterator[bytes],
        *,
        maximum_bytes: int,
    ) -> tuple[str, int, str]:
        payload = b""
        async for chunk in stream:
            payload += chunk
        assert len(payload) <= maximum_bytes
        digest = hashlib.sha256(payload).hexdigest()
        return f"sha256/{digest[:2]}/{digest}", len(payload), digest

    async def exists(self, storage_path: str) -> bool:
        return True

    async def delete(self, storage_path: str) -> bool:
        return True

    async def delete_stale_temporary_files(
        self, *, older_than: datetime, limit: int
    ) -> int:
        return 0


def _make_test_campaign(
    *,
    campaign_id: str = "daily-store-ad",
    username: str = "advertisement_example",
    message_id: int = 100,
    cache_policy: SourceCachePolicy = SourceCachePolicy.PERIODIC_REFRESH,
    unavail_policy: SourceUnavailablePolicy = (
        SourceUnavailablePolicy.USE_LAST_VALID_SNAPSHOT
    ),
    refresh_interval: int | None = None,
    retention_days: int = 30,
) -> AdvertisementCampaign:
    effective_interval = (
        refresh_interval
        if refresh_interval is not None
        else (900 if cache_policy == SourceCachePolicy.PERIODIC_REFRESH else None)
    )
    if cache_policy != SourceCachePolicy.PERIODIC_REFRESH:
        effective_interval = None

    return AdvertisementCampaign(
        campaign_id=campaign_id,
        name="تبلیغ فروشگاهی 🛍️",
        enabled=True,
        source_post=SourceAdvertisementPost(
            url=f"https://t.me/{username}/{message_id}",
            channel_username=username,
            message_id=message_id,
        ),
        destination_names=("dest-fa",),
        weekdays=(Weekday.SATURDAY,),
        times=(time(12, 0),),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        timezone=ZoneInfo("Asia/Tehran"),
        publication_mode=AdvertisementPublicationMode.COPY,
        priority=5,
        minimum_gap_seconds=120,
        error_policy=AdvertisementErrorPolicy.RETRY_THEN_FAIL,
        max_retries=3,
        source_cache_policy=cache_policy,
        source_unavailable_policy=unavail_policy,
        snapshot_retention_days=retention_days,
        refresh_interval_seconds=effective_interval,
    )


async def _run_with_repo(
    test_func: Callable[[MongoAdvertisementRepository], Awaitable[None]],
) -> None:
    uri = os.environ.get(
        "TEST_MONGODB_URI", "mongodb://127.0.0.1:27017/?directConnection=true"
    )
    db_name = f"tab_t049_{uuid4().hex}"
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(uri)
    db = client[db_name]
    collection = db["advertisement_snapshots"]
    repo = MongoAdvertisementRepository(collection)
    await repo.initialize_indexes()
    try:
        await test_func(repo)
    finally:
        await client.drop_database(db_name)
        await client.close()


def test_initial_text_fetch_persists_to_mongodb() -> None:
    async def scenario(mongo_repo: MongoAdvertisementRepository) -> None:
        gateway = FakeIntegrationGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeIntegrationClock(t0)
        use_case = FetchAdvertisementSource(
            repository=mongo_repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
            media_source=FakeMediaSource(),
            media_storage=FakeMediaStorage(),
            maximum_media_bytes=1024,
        )

        campaign = _make_test_campaign()
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="متن فارسی تبلیغ به همراه Emoji 🎉",
                caption=None,
                text_entities=(
                    TelegramEntity(offset_utf16=0, length_utf16=4, entity_type="bold"),
                ),
            )
        )

        res = await use_case.execute(campaign)
        assert res.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert res.snapshot is not None
        assert res.snapshot.text == "متن فارسی تبلیغ به همراه Emoji 🎉"
        assert len(res.snapshot.text_entities) == 1

        source_id = AdvertisementSourceIdentity.create(
            "daily-store-ad", "advertisement_example", 100
        )
        persisted = await mongo_repo.get_current_snapshot(
            "daily-store-ad", source_id.source_identity_fingerprint
        )
        assert persisted is not None
        assert persisted.snapshot_id == res.snapshot.snapshot_id
        assert persisted.snapshot_version == 1
        assert persisted.text == "متن فارسی تبلیغ به همراه Emoji 🎉"

    asyncio.run(_run_with_repo(scenario))


def test_out_of_order_album_members_normalized() -> None:
    async def scenario(mongo_repo: MongoAdvertisementRepository) -> None:
        gateway = FakeIntegrationGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeIntegrationClock(t0)
        use_case = FetchAdvertisementSource(
            repository=mongo_repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
            media_source=FakeMediaSource(),
            media_storage=FakeMediaStorage(),
            maximum_media_bytes=1024,
        )

        campaign = _make_test_campaign()

        m100 = AdvertisementSourceMessageDTO(
            source_channel_username="advertisement_example",
            source_message_id=100,
            media_group_id="group-999",
            source_published_at=t0,
            source_edited_at=None,
            text=None,
            caption="کپشن اصلی آلبوم ✨",
            caption_entities=(
                TelegramEntity(offset_utf16=0, length_utf16=5, entity_type="italic"),
            ),
            media=(
                TelegramMediaReference(
                    MediaType.PHOTO,
                    0,
                    500,
                    "image/jpeg",
                    "img100.jpg",
                    "100:100:0",
                    "group-999",
                ),
            ),
        )
        m101 = AdvertisementSourceMessageDTO(
            source_channel_username="advertisement_example",
            source_message_id=101,
            media_group_id="group-999",
            source_published_at=t0,
            source_edited_at=None,
            text=None,
            caption=None,
            media=(
                TelegramMediaReference(
                    MediaType.PHOTO,
                    1,
                    600,
                    "image/jpeg",
                    "img101.jpg",
                    "100:101:0",
                    "group-999",
                ),
            ),
        )
        m102 = AdvertisementSourceMessageDTO(
            source_channel_username="advertisement_example",
            source_message_id=102,
            media_group_id="group-999",
            source_published_at=t0,
            source_edited_at=None,
            text=None,
            caption=None,
            media=(
                TelegramMediaReference(
                    MediaType.PHOTO,
                    2,
                    700,
                    "image/jpeg",
                    "img102.jpg",
                    "100:102:0",
                    "group-999",
                ),
            ),
        )

        gateway.responses[("advertisement_example", 100)] = AdvertisementSourceGroupDTO(
            media_group_id="group-999",
            members=(m100, m101, m102),
            canonical_caption="کپشن اصلی آلبوم ✨",
            canonical_caption_entities=(
                TelegramEntity(offset_utf16=0, length_utf16=5, entity_type="italic"),
            ),
        )

        res = await use_case.execute(campaign)
        assert res.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert res.snapshot is not None
        assert res.snapshot.media_group_id == "group-999"
        assert res.snapshot.caption == "کپشن اصلی آلبوم ✨"
        assert len(res.snapshot.media_references) == 3

    asyncio.run(_run_with_repo(scenario))


def test_edited_source_creates_new_version_and_expires_old() -> None:
    async def scenario(mongo_repo: MongoAdvertisementRepository) -> None:
        gateway = FakeIntegrationGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeIntegrationClock(t0)
        use_case = FetchAdvertisementSource(
            repository=mongo_repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_test_campaign(
            cache_policy=SourceCachePolicy.LATEST, retention_days=30
        )
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="متن نسخه اول",
                caption=None,
            )
        )

        res1 = await use_case.execute(campaign)
        assert res1.snapshot is not None
        assert res1.snapshot.snapshot_version == 1

        clock.advance(timedelta(minutes=10))
        t1 = t0 + timedelta(minutes=10)
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=t1,
                text="متن نسخه دوم ویرایش شده",
                caption=None,
            )
        )

        res2 = await use_case.execute(campaign)
        assert res2.kind == FetchAdvertisementSourceOutcomeKind.REFRESH_UPDATED
        assert res2.snapshot is not None
        assert res2.snapshot.snapshot_version == 2
        assert res2.snapshot.text == "متن نسخه دوم ویرایش شده"

        docs = await mongo_repo._collection.find(
            {"campaign_id": "daily-store-ad"}
        ).to_list(10)
        assert len(docs) == 2
        old_doc = next(d for d in docs if d["snapshot_version"] == 1)
        new_doc = next(d for d in docs if d["snapshot_version"] == 2)
        assert old_doc["is_current"] is False
        assert old_doc["expires_at"] is not None
        assert new_doc["is_current"] is True
        assert new_doc["expires_at"] is None

    asyncio.run(_run_with_repo(scenario))


def test_concurrent_initial_fetches_produce_single_current_snapshot() -> None:
    async def scenario(mongo_repo: MongoAdvertisementRepository) -> None:
        gateway = FakeIntegrationGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeIntegrationClock(t0)
        use_case = FetchAdvertisementSource(
            repository=mongo_repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        campaign = _make_test_campaign(cache_policy=SourceCachePolicy.LATEST)
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="پست هم‌زمان",
                caption=None,
            )
        )

        _res1, _res2 = await asyncio.gather(
            use_case.execute(campaign),
            use_case.execute(campaign),
        )

        source_id = AdvertisementSourceIdentity.create(
            "daily-store-ad", "advertisement_example", 100
        )
        docs = await mongo_repo._collection.find(
            {
                "campaign_id": "daily-store-ad",
                "source_identity_fingerprint": source_id.source_identity_fingerprint,
                "is_current": True,
            }
        ).to_list(10)

        assert len(docs) == 1
        assert docs[0]["snapshot_version"] == 1

    asyncio.run(_run_with_repo(scenario))


def test_source_identity_change_does_not_reuse_old_snapshot() -> None:
    async def scenario(mongo_repo: MongoAdvertisementRepository) -> None:
        gateway = FakeIntegrationGateway()
        t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        clock = FakeIntegrationClock(t0)
        use_case = FetchAdvertisementSource(
            repository=mongo_repo,
            source_gateway=gateway,
            clock=clock,
            fetch_policy=_FETCH_POLICY,
        )

        c1 = _make_test_campaign(message_id=100, cache_policy=SourceCachePolicy.CACHED)
        gateway.responses[("advertisement_example", 100)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=100,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="پست قدیمی ۱۰۰",  # noqa: RUF001
                caption=None,
            )
        )
        res1 = await use_case.execute(c1)
        assert res1.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL

        c2 = _make_test_campaign(message_id=200, cache_policy=SourceCachePolicy.CACHED)
        gateway.responses[("advertisement_example", 200)] = (
            AdvertisementSourceMessageDTO(
                source_channel_username="advertisement_example",
                source_message_id=200,
                media_group_id=None,
                source_published_at=t0,
                source_edited_at=None,
                text="پست جدید ۲۰۰",
                caption=None,
            )
        )

        res2 = await use_case.execute(c2)
        assert res2.kind == FetchAdvertisementSourceOutcomeKind.FETCHED_INITIAL
        assert res2.snapshot is not None
        assert res2.snapshot.text == "پست جدید ۲۰۰"

    asyncio.run(_run_with_repo(scenario))
