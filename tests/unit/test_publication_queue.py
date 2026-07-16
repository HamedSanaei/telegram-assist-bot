"""Verify safe queue inspection and explicit cancellation composition."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

import telegram_assist_bot.bootstrap.publication_queue as module
from telegram_assist_bot.domain import CancellationResult, ScheduledPublication

if TYPE_CHECKING:
    from telegram_assist_bot.application.scheduling import CancelRequest


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class Cursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    def sort(self, values: object) -> Cursor:
        del values
        return self

    def __aiter__(self) -> Cursor:
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self) -> dict[str, object]:
        try:
            return next(self._iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error


class Collection:
    def __init__(
        self,
        documents: list[dict[str, object]] | None = None,
        found: dict[str, object] | None = None,
    ) -> None:
        self.documents = documents or []
        self.found = found
        self.query: dict[str, object] | None = None
        self.projection: dict[str, object] | None = None

    def find(
        self, query: dict[str, object], *, projection: dict[str, object]
    ) -> Cursor:
        self.query = query
        self.projection = projection
        return Cursor(self.documents)

    async def find_one(
        self, query: dict[str, object], *, projection: dict[str, object]
    ) -> dict[str, object] | None:
        self.query = query
        self.projection = projection
        return self.found


class Foundation:
    def __init__(self, database: dict[str, object]) -> None:
        destination = SimpleNamespace(telegram_channel_id=-1001, name="kingofilter")
        publishing = SimpleNamespace(
            scheduled_publication_interval_seconds=300,
            cancellation_policy="preserve",
        )
        settings = SimpleNamespace(
            mongodb=SimpleNamespace(database_name="test"),
            destination_channels=(destination,),
            publishing=publishing,
        )
        self.configuration = SimpleNamespace(settings=settings)
        self.mongodb_client = {"test": database}
        self.started = 0
        self.stopped = 0

    async def start(self, path: Path, *, environ: object) -> None:
        del path, environ
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1


def test_inspection_projects_and_renders_only_safe_queue_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules = Collection(
        [
            {
                "_id": "job-safe",
                "post_id": "post-identity-long-value",
                "destination_id": -1001,
                "action": "immediate",
                "status": "Pending",
                "due_at": NOW,
                "attempt_count": 2,
                "private_metadata": "must-not-render",
                "media_path": "must-not-render.jpg",
            }
        ]
    )
    posts = Collection(found={"source_message_id": 133594, "text": "محرمانه"})
    foundation = Foundation(
        {
            "scheduled_publications": schedules,
            "posts": posts,
        }
    )
    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )

    rows = asyncio.run(
        module.inspect_publication_queue(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            status="pending",
        )
    )

    assert foundation.started == foundation.stopped == 1
    assert schedules.query == {"status": "Pending"}
    assert schedules.projection is not None
    assert "private_metadata" not in schedules.projection
    assert "job_id=job-safe" in rows[0]
    assert "post_id=post-identit" in rows[0]
    assert "source_message_id=133594" in rows[0]
    assert "destination=kingofilter" in rows[0]
    assert "must-not-render" not in rows[0]
    assert "محرمانه" not in rows[0]


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (None, CancellationResult.NOT_FOUND),
        (
            ScheduledPublication("job-1", "post-1", -1001, NOW),
            CancellationResult.ALREADY_CANCELLED,
        ),
    ],
)
def test_cancellation_is_explicit_idempotent_and_closes_foundation(
    monkeypatch: pytest.MonkeyPatch,
    job: ScheduledPublication | None,
    expected: CancellationResult,
) -> None:
    foundation = Foundation(
        {"scheduled_publications": object(), "schedule_queues": object()}
    )
    requests: list[CancelRequest] = []

    class Repository:
        def __init__(self, *args: object) -> None:
            del args

        async def get(self, job_id: str) -> ScheduledPublication | None:
            assert job_id == "job-1"
            return job

    class Cancel:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def execute(self, request: CancelRequest) -> CancellationResult:
            requests.append(request)
            return CancellationResult.ALREADY_CANCELLED

    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )
    monkeypatch.setattr(module, "MongoScheduleRepository", Repository)
    monkeypatch.setattr(module, "CancelScheduledPost", Cancel)

    result = asyncio.run(
        module.cancel_publication_job(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            job_id="job-1",
        )
    )

    assert result is expected
    assert foundation.started == foundation.stopped == 1
    assert len(requests) == (0 if job is None else 1)
    if requests:
        assert requests[0].job_id == "job-1"
        assert requests[0].authorized


def test_failed_immediate_recovery_wrapper_closes_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation({})
    captured: dict[str, object] = {}

    async def recover(database: object, **values: object) -> object:
        captured["database"] = database
        captured.update(values)
        return module.PreSendRecoveryResult.DRY_RUN_ELIGIBLE

    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )
    monkeypatch.setattr(module, "_recover_failed_immediate_in_database", recover)
    result = asyncio.run(
        module.recover_failed_immediate_selection(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            approval_post_id="post-1",
            dry_run=True,
            requeue=False,
        )
    )
    assert result is module.PreSendRecoveryResult.DRY_RUN_ELIGIBLE
    assert foundation.started == 1
    assert foundation.stopped == 1
    assert captured["approval_post_id"] == "post-1"
    assert captured["dry_run"] is True
    assert captured["requeue"] is False
