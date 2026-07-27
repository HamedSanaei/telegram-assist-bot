"""Approval scoring fan-out boundary tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from telegram_assist_bot.application.scoring_approval import ApprovalScoringFanout


class Synchronizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, datetime]] = []

    async def synchronize_scoring_header(
        self,
        *,
        post_id: str,
        version: int,
        now: datetime,
    ) -> None:
        self.calls.append((post_id, version, now))


def test_scoring_fanout_preserves_cas_version_and_explicit_time() -> None:
    synchronizer = Synchronizer()
    fanout = ApprovalScoringFanout(synchronizer)
    now = datetime(2026, 7, 27, 12, 30, tzinfo=UTC)

    asyncio.run(fanout.execute(post_id="post-1", version=4, now=now))

    assert synchronizer.calls == [("post-1", 4, now)]
