"""Cancellation-safe polling loop for durable scheduled publications."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ScheduledPublicationWorker:
    """Poll one injected iteration without retaining queue state in memory."""

    def __init__(
        self,
        run_once: Callable[[], Awaitable[object]],
        *,
        poll_seconds: float,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Store an injected iteration and bounded polling delay."""
        if poll_seconds <= 0 or poll_seconds > 300:
            raise ValueError("Schedule poll interval is invalid.")
        self._run_once = run_once
        self._poll_seconds = poll_seconds
        self._sleeper = sleeper

    async def run(self) -> None:
        """Run until cancellation and propagate cancellation immediately."""
        while True:
            await self._run_once()
            await self._sleeper(self._poll_seconds)


__all__ = ("ScheduledPublicationWorker",)
