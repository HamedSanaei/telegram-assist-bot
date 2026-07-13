"""Verify unified runtime wiring without opening Telegram or MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast

import telegram_assist_bot.bootstrap.text_ingestion as module
from telegram_assist_bot.application.ports import PublicationPayload
from telegram_assist_bot.application.publication import PublishResult, PublishStatus
from telegram_assist_bot.application.scheduling import RunDueStatus
from telegram_assist_bot.domain import ScheduledPublication

if TYPE_CHECKING:
    import pytest


class Database(dict[str, object]):
    def __missing__(self, key: str) -> object:
        value = object()
        self[key] = value
        return value


class Loader:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def load(self, post_id: str, destination_id: int) -> PublicationPayload:
        del post_id
        return PublicationPayload(destination_id, "سلام", ())


class PublisherUseCase:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def execute(self, request: object) -> PublishResult:
        del request
        return PublishResult(PublishStatus.SUCCEEDED)


class Runner:
    instances: ClassVar[list[Runner]] = []

    def __init__(self, repository: object, **kwargs: object) -> None:
        del repository
        self.action = cast("str", kwargs["action"])
        self.build = cast("Any", kwargs["build_request"])
        self.publish = cast("Any", kwargs["publish"])
        self.after = cast("Any", kwargs["after_result"])
        self.instances.append(self)

    async def execute_once(self) -> RunDueStatus:
        request = await self.build("post-1", -1001)
        await self.publish(request)
        job = ScheduledPublication(
            f"job-{self.action}",
            "post-1",
            -1001,
            datetime(2026, 7, 13, tzinfo=UTC),
            action=self.action,
        )
        status = (
            RunDueStatus.COMPLETED
            if self.action == "immediate"
            else RunDueStatus.FAILED
        )
        await self.after(job, status)
        return status


class Worker:
    def __init__(self, run_once: object, *, poll_seconds: float) -> None:
        self.run_once = cast("Any", run_once)
        self.poll_seconds = poll_seconds

    async def run(self) -> None:
        await self.run_once()
        await asyncio.sleep(0)


class Operational:
    statuses: ClassVar[list[str]] = []

    def __init__(self, *args: object) -> None:
        del args

    async def record_destination_status(self, *args: object, **kwargs: object) -> None:
        del args
        self.statuses.append(cast("str", kwargs["status"]))


class Heartbeat:
    beats: ClassVar[list[str]] = []

    def __init__(self, collection: object) -> None:
        del collection

    async def beat(self, *args: object, **kwargs: object) -> None:
        del args
        self.beats.append(cast("str", kwargs["status"]))


def test_unified_worker_builds_immediate_and_scheduled_over_shared_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        Runner.instances.clear()
        Operational.statuses.clear()
        Heartbeat.beats.clear()

        async def initialize(*args: object) -> None:
            del args

        monkeypatch.setattr(module, "initialize_publication_indexes", initialize)
        monkeypatch.setattr(
            module, "initialize_operational_approval_indexes", initialize
        )
        monkeypatch.setattr(
            module, "MongoContentPreparationRepository", lambda *args: object()
        )
        monkeypatch.setattr(module, "MongoPublicationPayloadLoader", Loader)
        monkeypatch.setattr(
            module, "MongoPublicationRepository", lambda *args: object()
        )
        monkeypatch.setattr(module, "MongoScheduleRepository", lambda *args: object())
        monkeypatch.setattr(module, "MongoOperationalApprovalRepository", Operational)
        monkeypatch.setattr(module, "MongoRuntimeHeartbeatRepository", Heartbeat)
        monkeypatch.setattr(module, "PublishImmediately", PublisherUseCase)
        monkeypatch.setattr(module, "RunDuePublication", Runner)
        monkeypatch.setattr(module, "ScheduledPublicationWorker", Worker)
        publishing = SimpleNamespace(
            operation_timeout_seconds=30,
            publication_lease_seconds=60,
            publication_max_attempts=3,
            retry_initial_delay_seconds=1,
            retry_maximum_delay_seconds=30,
            worker_poll_seconds=5,
        )
        destination = SimpleNamespace(
            telegram_channel_id=-1001, name="dest", enabled=True
        )
        settings = SimpleNamespace(
            mongodb=SimpleNamespace(database_name="test"),
            destination_channels=(destination,),
            media=SimpleNamespace(root=cast("Any", "var/media")),
            publishing=publishing,
        )
        loaded = SimpleNamespace(settings=settings)
        database = Database()
        foundation = SimpleNamespace(
            mongodb_client={"test": database},
            logger=SimpleNamespace(emit=lambda **_: None),
        )
        shared_publisher = object()
        gateway = SimpleNamespace(
            publisher=lambda **kwargs: shared_publisher,
        )
        report = SimpleNamespace(
            channels=(
                SimpleNamespace(
                    role=SimpleNamespace(value="Destination"),
                    channel=SimpleNamespace(channel_id=-1001),
                ),
            )
        )
        worker = await module._create_publication_worker(
            cast("Any", loaded),
            cast("Any", report),
            cast("Any", gateway),
            cast("Any", foundation),
        )
        await worker.run()
        assert [item.action for item in Runner.instances] == [
            "immediate",
            "scheduled",
        ]
        assert Operational.statuses == ["published", "permanent_failed"]
        assert Heartbeat.beats == ["running", "stopped"]
        monkeypatch.setattr(
            module, "create_foundation_application", lambda **kwargs: object()
        )
        application = module.create_operational_runtime_application(
            sink=cast("Any", object())
        )
        assert isinstance(application, module.TextIngestionApplication)

    asyncio.run(scenario())
