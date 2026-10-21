"""Verify incident rejection, leases and restart replay against isolated MongoDB."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestPostIdempotently,
)
from telegram_assist_bot.application.ports.admin import BotUpdate
from telegram_assist_bot.application.prepare_post_pipeline import PreparePostPipeline
from telegram_assist_bot.application.retry_rejected_media import (
    request_rejected_media_retry,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    initialize_post_indexes,
    verify_mongodb_connection,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)
from tests.unit.application.test_media_incident_recovery import administrator
from tests.unit.application.test_runtime_ingestion import Clock, coordinator, message

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.test_content_preparation_pipeline import MongoTestSettings

pytestmark = pytest.mark.integration


def test_mongo_concurrent_lease_rejection_restart_and_authorized_retry(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = MongoConfig(
            uri=SecretReference(environment_variable="TEST_MONGODB_URI"),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            config, ResolvedSecrets({"TEST_MONGODB_URI": mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config.database_name]
            posts = get_posts_collection(client, config)
            await initialize_post_indexes(posts, timeout_seconds=5)
            media, groups, preparations = (
                database["media_items"],
                database["media_groups"],
                database["content_preparations"],
            )
            await initialize_content_preparation_indexes(media, groups, preparations)
            repository = MongoContentPreparationRepository(media, groups, preparations)
            now = datetime(2026, 10, 21, tzinfo=UTC)
            lease_id = PostId("concurrent-lease")
            claims = await asyncio.gather(
                *(
                    repository.claim_ingestion(
                        lease_id,
                        owner=f"worker-{index}",
                        now=now,
                        lease_until=now + timedelta(seconds=1),
                    )
                    for index in range(8)
                )
            )
            assert sum(claims) == 1
            assert await repository.claim_ingestion(
                lease_id,
                owner="restart",
                now=now + timedelta(seconds=2),
                lease_until=now + timedelta(minutes=1),
            )
            runtime, _, source, _ = coordinator(tmp_path, Clock(now))
            post_repository = MongoPostRepository(posts, 5)
            runtime.post_repository = post_repository
            runtime.post_ingestor = IngestPostIdempotently(
                post_repository,
                runtime.clock,
                lambda identity: PostId(f"post-{identity.source_message_id}"),
                runtime.logger,
            )
            runtime.content_repository = repository
            runtime.assembler = AssembleMediaGroup(
                repository,
                quiet_window=timedelta(seconds=2),
                maximum_wait=timedelta(seconds=10),
            )
            runtime.pipeline = PreparePostPipeline(repository)
            runtime.policy = replace(
                runtime.policy,
                sources=(
                    replace(
                        runtime.policy.sources[0],
                        source_channel_id=-1001648659094,
                    ),
                ),
            )
            runtime.downloader = DownloadPostMedia(
                source,
                runtime.storage,
                repository,
                maximum_bytes=100 * 1024 * 1024,
                timeout_seconds=1,
            )
            fixture = replace(
                message(
                    68568,
                    now - timedelta(days=1),
                    media_type=MediaType.DOCUMENT,
                    caption="اصل 😀",
                ),
                source_channel_id=-1001648659094,
            )
            fixture = replace(
                fixture,
                media=(
                    replace(
                        fixture.media[0],
                        size_bytes=100 * 1024 * 1024 + 1,
                    ),
                ),
            )
            result = await runtime.execute(fixture, correlation_id="incident")
            assert source.opens == 0
            restarted_repository = MongoContentPreparationRepository(
                media, groups, preparations
            )
            restarted = replace(
                runtime,
                content_repository=restarted_repository,
                pipeline=PreparePostPipeline(restarted_repository),
                downloader=DownloadPostMedia(
                    source,
                    runtime.storage,
                    restarted_repository,
                    maximum_bytes=200 * 1024 * 1024,
                    timeout_seconds=1,
                ),
            )
            await restarted.execute(fixture, correlation_id="restart")
            assert source.opens == 0
            failure = await restarted_repository.get_media_failure(
                MediaIdentity(-1001648659094, 68568)
            )
            assert failure is not None
            assert failure.category == "oversized"
            with pytest.raises(PermissionError):
                await request_rejected_media_retry(
                    source_channel_id=-1001648659094,
                    source_message_id=68568,
                    update=BotUpdate(8, 8, "private"),
                    authorize=administrator(),
                    post_repository=post_repository,
                    content_repository=restarted_repository,
                    clock=runtime.clock,
                )
            assert await request_rejected_media_retry(
                source_channel_id=-1001648659094,
                source_message_id=68568,
                update=BotUpdate(7, 7, "private"),
                authorize=administrator(),
                post_repository=post_repository,
                content_repository=restarted_repository,
                clock=runtime.clock,
                destination_ids=(-200,),
            )
            assert await restarted.recover_pending_messages() == 1
            assert source.opens == 1
            assert (
                await restarted_repository.get_preparation_state(result.post_id)
            ).ready_at is not None
            await restarted.execute(fixture, correlation_id="completed")
            assert source.opens == 1
            assert await posts.count_documents({}) == 1
            audit = await preparations.find_one(
                {"_id": f"media-operation:{MediaIdentity(-1001648659094, 68568).key}"}
            )
            assert audit is not None
            retry_audit = cast("list[dict[str, object]]", audit["media_retry_audit"])
            assert retry_audit[0]["administrator_id"] == 7
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
