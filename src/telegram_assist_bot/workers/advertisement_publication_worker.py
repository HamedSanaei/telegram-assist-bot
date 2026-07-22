"""Isolated polling worker seam for due advertisement publications."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram_assist_bot.application.advertisements.publish_advertisement_slot import (  # noqa: E501
        AdvertisementPublicationContext,
        PublishAdvertisementSlot,
    )


class AdvertisementPublicationWorker:
    """Poll one T051 use case without adding operational runtime wiring."""

    def __init__(
        self,
        use_case: PublishAdvertisementSlot,
        context: AdvertisementPublicationContext,
        *,
        poll_seconds: float,
    ) -> None:
        """Store one isolated use case and explicit positive poll interval."""
        if poll_seconds <= 0:
            raise ValueError("advertisement worker poll interval must be positive")
        self._use_case = use_case
        self._context = context
        self._poll_seconds = poll_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Run until cancellation or a caller-owned stop signal."""
        while not stop.is_set():
            await self._use_case.execute_once(self._context)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                continue


__all__ = ("AdvertisementPublicationWorker",)
