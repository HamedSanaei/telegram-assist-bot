from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import errors, types  # type: ignore[import-untyped]

from telegram_assist_bot.application.native_scheduling import RunNativeScheduling
from telegram_assist_bot.application.ports import (
    NativeScheduleCommand,
    NativeScheduleRepository,
    NativeScheduleStatus,
    PublicationMedia,
    PublicationPayload,
    PublicationPayloadLoader,
)
from telegram_assist_bot.application.ports.telegram_source_gateway import (
    TelegramRateLimitError,
)
from telegram_assist_bot.bootstrap.native_scheduling import (
    classify_native_scheduling_failure,
    run_native_scheduling_cycle,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramUrlButton
from telegram_assist_bot.infrastructure.telegram.media_serializer import (
    TelethonMediaSerializationError,
)
from telegram_assist_bot.infrastructure.telegram.native_scheduler import (
    TelethonNativeSchedulerGateway,
)
from telegram_assist_bot.shared.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class Client:
    def __init__(self) -> None:
        self.messages = [SimpleNamespace(id=40, date=NOW + timedelta(minutes=7))]
        self.calls: list[tuple[str, object, dict[str, object]]] = []
        self.raw: object | None = None

    async def _iterate(self) -> AsyncIterator[object]:
        for message in self.messages:
            yield message

    def iter_messages(self, entity: int, **kwargs: object) -> AsyncIterator[object]:
        self.calls.append(("list", entity, kwargs))
        return self._iterate()

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        self.calls.append(("text", message, kwargs))
        return SimpleNamespace(id=51)

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        self.calls.append(("file", file, kwargs))
        if isinstance(file, list):
            return [SimpleNamespace(id=52), SimpleNamespace(id=53)]
        return SimpleNamespace(id=52)

    async def upload_file(self, file: object, **kwargs: object) -> object:
        self.calls.append(("upload", file, kwargs))
        return SimpleNamespace(upload=file, name=kwargs.get("file_name"))

    async def get_input_entity(self, peer: int) -> object:
        return f"peer:{peer}"

    async def __call__(self, request: object) -> object:
        self.raw = request
        return object()


def test_native_adapter_reads_external_schedules_and_schedules_every_payload_kind(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = Client()
        media: list[PublicationMedia] = []
        for kind, name in (
            (MediaType.PHOTO, "photo.jpg"),
            (MediaType.VIDEO, "video.mp4"),
            (MediaType.ANIMATION, "animation.mp4"),
            (MediaType.DOCUMENT, "document.bin"),
        ):
            (tmp_path / name).write_bytes(name.encode())
            media.append(
                PublicationMedia(
                    kind,
                    name,
                    NOW + timedelta(days=1),
                    original_filename="original.jpg"
                    if kind is MediaType.PHOTO
                    else None,
                )
            )
        gateway = TelethonNativeSchedulerGateway(
            cast("Any", client), media_root=tmp_path
        )
        listed = await gateway.list_scheduled(-1001, timeout_seconds=1)
        assert listed[0].message_id == 40
        assert client.calls[0] == ("list", -1001, {"scheduled": True})

        due_at = NOW + timedelta(minutes=12)
        text = await gateway.schedule(
            PublicationPayload(
                -1001,
                "سلام",
                (),
                (),
                (
                    (
                        TelegramUrlButton(
                            "Connect",
                            "https://t.me/proxy?server=example.invalid&port=443&secret=safe",
                        ),
                    ),
                ),
            ),
            due_at=due_at,
            timeout_seconds=1,
        )
        assert text.message_ids == (51,)
        assert client.calls[-1][2]["schedule"] == due_at
        assert client.calls[-1][1] == (
            "سلام\n\n"
            "Connect: "
            "https://t.me/proxy?server=example.invalid&port=443&secret=safe"
        )
        assert "buttons" not in client.calls[-1][2]

        for index, item in enumerate(media):
            receipt = await gateway.schedule(
                PublicationPayload(-1001, "کپشن", (), (item,)),
                due_at=due_at,
                timeout_seconds=1,
            )
            assert receipt.message_ids == (52,)
            assert client.calls[-1][2]["schedule"] == due_at
            serialized = client.calls[-1][1]
            if index == 0:
                assert isinstance(serialized, types.InputMediaUploadedPhoto)
            else:
                assert isinstance(serialized, types.InputMediaUploadedDocument)
                document = cast("types.InputMediaUploadedDocument", serialized)
                if item.media_type is MediaType.VIDEO:
                    video = next(
                        value
                        for value in document.attributes
                        if isinstance(value, types.DocumentAttributeVideo)
                    )
                    assert video.supports_streaming
                elif item.media_type is MediaType.ANIMATION:
                    assert any(
                        isinstance(value, types.DocumentAttributeAnimated)
                        for value in document.attributes
                    )
                else:
                    assert document.force_file

        album = await gateway.schedule(
            PublicationPayload(-1001, "آلبوم", (), tuple(media[:2])),
            due_at=due_at,
            timeout_seconds=1,
        )
        assert album.message_ids == (52, 53)
        serialized_album = cast("list[object]", client.calls[-1][1])
        assert len(serialized_album) == 2
        assert isinstance(serialized_album[0], types.InputMediaUploadedPhoto)
        assert isinstance(serialized_album[1], types.InputMediaUploadedDocument)

        await gateway.cancel(-1001, (52, 53), timeout_seconds=1)
        request = cast("Any", client.raw)
        assert request.id == [52, 53]

    asyncio.run(scenario())


def test_native_adapter_rejects_invalid_receipts_and_confined_paths(
    tmp_path: Path,
) -> None:
    class InvalidClient(Client):
        async def send_message(
            self, entity: int, message: str, **kwargs: object
        ) -> object:
            del entity, message, kwargs
            return SimpleNamespace(id=0)

    async def scenario() -> None:
        client = InvalidClient()
        client.messages = [
            SimpleNamespace(id=0, date=NOW),
            SimpleNamespace(id=3, date=datetime(2026, 7, 13, 12)),  # noqa: DTZ001
            SimpleNamespace(id=4, date=None),
        ]
        gateway = TelethonNativeSchedulerGateway(
            cast("Any", client), media_root=tmp_path
        )
        assert await gateway.list_scheduled(-1, timeout_seconds=1) == ()
        with pytest.raises(RuntimeError, match="receipt"):
            await gateway.schedule(
                PublicationPayload(-1, "text", ()),
                due_at=NOW + timedelta(minutes=5),
                timeout_seconds=1,
            )
        await gateway.cancel(-1, (), timeout_seconds=1)
        invalid = PublicationMedia(
            MediaType.DOCUMENT,
            "../outside",
            NOW + timedelta(days=1),
        )
        with pytest.raises(TelethonMediaSerializationError, match="path"):
            await gateway.schedule(
                PublicationPayload(-1, None, (), (invalid,)),
                due_at=NOW + timedelta(minutes=5),
                timeout_seconds=1,
            )
        missing = PublicationMedia(
            MediaType.DOCUMENT,
            "missing.bin",
            NOW + timedelta(days=1),
        )
        with pytest.raises(TelethonMediaSerializationError, match="path"):
            await gateway.schedule(
                PublicationPayload(-1, None, (), (missing,)),
                due_at=NOW + timedelta(minutes=5),
                timeout_seconds=1,
            )
        (tmp_path / "directory").mkdir()
        directory = PublicationMedia(
            MediaType.DOCUMENT,
            "directory",
            NOW + timedelta(days=1),
        )
        with pytest.raises(TelethonMediaSerializationError, match="path"):
            await gateway.schedule(
                PublicationPayload(-1, None, (), (directory,)),
                due_at=NOW + timedelta(minutes=5),
                timeout_seconds=1,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["list", "schedule", "cancel"])
@pytest.mark.parametrize(
    "flood_type",
    [
        errors.FloodWaitError,
        errors.FloodPremiumWaitError,
        errors.SlowModeWaitError,
        errors.FloodTestPhoneWaitError,
    ],
)
def test_native_adapter_preserves_floodwait_minimum_without_replaying_request(
    tmp_path: Path, operation: str, flood_type: Callable[..., Exception]
) -> None:
    error = flood_type(None, capture=90)

    class FloodedClient(Client):
        async def _iterate(self) -> AsyncIterator[object]:
            if operation == "list":
                raise error
            async for value in super()._iterate():
                yield value

        async def send_message(
            self, entity: int, message: str, **kwargs: object
        ) -> object:
            self.calls.append(("text", message, kwargs))
            raise error

        async def __call__(self, request: object) -> object:
            self.raw = request
            raise error

    async def scenario() -> None:
        client = FloodedClient()
        gateway = TelethonNativeSchedulerGateway(client, media_root=tmp_path)

        async def invoke() -> None:
            if operation == "list":
                await gateway.list_scheduled(-1001, timeout_seconds=1)
            elif operation == "schedule":
                await gateway.schedule(
                    PublicationPayload(-1001, "text", ()),
                    due_at=NOW,
                    timeout_seconds=1,
                )
            else:
                await gateway.cancel(-1001, (81,), timeout_seconds=1)

        with pytest.raises(TelegramRateLimitError) as failure:
            await invoke()
        assert failure.value.retry_after_seconds == 90
        assert failure.value.__cause__ is error
        if operation == "schedule":
            assert len(client.calls) == 1

    asyncio.run(scenario())


def test_native_cycle_recovers_on_the_same_client_before_send(tmp_path: Path) -> None:
    class ReconnectingClient(Client):
        connected = False
        read_attempts = 0

        async def _iterate(self) -> AsyncIterator[object]:
            self.read_attempts += 1
            if not self.connected:
                raise ConnectionError("Cannot send requests while disconnected")
            async for value in super()._iterate():
                yield value

    async def scenario() -> None:
        client = ReconnectingClient()
        repository = AsyncMock(spec=NativeScheduleRepository)
        command = NativeScheduleCommand(
            "command", "post", -1001, 3, NativeScheduleStatus.CLAIMED
        )
        repository.claim_next.side_effect = [command, command]
        repository.acquire_destination.return_value = True
        repository.mark_request_started.return_value = True
        repository.complete_scheduled.return_value = NativeScheduleCommand(
            "command",
            "post",
            -1001,
            3,
            NativeScheduleStatus.SCHEDULED,
            due_at=NOW + timedelta(minutes=12),
            telegram_message_ids=(51,),
        )
        repository.claim_reconciliation.return_value = None
        loader = AsyncMock(spec=PublicationPayloadLoader)
        loader.load.return_value = PublicationPayload(-1001, "text", ())
        native = RunNativeScheduling(
            cast("NativeScheduleRepository", repository),
            TelethonNativeSchedulerGateway(client, media_root=tmp_path),
            cast("PublicationPayloadLoader", loader),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=1,
            lease_seconds=60,
            retry_seconds=1,
            classify_failure=classify_native_scheduling_failure,
        )

        async def reconnect(_delay: float) -> None:
            # Stand in for the existing client's auto_reconnect, not a second
            # client/session owner or an explicit reconnect around a send.
            client.connected = True

        await run_native_scheduling_cycle(
            native,
            policy=RetryPolicy(10, 1, 8, jitter_ratio=0.2),
            logger=Mock(),
            sleeper=reconnect,
            jitter_source=lambda: 0.5,
        )
        assert client.read_attempts == 2
        assert [call[0] for call in client.calls] == ["list", "list", "text"]
        repository.mark_request_started.assert_awaited_once()
        repository.complete_scheduled.assert_awaited_once()
        assert not repository.fail.await_args.kwargs["outcome_unknown"]

    asyncio.run(scenario())
