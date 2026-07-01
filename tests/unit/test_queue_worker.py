"""Unit tests for the queue worker dispatch/retry logic."""

from __future__ import annotations

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
