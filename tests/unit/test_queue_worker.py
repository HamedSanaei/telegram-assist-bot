"""Unit tests for the queue worker dispatch/retry logic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.domain.entities import QueueItem
from src.domain.enums import QueueItemType, QueueStatus
from src.workers.queue_worker import QueueWorker
from tests.unit.application.fakes import FakeQueueRepository


class TestQueueWorker:
    """Tests for :class:`QueueWorker`."""

    async def test_successful_item_gets_handler_status(self) -> None:
        queue = FakeQueueRepository()
        await queue.enqueue(QueueItemType.APPROVAL_REQUEST, {"post_id": "p1"})

        async def handler(item: QueueItem) -> QueueStatus:
            return QueueStatus.WAITING_APPROVAL

        worker = QueueWorker(queue, {QueueItemType.APPROVAL_REQUEST: handler})
        item = await queue.claim_next_due(datetime.now(timezone.utc))
        await worker.process_item(item)
        assert queue.items[0].status == QueueStatus.WAITING_APPROVAL

    async def test_failed_item_is_rescheduled(self) -> None:
        queue = FakeQueueRepository()
        await queue.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})

        async def handler(item: QueueItem) -> QueueStatus:
            raise RuntimeError("boom")

        worker = QueueWorker(queue, {QueueItemType.VPN_TEST: handler}, max_attempts=5)
        item = await queue.claim_next_due(datetime.now(timezone.utc))
        await worker.process_item(item)
        assert queue.items[0].status == QueueStatus.PENDING
        assert "boom" in queue.items[0].last_error

    async def test_exhausted_item_is_failed(self) -> None:
        queue = FakeQueueRepository()
        await queue.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})

        async def handler(item: QueueItem) -> QueueStatus:
            raise RuntimeError("boom")

        worker = QueueWorker(queue, {QueueItemType.VPN_TEST: handler}, max_attempts=1)
        item = await queue.claim_next_due(datetime.now(timezone.utc))
        await worker.process_item(item)
        assert queue.items[0].status == QueueStatus.FAILED

    async def test_unknown_type_is_failed(self) -> None:
        queue = FakeQueueRepository()
        await queue.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        worker = QueueWorker(queue, handlers={})
        item = await queue.claim_next_due(datetime.now(timezone.utc))
        await worker.process_item(item)
        assert queue.items[0].status == QueueStatus.FAILED

    async def test_transient_claim_error_does_not_stop_worker(self) -> None:
        """A locked SQLite claim is retried without crashing the main process."""

        class FlakyClaimQueue(FakeQueueRepository):
            """Queue fake that fails its first claim and then becomes idle."""

            def __init__(self) -> None:
                super().__init__()
                self.calls = 0
                self.retried = asyncio.Event()

            async def claim_next_due(
                self,
                now: datetime,
                item_types: set[QueueItemType] | None = None,
            ) -> QueueItem | None:
                """Raise once, then report an empty queue."""
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("database is locked")
                self.retried.set()
                return await super().claim_next_due(now, item_types)

        queue = FlakyClaimQueue()
        worker = QueueWorker(queue, {}, poll_interval_seconds=0.01)
        task = asyncio.create_task(worker.run())

        await asyncio.wait_for(queue.retried.wait(), timeout=1)
        worker.stop()
        await asyncio.wait_for(task, timeout=1)

        assert queue.calls >= 2
