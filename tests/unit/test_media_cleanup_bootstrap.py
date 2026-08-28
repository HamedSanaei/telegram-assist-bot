"""Verify the one-shot media-cleanup Composition Root and exit contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import telegram_assist_bot.bootstrap.media_cleanup as cleanup_module
from telegram_assist_bot.application.cleanup_expired_media import CleanupBatchResult
from telegram_assist_bot.bootstrap.media_cleanup import (
    run_media_cleanup,
    run_media_cleanup_worker,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from telegram_assist_bot.bootstrap.runtime import FoundationApplication
    from telegram_assist_bot.shared.observability import EventSink


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class Logger:
    """Capture safe cleanup events."""

    events: list[dict[str, object]] = field(default_factory=list)

    def emit(self, **event: object) -> None:
        self.events.append(dict(event))


@dataclass
class Foundation:
    """Expose only the already-owned resources used by cleanup composition."""

    failure: BaseException | None = None
    shutdowns: int = 0
    logger_value: Logger = field(default_factory=Logger)

    @property
    def configuration(self) -> object:
        media = SimpleNamespace(
            root=Path("synthetic-media"),
            orphan_grace_seconds=60,
            cleanup_batch_size=10,
            cleanup_interval_seconds=3600,
            cleanup_max_batches_per_cycle=10,
            cleanup_defer_seconds=3600,
        )
        return SimpleNamespace(
            settings=SimpleNamespace(
                mongodb=SimpleNamespace(database_name="synthetic"), media=media
            )
        )

    @property
    def mongodb_client(self) -> dict[str, dict[str, object]]:
        return {
            "synthetic": {
                "media_items": object(),
                "media_groups": object(),
                "content_preparations": object(),
                "posts": object(),
                "publications": object(),
                "scheduled_publications": object(),
                "native_schedule_commands": object(),
                "approval_deliveries": object(),
                "advertisement_sources": object(),
                "advertisement_slots": object(),
            }
        }

    @property
    def logger(self) -> Logger:
        return self.logger_value

    async def start(self, path: Path, *, environ: object) -> None:
        del path, environ
        if self.failure is not None:
            raise self.failure

    async def shutdown(self) -> None:
        self.shutdowns += 1


class Cleanup:
    """Return or raise one injected cleanup result."""

    result: CleanupBatchResult | BaseException = CleanupBatchResult(deleted=3)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def execute(self, *, now: object) -> CleanupBatchResult:
        del now
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def setup(monkeypatch: pytest.MonkeyPatch, foundation: Foundation) -> None:
    """Replace concrete boundaries while retaining cleanup orchestration."""
    monkeypatch.setattr(
        cleanup_module,
        "create_foundation_application",
        lambda *, sink: cast("FoundationApplication", foundation),
    )

    async def indexes(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        cleanup_module, "initialize_content_preparation_indexes", indexes
    )
    monkeypatch.setattr(
        cleanup_module,
        "MongoContentPreparationRepository",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(cleanup_module, "LocalMediaStorage", lambda _root: object())
    monkeypatch.setattr(cleanup_module, "CleanupExpiredMedia", Cleanup)


def execute() -> FoundationExitCode:
    """Run cleanup with a fully synthetic safe boundary."""
    return run(
        run_media_cleanup(
            Path("synthetic.json"),
            environ={},
            sink=cast("EventSink", lambda _event: None),
        )
    )


def test_cleanup_success_emits_metrics_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)
    Cleanup.result = CleanupBatchResult(
        scanned=10,
        deleted=3,
        deferred=2,
        orphan_deleted=1,
        temporary_deleted=1,
        failed=0,
        deleted_bytes=100,
        orphan_deleted_bytes=50,
    )

    assert execute() is FoundationExitCode.SUCCESS
    assert foundation.shutdowns == 1
    assert foundation.logger.events[-1]["event_name"] == "media_cleanup_completed"
    assert foundation.logger.events[-1]["fields"] == {
        "scanned": 10,
        "deleted": 3,
        "deferred": 2,
        "orphan_deleted": 1,
        "temporary_deleted": 1,
        "failed": 0,
        "deleted_bytes": 100,
        "orphan_deleted_bytes": 50,
    }


def test_cleanup_maps_startup_and_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup = Foundation(failure=FoundationConfigurationError())
    setup(monkeypatch, startup)
    assert execute() is FoundationExitCode.CONFIGURATION_ERROR
    assert startup.shutdowns == 1

    runtime = Foundation()
    setup(monkeypatch, runtime)
    Cleanup.result = RuntimeError("synthetic unsafe detail")
    assert execute() is FoundationExitCode.INFRASTRUCTURE_ERROR
    assert runtime.shutdowns == 1
    assert runtime.logger.events[-1]["event_name"] == "media_cleanup_failed"


def test_cleanup_propagates_cancellation_after_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)
    Cleanup.result = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        execute()
    assert foundation.shutdowns == 1


def test_cleanup_candidate_error_emits_safe_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)
    captured: dict[str, object] = {}

    class CapturingCleanup(Cleanup):
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            super().__init__(*_args, **_kwargs)
            captured.update(_kwargs)

    monkeypatch.setattr(cleanup_module, "CleanupExpiredMedia", CapturingCleanup)
    Cleanup.result = CleanupBatchResult()

    assert execute() is FoundationExitCode.SUCCESS
    callback = cast(
        "Callable[[object, object, int], None]", captured["on_candidate_error"]
    )
    item = SimpleNamespace(
        identity=SimpleNamespace(source_channel_id=-5, source_message_id=7)
    )
    callback(item, ValueError("synthetic"), 2)
    warning = foundation.logger.events[-1]
    assert warning["event_name"] == "media_cleanup_candidate_failed"
    assert warning["level"] == "WARNING"
    assert warning["fields"] == {
        "retry_attempt": 2,
        "source_channel_id": -5,
        "source_message_id": 7,
    }


def test_periodic_cleanup_worker_failure_maps_to_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)

    class FailingWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def run(self, stop_event: asyncio.Event) -> int:
            del stop_event
            raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(cleanup_module, "PeriodicMediaCleanupWorker", FailingWorker)
    stop = asyncio.Event()
    stop.set()
    result = run(
        run_media_cleanup_worker(
            Path("synthetic.json"),
            environ={},
            sink=cast("EventSink", lambda _event: None),
            stop_event=stop,
        )
    )

    assert result is FoundationExitCode.INFRASTRUCTURE_ERROR
    assert foundation.logger.events[-1]["event_name"] == "media_cleanup_worker_failed"


def test_periodic_cleanup_uses_configured_interval_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)
    intervals: list[int] = []
    batches: list[int] = []

    class Worker:
        def __init__(
            self,
            _use_case: object,
            _clock: object,
            *,
            interval_seconds: int,
            max_batches_per_cycle: int,
            on_batch_completed: object | None = None,
        ) -> None:
            intervals.append(interval_seconds)
            batches.append(max_batches_per_cycle)
            del on_batch_completed

        async def run(self, stop_event: asyncio.Event) -> int:
            assert stop_event.is_set()
            return 2

    monkeypatch.setattr(cleanup_module, "PeriodicMediaCleanupWorker", Worker)
    stop = asyncio.Event()
    stop.set()
    result = run(
        run_media_cleanup_worker(
            Path("synthetic.json"),
            environ={},
            sink=cast("EventSink", lambda _event: None),
            stop_event=stop,
        )
    )

    assert result is FoundationExitCode.SUCCESS
    assert intervals == [3600]
    assert batches == [10]
    assert foundation.shutdowns == 1
    assert foundation.logger.events[-1]["event_name"] == (
        "media_cleanup_worker_stopped"
    )
