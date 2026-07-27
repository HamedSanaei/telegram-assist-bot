# mypy: disable-error-code="arg-type"
"""Behavioral tests for the isolated advertisement publication poller."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from telegram_assist_bot.workers.advertisement_publication_worker import (
    AdvertisementPublicationWorker,
)


class UseCase:
    def __init__(self, stop: asyncio.Event, *, stop_after: int) -> None:
        self.stop = stop
        self.stop_after = stop_after
        self.calls = 0

    async def execute_once(self, context: object) -> None:
        assert context == {"source": "test"}
        self.calls += 1
        if self.calls == self.stop_after:
            self.stop.set()


def test_worker_requires_positive_poll_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        AdvertisementPublicationWorker(
            cast("object", UseCase(asyncio.Event(), stop_after=1)),
            cast("object", {}),
            poll_seconds=0,
        )


def test_worker_polls_until_stop_and_returns_without_extra_iteration() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        use_case = UseCase(stop, stop_after=2)
        worker = AdvertisementPublicationWorker(
            cast("object", use_case),
            cast("object", {"source": "test"}),
            poll_seconds=0.001,
        )

        await worker.run(stop)

        assert use_case.calls == 2

    asyncio.run(scenario())
