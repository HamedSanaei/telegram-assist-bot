"""Verify replay-safe durable media-group semantics."""

import asyncio
from datetime import UTC, datetime, timedelta

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.ports import MediaGroupMember
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import TelegramEntity
from tests.unit.application.m2_fakes import FakePreparationRepository


def member(message_id: int, at: datetime) -> MediaGroupMember:
    """Build a Persian-caption group member."""
    media = StoredMedia(
        MediaIdentity(-1, message_id),
        MediaType.PHOTO,
        f"{message_id:064x}",
        1,
        None,
        None,
        f"sha/{message_id}",
        at + timedelta(days=14),
    )
    return MediaGroupMember(
        message_id,
        at,
        media,
        "کپشن‌فارسی 😀",
        (TelegramEntity(13, 2, "custom_emoji", "1"),),
    )


def test_out_of_order_replay_finalize_and_late_member() -> None:
    repository = FakePreparationRepository()
    use_case = AssembleMediaGroup(
        repository,
        quiet_window=timedelta(seconds=2),
        maximum_wait=timedelta(seconds=10),
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    async def scenario() -> None:
        group = await use_case.add_member(
            source_channel_id=-1,
            telegram_group_id="g",
            member=member(2, now + timedelta(seconds=1)),
        )
        group = await use_case.add_member(
            source_channel_id=-1, telegram_group_id="g", member=member(1, now)
        )
        group = await use_case.add_member(
            source_channel_id=-1, telegram_group_id="g", member=member(1, now)
        )
        assert [item.source_message_id for item in group.members] == [1, 2]
        assert not await use_case.finalize_if_due(
            group.group_key, now=now + timedelta(seconds=2)
        )
        results = await asyncio.gather(
            *(
                use_case.finalize_if_due(
                    group.group_key, now=now + timedelta(seconds=3)
                )
                for _ in range(2)
            )
        )
        assert results.count(True) == 1
        late = await use_case.add_member(
            source_channel_id=-1,
            telegram_group_id="g",
            member=member(3, now + timedelta(seconds=4)),
        )
        assert len(late.members) == 2

    asyncio.run(scenario())
