"""Prove exact recovery of legacy text-URL failures before Telegram send."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.bootstrap.publication_queue import (
    PreSendRecoveryResult,
    _recover_pre_send_in_database,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_recovery_requires_exact_pre_send_proof_and_is_idempotent(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            now = datetime(2026, 7, 16, 10, tzinfo=UTC)
            post_id = "legacy-text-url-post"
            job_id = "legacy-text-url-job"
            await database["content_preparations"].insert_one(
                {
                    "_id": post_id,
                    "artifacts": {
                        "destination": {
                            "entities": [
                                {
                                    "offset": 8,
                                    "length": 4,
                                    "entity_type": "text_url",
                                    "custom_emoji_id": None,
                                }
                            ]
                        }
                    },
                }
            )
            await database["scheduled_publications"].insert_one(
                {
                    "_id": job_id,
                    "post_id": post_id,
                    "destination_id": -1001,
                    "action": "immediate",
                    "due_at": now - timedelta(minutes=1),
                    "status": "OutcomeUnknown",
                    "attempt_count": 1,
                    "version": 1,
                    "last_error_category": "ambiguous",
                    "last_failure_type": "ValueError",
                }
            )
            await database["publications"].insert_one(
                {
                    "_id": job_id,
                    "post_id": post_id,
                    "destination_id": -1001,
                    "action": "immediate",
                    "state": "Claimed",
                    "attempt_count": 1,
                    "lease_until": now - timedelta(seconds=1),
                }
            )

            first = await _recover_pre_send_in_database(
                database, approval_post_id=post_id, now=now
            )
            second = await _recover_pre_send_in_database(
                database, approval_post_id=post_id, now=now
            )

            assert first is PreSendRecoveryResult.REQUEUED
            assert second is PreSendRecoveryResult.ALREADY_REQUEUED
            stored = await database["scheduled_publications"].find_one({"_id": job_id})
            assert stored is not None
            assert stored["status"] == "Pending"
            assert stored["attempt_count"] == 1
            publication = await database["publications"].find_one({"_id": job_id})
            assert publication is not None
            assert publication["state"] == "Claimed"
        finally:
            await client.close()

    asyncio.run(scenario())
