"""Exercise local-operator recovery permissions against durable MongoDB state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from pymongo import AsyncMongoClient

import telegram_assist_bot.bootstrap.media_recovery as recovery
from telegram_assist_bot.application.ports import MediaDownloadSpec
from telegram_assist_bot.application.ports.media import MediaFailure
from telegram_assist_bot.application.text_ingestion import build_stored_post
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
)
from tests.integration.test_runtime_media_ingestion import (
    Clock,
    Gateway,
    MediaSource,
    MongoTestSettings,
    create_application,
    loaded_configuration,
    source_message,
)

if TYPE_CHECKING:
    from pathlib import Path

    from telegram_assist_bot.shared.observability import EventSink

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "mode",
    [
        "allowed",
        "username_case",
        "unknown_actor",
        "inactive",
        "destination_denied",
        "source_disabled",
    ],
)
def test_recovery_revalidates_permissions_before_mutating_durable_rejection(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        loaded = loaded_configuration(tmp_path, mongodb_test_settings.database_name)
        settings = loaded.settings
        admin = settings.admins[0]
        if mode == "inactive":
            admin = admin.model_copy(update={"active": False})
        elif mode == "destination_denied":
            admin = admin.model_copy(update={"allowed_destination_ids": (-999,)})
        source = settings.source_channels[0]
        if mode == "source_disabled":
            source = source.model_copy(update={"enabled": False})
        elif mode == "username_case":
            source = source.model_copy(update={"username": "SOURCE_EXAMPLE"})
        settings = settings.model_copy(
            update={"admins": (admin,), "source_channels": (source,)}
        )
        loaded = type(loaded)(settings, loaded.secrets)
        _, foundation = await create_application(
            mongodb_test_settings, loaded, Clock(now), Gateway((), [], MediaSource())
        )
        message = source_message(68568, now, media_type=MediaType.DOCUMENT)
        post = build_stored_post(
            message, received_at=now, post_id_factory=lambda _: PostId("rejected-post")
        )
        await foundation.repository.insert_idempotently(post)
        database = foundation.client[mongodb_test_settings.database_name]
        repository = MongoContentPreparationRepository(
            database["media_items"],
            database["media_groups"],
            database["content_preparations"],
        )
        assert await repository.claim_ingestion(
            post.post_id,
            owner="seed",
            now=now,
            lease_until=now + timedelta(minutes=5),
        )
        await repository.save_ingestion_message(
            post.post_id, message, expires_at=post.expires_at
        )
        identity = MediaIdentity(-100, 68568, 0)
        await repository.record_media_failure(
            MediaDownloadSpec(
                identity,
                MediaType.DOCUMENT,
                "synthetic-reference",
                "application/octet-stream",
                "synthetic.bin",
                now + timedelta(days=2),
                post.post_id,
                size_bytes=104857601,
            ),
            MediaFailure(
                "oversized", 1, now, maximum_bytes=104857600, size_bytes=104857601
            ),
        )
        await repository.release_ingestion(post.post_id, owner="seed")

        def factory(*, sink: EventSink) -> object:
            del sink
            return foundation

        monkeypatch.setattr(recovery, "create_foundation_application", factory)
        actor = 999 if mode == "unknown_actor" else admin.telegram_user_id
        if mode in {"allowed", "username_case"}:
            assert await recovery.queue_rejected_media_retry(
                tmp_path / "unused.json",
                environ={},
                sink=lambda _: None,
                source_channel_id=-100,
                source_message_id=68568,
                administrator_id=actor,
            )
        else:
            with pytest.raises(PermissionError):
                await recovery.queue_rejected_media_retry(
                    tmp_path / "unused.json",
                    environ={},
                    sink=lambda _: None,
                    source_channel_id=-100,
                    source_message_id=68568,
                    administrator_id=actor,
                )
        assert foundation.shutdown_calls == 1
        observer: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, timeoutMS=5000, tz_aware=True
        )
        try:
            preparations = observer[mongodb_test_settings.database_name][
                "content_preparations"
            ]
            state = await preparations.find_one({"_id": post.post_id.value})
            operation = await preparations.find_one(
                {"_id": f"media-operation:{identity.key}"}
            )
            assert state is not None
            assert operation is not None
            if mode in {"allowed", "username_case"}:
                assert state["media_rejected"] is False
                assert state["recovery_requested"] is True
                audit = cast("list[dict[str, object]]", operation["media_retry_audit"])
                assert audit[0]["administrator_id"] == actor
            else:
                assert state["media_rejected"] is True
                assert "media_retry_audit" not in operation
        finally:
            await observer.close()

    asyncio.run(scenario())
