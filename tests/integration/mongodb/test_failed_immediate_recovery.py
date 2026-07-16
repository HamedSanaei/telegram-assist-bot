"""Verify exact pre-send immediate recovery against isolated MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bson.int64 import Int64
from pymongo import AsyncMongoClient

from telegram_assist_bot.bootstrap.publication_queue import (
    PreSendRecoveryResult,
    _recover_failed_immediate_in_database,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_recovery_dry_run_clear_and_requeue_are_narrow(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            now = datetime(2026, 7, 16, tzinfo=UTC)

            async def seed(post_id: str) -> str:
                job_id = f"job-{post_id}"
                await database["scheduled_publications"].insert_one(
                    {
                        "_id": job_id,
                        "post_id": post_id,
                        "destination_id": Int64(-1001234567890),
                        "action": "immediate",
                        "status": "PermanentFailed",
                        "last_error_category": "permanent",
                        "last_failure_type": "PublisherError",
                        "due_at": now,
                        "version": 2,
                    }
                )
                await database["publications"].insert_one(
                    {
                        "_id": job_id,
                        "post_id": post_id,
                        "action": "immediate",
                        "state": "PermanentFailed",
                        "error_category": "permanent",
                        "failure_type": "PublisherError",
                        "version": 2,
                    }
                )
                await database["destination_selections"].insert_one(
                    {
                        "post_id": post_id,
                        "destination_id": Int64(-1001234567890),
                        "mode": "immediate",
                        "version": 1,
                        "history": [],
                    }
                )
                await database["approval_deliveries"].insert_one(
                    {"_id": post_id, "sync_version": 0}
                )
                return job_id

            clear_job = await seed("clear-post")
            assert (
                await _recover_failed_immediate_in_database(
                    database,
                    approval_post_id="clear-post",
                    now=now,
                    dry_run=True,
                    requeue=False,
                )
                is PreSendRecoveryResult.DRY_RUN_ELIGIBLE
            )
            unchanged = await database["scheduled_publications"].find_one(
                {"_id": clear_job}
            )
            assert unchanged is not None
            assert unchanged["status"] == "PermanentFailed"
            assert (
                await _recover_failed_immediate_in_database(
                    database,
                    approval_post_id="clear-post",
                    now=now,
                    dry_run=False,
                    requeue=False,
                )
                is PreSendRecoveryResult.CLEARED
            )
            selection = await database["destination_selections"].find_one(
                {"post_id": "clear-post"}
            )
            assert selection is not None
            assert selection["mode"] == "none"

            requeue_job = await seed("requeue-post")
            assert (
                await _recover_failed_immediate_in_database(
                    database,
                    approval_post_id="requeue-post",
                    now=now,
                    dry_run=False,
                    requeue=True,
                )
                is PreSendRecoveryResult.REQUEUED
            )
            schedule = await database["scheduled_publications"].find_one(
                {"_id": requeue_job}
            )
            publication = await database["publications"].find_one({"_id": requeue_job})
            assert schedule is not None
            assert schedule["status"] == "Pending"
            assert publication is not None
            assert publication["state"] == "Pending"
            delivery = await database["approval_deliveries"].find_one(
                {"_id": "requeue-post"}
            )
            assert delivery is not None
            assert delivery["sync_required"]
        finally:
            await client.close()

    asyncio.run(scenario())
