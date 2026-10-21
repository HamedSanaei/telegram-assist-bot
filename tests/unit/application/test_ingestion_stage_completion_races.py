"""Recover completion crash gaps without stealing leases or repeating media work."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.domain.media import MediaType
from tests.unit.application.m2_fakes import FakePreparationRepository
from tests.unit.application.test_runtime_ingestion import Clock, coordinator, message

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from telegram_assist_bot.domain.posts import PostId


def test_ready_recovery_request_waits_for_competing_lease_before_acknowledgement(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        fixture = message(1, clock.now, media_type=MediaType.PHOTO)
        result = await runtime.execute(fixture, correlation_id="prepared")
        repository.pending[result.post_id.value] = None
        repository.leases[result.post_id.value] = (
            "competing",
            clock.now + timedelta(minutes=5),
        )
        await runtime.execute(fixture, correlation_id="competing-ready")
        assert result.post_id.value in repository.pending
        assert repository.leases[result.post_id.value][0] == "competing"
        assert source.opens == 1
        clock.now += timedelta(minutes=6)
        await runtime.execute(fixture, correlation_id="expired-owner")
        assert result.post_id.value not in repository.pending
        assert source.opens == 1

    asyncio.run(scenario())


def test_registered_album_member_crash_gap_is_acknowledged_without_readiness(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        first = message(1, clock.now, media_type=MediaType.PHOTO, group_id="album")
        second = message(2, clock.now, media_type=MediaType.PHOTO, group_id="album")
        result = await runtime.execute(first, correlation_id="first-member")
        await runtime.execute(second, correlation_id="second-member")
        repository.pending[result.post_id.value] = None
        await runtime.execute(first, correlation_id="registered-before-crash")
        assert result.post_id.value not in repository.pending
        assert not repository.ready
        assert source.opens == 2
        clock.now += timedelta(seconds=3)
        assert await runtime.finalize_due_groups() == 1
        assert len(repository.ready) == 1

    asyncio.run(scenario())


def test_readiness_committed_between_lookup_and_claim_does_not_redownload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        fixture = message(1, clock.now, media_type=MediaType.PHOTO)
        result = await runtime.execute(fixture, correlation_id="prepared")
        repository.ready.remove(result.post_id.value)
        repository.ready_at.pop(result.post_id.value)
        repository.outbox_recorded.discard(result.post_id.value)
        repository.pending[result.post_id.value] = None
        original_claim = repository.claim_ingestion

        async def claim_and_finish(
            _self: FakePreparationRepository,
            post_id: PostId,
            *,
            owner: str,
            now: datetime,
            lease_until: datetime,
        ) -> bool:
            claimed = await original_claim(
                post_id, owner=owner, now=now, lease_until=lease_until
            )
            if claimed:
                repository.ready.add(post_id.value)
                repository.ready_at[post_id.value] = now
            return claimed

        monkeypatch.setattr(
            FakePreparationRepository, "claim_ingestion", claim_and_finish
        )
        await runtime.execute(fixture, correlation_id="concurrent-completion")
        assert source.opens == 1
        assert result.post_id.value not in repository.pending
        assert result.post_id.value in repository.outbox_recorded

    asyncio.run(scenario())
