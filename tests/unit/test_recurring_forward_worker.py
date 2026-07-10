"""Unit tests for recurring Telegram native schedule reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.domain.entities import RecurringForwardOccurrence
from src.workers.recurring_forward import RecurringForwardWorker


class FakeOccurrenceRepository:
    """In-memory unique occurrence store for worker tests."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, int, datetime], RecurringForwardOccurrence] = {}

    async def reserve(self, occurrence: RecurringForwardOccurrence) -> int | None:
        key = (occurrence.campaign_id, occurrence.destination_chat_id, occurrence.scheduled_at)
        if key in self.rows:
            return None
        occurrence_id = len(self.rows) + 1
        self.rows[key] = RecurringForwardOccurrence(**{**occurrence.__dict__, "id": occurrence_id})
        return occurrence_id

    async def mark_scheduled(self, occurrence_id: int, message_ids: list[int]) -> None:
        for key, value in list(self.rows.items()):
            if value.id == occurrence_id:
                self.rows[key] = RecurringForwardOccurrence(
                    **{**value.__dict__, "status": "scheduled", "message_ids": tuple(message_ids)}
                )

    async def mark_failed(self, occurrence_id: int, error: str) -> None:
        return None

    async def list_future_scheduled(
        self, now: datetime
    ) -> list[RecurringForwardOccurrence]:
        return [
            value
            for value in self.rows.values()
            if value.status == "scheduled"
            and value.scheduled_at > now
        ]

    async def mark_cancelled(self, occurrence_id: int) -> None:
        for key, value in list(self.rows.items()):
            if value.id == occurrence_id:
                self.rows[key] = RecurringForwardOccurrence(
                    **{**value.__dict__, "status": "cancelled"}
                )


class FakeRecurringPublisher:
    """Records scheduled and deleted recurring messages."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, int, bool, datetime]] = []
        self.deleted: list[tuple[int, list[int]]] = []

    async def schedule_from_url(
        self, source_post_url: str, destination_chat_id: int,
        show_forward_header: bool, scheduled_at: datetime
    ) -> list[int]:
        self.scheduled.append(
            (source_post_url, destination_chat_id, show_forward_header, scheduled_at)
        )
        return [100 + len(self.scheduled)]

    async def delete_scheduled_messages(
        self, destination_chat_id: int, message_ids: list[int]
    ) -> None:
        self.deleted.append((destination_chat_id, message_ids))


def _write_config(path, enabled: bool) -> None:
    """Write one valid recurring campaign config."""
    path.write_text(
        json.dumps(
            {
                "telegram": {},
                "ai": {},
                "database": {},
                "scheduler": {
                    "timezone": "Asia/Tehran",
                    "recurring_forward_lookahead_hours": 24,
                    "recurring_forwards": [
                        {
                            "id": "daily_ad",
                            "enabled": enabled,
                            "source_post_url": "https://t.me/source/10",
                            "destination_chat_ids": [-1001],
                            "show_forward_header": False,
                            "times": ["09:00"],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def test_worker_schedules_once_and_cancels_disabled_campaign(tmp_path) -> None:
    """Restart reconciliation is idempotent and disable removes future schedule."""
    path = tmp_path / "configuration.json"
    _write_config(path, enabled=True)
    repository = FakeOccurrenceRepository()
    publisher = FakeRecurringPublisher()
    worker = RecurringForwardWorker(repository, publisher, str(path))
    now = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)

    assert await worker.reconcile(now) == 1
    assert await worker.reconcile(now) == 0
    assert len(publisher.scheduled) == 1

    _write_config(path, enabled=False)
    await worker.reconcile(now)

    assert publisher.deleted == [(-1001, [101])]
