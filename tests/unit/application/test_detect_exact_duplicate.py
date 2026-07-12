"""Verify fourteen-day exact duplicate decisions and canonical persistence."""

import asyncio
from datetime import UTC, datetime, timedelta

from telegram_assist_bot.application.detect_exact_duplicate import DetectExactDuplicate
from telegram_assist_bot.domain.posts import PostId
from tests.unit.application.m2_fakes import FakePreparationRepository


def test_window_self_exclusion_and_restart_reuse() -> None:
    repository = FakePreparationRepository()
    use_case = DetectExactDuplicate(repository)
    now = datetime(2026, 1, 20, tzinfo=UTC)

    async def scenario() -> None:
        original = await use_case.execute(
            post_id=PostId("p1"),
            text="سلام",
            caption=None,
            media_hashes=(),
            checked_at=now - timedelta(days=1),
        )
        repeated = await use_case.execute(
            post_id=PostId("p2"),
            text="سلام",
            caption=None,
            media_hashes=(),
            checked_at=now,
        )
        assert not original.is_duplicate
        assert repeated.matched_post_id == PostId("p1")
        assert (
            await use_case.execute(
                post_id=PostId("p2"),
                text="تغییر",
                caption=None,
                media_hashes=(),
                checked_at=now,
            )
            == repeated
        )

    asyncio.run(scenario())


def test_match_outside_fourteen_day_boundary_is_excluded() -> None:
    repository = FakePreparationRepository()
    use_case = DetectExactDuplicate(repository)
    now = datetime(2026, 2, 1, tzinfo=UTC)

    async def scenario() -> None:
        await use_case.execute(
            post_id=PostId("old"),
            text="همان",
            caption=None,
            media_hashes=(),
            checked_at=now - timedelta(days=14, microseconds=1),
        )
        current = await use_case.execute(
            post_id=PostId("new"),
            text="همان",
            caption=None,
            media_hashes=(),
            checked_at=now,
        )
        assert not current.is_duplicate

    asyncio.run(scenario())
