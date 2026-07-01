"""Restart-safe background worker processing the SQLite job queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from src.domain.entities import QueueItem
from src.domain.enums import QueueItemType, QueueStatus
from src.domain.interfaces import QueueRepository
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

QueueHandler = Callable[[QueueItem], Awaitable[QueueStatus]]


class QueueWorker:
    """
    Polls the queue, claims due items atomically, and dispatches them
    to type-specific handlers.

    Items are claimed with a conditional UPDATE so the same item is
    never processed twice concurrently; failed items are retried with
    linear backoff until ``max_attempts`` and then marked failed.

    Example:
        worker = QueueWorker(queue, {QueueItemType.VPN_TEST: handle_vpn_test})
        await worker.run()
    """

    def __init__(
        self,
        queue: QueueRepository,
        handlers: dict[QueueItemType, QueueHandler],
        poll_interval_seconds: float = 2.0,
        max_attempts: int = 5,
        retry_delay_seconds: int = 60,
    ) -> None:
        """
        Args:
            queue: The queue repository.
            handlers: Mapping of item type to async handler. A handler
                returns the final :class:`QueueStatus` for the item.
            poll_interval_seconds: Sleep time when the queue is empty.
            max_attempts: Attempts before an item is marked failed.
            retry_delay_seconds: Base delay between retries (multiplied
                by the attempt number).
        """
        self._queue = queue
        self._handlers = handlers
        self._poll_interval = poll_interval_seconds
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay_seconds
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        """Request a graceful stop after the current item finishes."""
        self._stopped.set()

    async def run(self) -> None:
        """
        Run the worker loop until :meth:`stop` is called.

        Side effects:
            Continuously claims and processes queue items.
        """
        logger.info("Queue worker started handlers=%s", list(self._handlers))
        while not self._stopped.is_set():
            item = await self._queue.claim_next_due(datetime.now(timezone.utc))
            if item is None:
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(), timeout=self._poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            await self.process_item(item)
        logger.info("Queue worker stopped")

    async def process_item(self, item: QueueItem) -> None:
        """
        Process one claimed item and persist its resulting status.

        Args:
            item: The claimed queue item (already in ``processing`` state).
        """
        handler = self._handlers.get(item.type)
        if handler is None:
            logger.error("No handler for queue item type=%s id=%s", item.type, item.id)
            await self._queue.mark_status(
                item.id, QueueStatus.FAILED, f"No handler for type {item.type.value}"
            )
            return
        try:
            status = await handler(item)
        except Exception as exc:
            logger.exception(
                "Queue item failed id=%s type=%s attempt=%d", item.id, item.type, item.attempts
            )
            if item.attempts >= self._max_attempts:
                await self._queue.mark_status(item.id, QueueStatus.FAILED, str(exc))
            else:
                retry_at = datetime.now(timezone.utc) + timedelta(
                    seconds=self._retry_delay * item.attempts
                )
                await self._queue.reschedule(item.id, retry_at, str(exc))
            return
        await self._queue.mark_status(item.id, status)
        logger.info("Queue item done id=%s type=%s status=%s", item.id, item.type, status.value)
