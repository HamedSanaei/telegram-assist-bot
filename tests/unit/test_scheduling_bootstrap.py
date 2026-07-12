"""Verify scheduling composition has inert construction and exact cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import telegram_assist_bot.bootstrap.scheduling as scheduling
from telegram_assist_bot.bootstrap.runtime import FoundationExitCode

if TYPE_CHECKING:
    from pathlib import Path


class Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class Secrets:
    def get(self, reference: object) -> Secret:
        return Secret("123" if reference == "api-id" else "synthetic-secret")


class Database(dict[str, object]):
    def __missing__(self, key: str) -> object:
        value = object()
        self[key] = value
        return value


class MongoClient:
    def __init__(self) -> None:
        self.database = Database()

    def __getitem__(self, _name: str) -> Database:
        return self.database


class Foundation:
    def __init__(self, root: Path) -> None:
        publishing = SimpleNamespace(
            operation_timeout_seconds=10,
            publication_lease_seconds=20,
            publication_max_attempts=3,
            retry_initial_delay_seconds=1,
            retry_maximum_delay_seconds=10,
            worker_poll_seconds=1,
        )
        self.configuration = SimpleNamespace(
            settings=SimpleNamespace(
                telegram=SimpleNamespace(
                    user=SimpleNamespace(
                        session_path=root / "account.session",
                        api_id="api-id",
                        api_hash="api-hash",
                    )
                ),
                publishing=publishing,
                mongodb=SimpleNamespace(database_name="test"),
                destination_channels=(
                    SimpleNamespace(
                        telegram_channel_id=-1001, name="destination", enabled=True
                    ),
                ),
                media=SimpleNamespace(root=root / "media"),
            ),
            secrets=Secrets(),
        )
        self.mongodb_client = MongoClient()
        self.starts = self.stops = 0

    async def start(self, _path: Path, *, environ: object) -> None:
        self.starts += 1

    async def shutdown(self) -> None:
        self.stops += 1


class Session:
    closes = 0

    def __init__(self, **_values: object) -> None:
        pass

    async def open_authorized_client(self) -> object:
        return object()

    async def close(self) -> None:
        Session.closes += 1


class Worker:
    runs = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def run(self) -> None:
        Worker.runs += 1


def install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def validate(_settings: object, _session: object) -> object:
        channel = SimpleNamespace(channel_id=-1001)
        return SimpleNamespace(
            channels=(
                SimpleNamespace(
                    channel=channel, role=SimpleNamespace(value="Destination")
                ),
            )
        )

    async def initialize(*_values: object) -> None:
        return None

    class Factory:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class Loader(Factory):
        async def load(self, _post_id: str, destination_id: int) -> object:
            return SimpleNamespace(destination_id=destination_id)

    class Publication(Factory):
        async def execute(self, _request: object) -> object:
            return object()

    class Runner(Factory):
        async def execute_once(self) -> object:
            return object()

    monkeypatch.setattr(scheduling, "TelethonSessionAdapter", Session)
    monkeypatch.setattr(scheduling, "validate_telegram_startup", validate)
    monkeypatch.setattr(scheduling, "initialize_publication_indexes", initialize)
    monkeypatch.setattr(
        scheduling, "initialize_content_preparation_indexes", initialize
    )
    monkeypatch.setattr(scheduling, "MongoContentPreparationRepository", Factory)
    monkeypatch.setattr(scheduling, "MongoPublicationPayloadLoader", Loader)
    monkeypatch.setattr(scheduling, "TelethonPublisherGateway", Factory)
    monkeypatch.setattr(scheduling, "MongoPublicationRepository", Factory)
    monkeypatch.setattr(scheduling, "MongoScheduleRepository", Factory)
    monkeypatch.setattr(scheduling, "PublishImmediately", Publication)
    monkeypatch.setattr(scheduling, "RunDuePublication", Runner)
    monkeypatch.setattr(scheduling, "ScheduledPublicationWorker", Worker)


def test_start_wait_and_idempotent_reverse_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Session.closes = Worker.runs = 0
    install_fakes(monkeypatch)
    foundation = Foundation(tmp_path)
    application = scheduling.ScheduleWorkerApplication(foundation)  # type: ignore[arg-type]

    async def scenario() -> None:
        await application.start(tmp_path / "config.json", environ={})
        await application.wait()
        await application.shutdown()
        await application.shutdown()

    asyncio.run(scenario())
    assert foundation.starts == 1
    assert foundation.stops == 1
    assert Session.closes == 1
    assert Worker.runs == 1
    assert (tmp_path / "media").is_dir()


def test_startup_cancellation_closes_owned_foundation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fakes(monkeypatch)
    foundation = Foundation(tmp_path)

    async def cancelled(_settings: object, _session: object) -> object:
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduling, "validate_telegram_startup", cancelled)
    application = scheduling.ScheduleWorkerApplication(foundation)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(application.start(tmp_path / "config.json", environ={}))
    assert foundation.stops == 1


def test_inert_factory_only_constructs_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(
        scheduling, "create_foundation_application", lambda **_values: sentinel
    )
    application = scheduling.create_schedule_worker_application(sink=object())  # type: ignore[arg-type]
    assert isinstance(application, scheduling.ScheduleWorkerApplication)


class RunnableApplication:
    """Script operational wrapper success, failure, and cancellation."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.stops = 0

    async def start(self, _path: Path, *, environ: object) -> None:
        if self.failure is not None:
            raise self.failure

    async def wait(self) -> None:
        return None

    async def shutdown(self) -> None:
        self.stops += 1


def test_operational_wrapper_returns_success_and_always_shuts_down(
    tmp_path: Path,
) -> None:
    application = RunnableApplication()
    result = asyncio.run(
        scheduling.run_schedule_worker_application(
            cast("scheduling.ScheduleWorkerApplication", application),
            tmp_path / "config.json",
            environ={},
        )
    )
    assert result is FoundationExitCode.SUCCESS
    assert application.stops == 1


def test_operational_wrapper_maps_safe_startup_failure(tmp_path: Path) -> None:
    application = RunnableApplication(scheduling.ScheduleWorkerStartupError())
    result = asyncio.run(
        scheduling.run_schedule_worker_application(
            cast("scheduling.ScheduleWorkerApplication", application),
            tmp_path / "config.json",
            environ={},
        )
    )
    assert result is FoundationExitCode.INFRASTRUCTURE_ERROR
    assert application.stops == 1


def test_operational_wrapper_propagates_cancellation_after_shutdown(
    tmp_path: Path,
) -> None:
    application = RunnableApplication(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            scheduling.run_schedule_worker_application(
                cast("scheduling.ScheduleWorkerApplication", application),
                tmp_path / "config.json",
                environ={},
            )
        )
    assert application.stops == 1
