"""Verify unified runtime wiring without opening Telegram or MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

import telegram_assist_bot.bootstrap.text_ingestion as module
from telegram_assist_bot.application.ports import (
    NativeScheduleCommand,
    NativeScheduleStatus,
    PublicationPayload,
)
from telegram_assist_bot.application.publication import PublishResult, PublishStatus
from telegram_assist_bot.application.scheduling import RunDueStatus
from telegram_assist_bot.domain import ScheduledPublication


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


class NativeRunner:
    instances: ClassVar[list[NativeRunner]] = []
    blocker: ClassVar[asyncio.Event | None] = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args
        self.after = cast("Any", kwargs["after_scheduled"])
        self.instances.append(self)

    async def execute_once(self) -> bool:
        if self.blocker is not None:
            await self.blocker.wait()
        await self.after(
            NativeScheduleCommand(
                "native-1",
                "post-1",
                -1001,
                2,
                NativeScheduleStatus.SCHEDULED,
                due_at=datetime(2026, 7, 13, tzinfo=UTC),
                telegram_message_ids=(9,),
            )
        )
        return True

    async def reconcile_once(self) -> bool:
        return False


class Worker:
    instances: ClassVar[list[Worker]] = []
    fail_index: ClassVar[int | None] = None

    def __init__(self, run_once: object, *, poll_seconds: float) -> None:
        self.run_once = cast("Any", run_once)
        self.poll_seconds = poll_seconds
        self.index = len(self.instances)
        self.instances.append(self)

    async def run(self) -> None:
        if self.fail_index == self.index:
            raise RuntimeError("synthetic publication loop failure")
        await self.run_once()
        await asyncio.Event().wait()


class Operational:
    statuses: ClassVar[list[str]] = []
    completed: ClassVar[asyncio.Event | None] = None

    def __init__(self, *args: object) -> None:
        del args

    async def record_destination_status(self, *args: object, **kwargs: object) -> None:
        del args
        self.statuses.append(cast("str", kwargs["status"]))
        if len(self.statuses) >= 2 and self.completed is not None:
            self.completed.set()


class Heartbeat:
    beats: ClassVar[list[str]] = []
    fail: ClassVar[bool] = False

    def __init__(self, collection: object) -> None:
        del collection

    async def beat(self, *args: object, **kwargs: object) -> None:
        del args
        if self.fail:
            raise RuntimeError("synthetic heartbeat failure")
        self.beats.append(cast("str", kwargs["status"]))


def test_unified_worker_builds_immediate_and_scheduled_over_shared_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        Runner.instances.clear()
        NativeRunner.instances.clear()
        NativeRunner.blocker = None
        Operational.statuses.clear()
        Operational.completed = asyncio.Event()
        Heartbeat.beats.clear()
        Heartbeat.fail = False
        Worker.instances.clear()
        Worker.fail_index = None
        emitted: list[dict[str, object]] = []

        async def initialize(*args: object) -> None:
            del args

        monkeypatch.setattr(module, "initialize_publication_indexes", initialize)
        monkeypatch.setattr(
            module, "initialize_operational_approval_indexes", initialize
        )
        monkeypatch.setattr(module, "initialize_native_schedule_indexes", initialize)
        monkeypatch.setattr(
            module, "MongoContentPreparationRepository", lambda *args: object()
        )
        monkeypatch.setattr(module, "MongoPublicationPayloadLoader", Loader)
        monkeypatch.setattr(
            module, "MongoPublicationRepository", lambda *args: object()
        )
        monkeypatch.setattr(module, "MongoScheduleRepository", lambda *args: object())
        monkeypatch.setattr(
            module, "MongoNativeScheduleRepository", lambda *args: object()
        )
        monkeypatch.setattr(module, "MongoOperationalApprovalRepository", Operational)
        monkeypatch.setattr(module, "MongoRuntimeHeartbeatRepository", Heartbeat)
        monkeypatch.setattr(module, "PublishImmediately", PublisherUseCase)
        monkeypatch.setattr(module, "RunDuePublication", Runner)
        monkeypatch.setattr(module, "RunNativeScheduling", NativeRunner)
        monkeypatch.setattr(module, "ScheduledPublicationWorker", Worker)
        publishing = SimpleNamespace(
            operation_timeout_seconds=30,
            publication_lease_seconds=60,
            publication_max_attempts=3,
            retry_initial_delay_seconds=1,
            retry_maximum_delay_seconds=30,
            worker_poll_seconds=5,
            native_schedule_timeout_seconds=30,
            native_schedule_lease_seconds=60,
            native_schedule_poll_seconds=1,
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
            logger=SimpleNamespace(emit=lambda **values: emitted.append(dict(values))),
        )
        shared_publisher = object()
        gateway = SimpleNamespace(
            publisher=lambda **kwargs: shared_publisher,
            native_scheduler=lambda **kwargs: object(),
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
        task = asyncio.create_task(worker.run())
        await worker.wait_ready()
        await asyncio.wait_for(Operational.completed.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert [item.action for item in Runner.instances] == ["immediate"]
        assert Operational.statuses == ["published", "native_scheduled"]
        assert Heartbeat.beats == ["running", "stopped"]
        assert [item.poll_seconds for item in Worker.instances] == [1.0, 1.0]
        names = [cast("str", item["event_name"]) for item in emitted]
        assert "runtime_heartbeat_active" in names
        assert "publication_worker_started" in names

        NativeRunner.blocker = asyncio.Event()
        Operational.statuses.clear()
        Worker.instances.clear()
        isolated = await module._create_publication_worker(
            cast("Any", loaded),
            cast("Any", report),
            cast("Any", gateway),
            cast("Any", foundation),
        )
        isolated_task = asyncio.create_task(isolated.run())
        await isolated.wait_ready()
        for _attempt in range(100):
            if "published" in Operational.statuses:
                break
            await asyncio.sleep(0.01)
        assert Operational.statuses == ["published"]
        isolated_task.cancel()
        await asyncio.gather(isolated_task, return_exceptions=True)
        NativeRunner.blocker = None

        Heartbeat.fail = True
        failing = await module._create_publication_worker(
            cast("Any", loaded),
            cast("Any", report),
            cast("Any", gateway),
            cast("Any", foundation),
        )
        with pytest.raises(module.OperationalRuntimeError) as heartbeat_failure:
            await failing.run()
        assert isinstance(heartbeat_failure.value.__cause__, RuntimeError)
        heartbeat_event = next(
            item
            for item in reversed(emitted)
            if item["event_name"] == "runtime_task_completed_unexpectedly"
        )
        assert heartbeat_event["fields"] == {
            "task_name": "runtime-heartbeat",
            "completion_kind": "failed",
            "failure_type": "RuntimeError",
        }

        Heartbeat.fail = False
        Worker.fail_index = len(Worker.instances)
        publication_failing = await module._create_publication_worker(
            cast("Any", loaded),
            cast("Any", report),
            cast("Any", gateway),
            cast("Any", foundation),
        )
        with pytest.raises(module.OperationalRuntimeError) as publication_failure:
            await publication_failing.run()
        assert isinstance(publication_failure.value.__cause__, RuntimeError)
        publication_event = next(
            item
            for item in reversed(emitted)
            if item["event_name"] == "runtime_task_completed_unexpectedly"
            and cast("dict[str, object]", item["fields"])["task_name"]
            == "runtime-publication"
        )
        assert publication_event["fields"] == {
            "task_name": "runtime-publication",
            "completion_kind": "failed",
            "failure_type": "RuntimeError",
        }
        monkeypatch.setattr(
            module, "create_foundation_application", lambda **kwargs: object()
        )
        application = module.create_operational_runtime_application(
            sink=cast("Any", object())
        )
        assert isinstance(application, module.TextIngestionApplication)

    asyncio.run(scenario())
