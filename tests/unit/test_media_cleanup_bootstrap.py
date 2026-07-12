"""Verify the one-shot media-cleanup Composition Root and exit contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import telegram_assist_bot.bootstrap.media_cleanup as cleanup_module
from telegram_assist_bot.bootstrap.media_cleanup import run_media_cleanup
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

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

    result: int | BaseException = 3

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def execute(self, *, now: object) -> int:
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
        cleanup_module, "MongoContentPreparationRepository", lambda *_args: object()
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


def test_cleanup_success_emits_count_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation()
    setup(monkeypatch, foundation)
    Cleanup.result = 3

    assert execute() is FoundationExitCode.SUCCESS
    assert foundation.shutdowns == 1
    assert foundation.logger.events[-1]["event_name"] == "media_cleanup_completed"
    assert foundation.logger.events[-1]["fields"] == {"cleaned_item_count": 3}


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
