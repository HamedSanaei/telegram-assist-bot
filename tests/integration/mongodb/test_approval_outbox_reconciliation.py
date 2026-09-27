"""MongoDB proofs for the durable approval outbox and its bounded polling cost.

The delivery claim is the steady-state hot path of every approval bot. These
tests build a production-shaped dataset, run the real indexed queries, and assert
with `explain` that the claim is bounded by the indexed delivery population
instead of the historical `content_preparations` collection.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from pymongo import AsyncMongoClient
from pymongo.monitoring import (
    CommandFailedEvent,
    CommandListener,
    CommandStartedEvent,
    CommandSucceededEvent,
)

from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparationInput,
    PreparePostPipeline,
)
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.domain.posts import PostId, TelegramEntity
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoOperationalApprovalRepository,
    initialize_operational_approval_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    operational_approval_repository as outbox,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
_HISTORICAL_DELIVERIES = 2000
_HISTORICAL_PREPARATIONS = 300


def _repository(
    database: dict[str, AsyncCollection[dict[str, Any]]],
) -> MongoOperationalApprovalRepository:
    return MongoOperationalApprovalRepository(
        cast("Any", database["content_preparations"]),
        cast("Any", database["approval_deliveries"]),
        outbox_state=cast("Any", database["approval_outbox_state"]),
    )


def _stage_names(explain: dict[str, Any]) -> list[str]:
    stage = explain["queryPlanner"]["winningPlan"]
    names: list[str] = []
    pending = [stage]
    while pending:
        current = pending.pop()
        name = current.get("stage")
        names.extend([name] if isinstance(name, str) else [])
        for key in ("inputStage", "queryPlan", "innerStage"):
            child = current.get(key)
            if isinstance(child, dict):
                pending.append(child)
        for key in ("inputStages", "shards"):
            children = current.get(key)
            if isinstance(children, list):
                pending.extend(item for item in children if isinstance(item, dict))
    shards = (
        explain.get("queryPlanner", {}).get("winningPlan", {}).get("inputStages", [])
    )
    names.extend(
        str(shard["inputStage"].get("stage"))
        for shard in shards
        if isinstance(shard, dict) and isinstance(shard.get("inputStage"), dict)
    )
    return names


def _total_stat(explain: dict[str, Any], name: str) -> int:
    stats = explain.get("executionStats")
    if not isinstance(stats, dict):
        return 0
    value = stats.get(name)
    return value if isinstance(value, int) else 0


class _CommandCounter(CommandListener):
    """Count issued MongoDB commands per target collection name."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []

    def started(self, event: CommandStartedEvent) -> None:
        target = event.command.get(event.command_name)
        collection = target if type(target) is str else ""
        self.commands.append((event.command_name, collection))

    def succeeded(self, event: CommandSucceededEvent) -> None:
        del event  # pragma: no cover - nothing to record for success.

    def failed(self, event: CommandFailedEvent) -> None:
        del event  # pragma: no cover - nothing to record for failure.


def test_idle_claim_polls_issue_no_readiness_commands(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Instrumented proof that idle polling performs constant, delivery-only work."""

    async def scenario() -> None:
        counter = _CommandCounter()
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True, event_listeners=[counter]
        )
        try:
            database = client[mongodb_test_settings.database_name]
            deliveries = database["approval_deliveries"]
            preparations = database["content_preparations"]
            await initialize_operational_approval_indexes(deliveries)
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            await deliveries.insert_many(
                [
                    {
                        "_id": f"completed-{index:05d}",
                        "status": "completed",
                        "ready_at": _NOW,
                        "created_at": _NOW,
                        "claim_due_at": None,
                    }
                    for index in range(_HISTORICAL_DELIVERIES)
                ]
            )
            await preparations.insert_many(
                [
                    {"_id": f"ready-{index:05d}", "ready_at": _NOW}
                    for index in range(_HISTORICAL_PREPARATIONS)
                ]
            )
            operational = _repository(cast("Any", database))
            counter.commands.clear()

            for _ in range(20):
                claim = await operational.claim_ready(
                    owner="worker",
                    now=_NOW,
                    lease_until=_NOW + timedelta(seconds=30),
                )
                assert claim is None

            readiness_commands = [
                command
                for command in counter.commands
                if command[1] == "content_preparations"
            ]
            assert readiness_commands == []
            assert counter.commands == [("findAndModify", "approval_deliveries")] * 20
        finally:
            await client.close()

    asyncio.run(scenario())


def test_approval_claim_plan_is_index_bounded_for_production_history(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """A historical backlog must never turn the claim poll into a collection scan."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            deliveries = database["approval_deliveries"]
            preparations = database["content_preparations"]
            await initialize_operational_approval_indexes(deliveries)
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            now = _NOW
            await deliveries.insert_many(
                [
                    {
                        "_id": f"historical-{index:05d}",
                        "status": "completed",
                        "ready_at": now - timedelta(days=1),
                        "created_at": now - timedelta(days=1),
                        "claim_due_at": None,
                    }
                    for index in range(_HISTORICAL_DELIVERIES)
                ]
            )
            await preparations.insert_many(
                [
                    {"_id": f"ready-{index:05d}", "ready_at": now}
                    for index in range(_HISTORICAL_PREPARATIONS)
                ]
            )
            await preparations.insert_many(
                [
                    {"_id": f"unready-{index:05d}", "artifacts": {}}
                    for index in range(_HISTORICAL_PREPARATIONS)
                ]
            )

            claim_query = outbox.approval_claim_filter(now=now)
            claim_explain = (
                await deliveries.find(claim_query)
                .sort(outbox.APPROVAL_CLAIM_SORT)
                .explain()
            )
            claim_stages = _stage_names(claim_explain)
            assert "COLLSCAN" not in claim_stages
            assert "IXSCAN" in claim_stages
            assert "SORT" not in claim_stages
            assert _total_stat(claim_explain, "totalKeysExamined") <= 10
            assert _total_stat(claim_explain, "totalDocsExamined") <= 10

            scan_explain = (
                await preparations.find(
                    outbox.outbox_reconciliation_filter(
                        outbox.INITIAL_RECONCILIATION_WATERMARK
                    ),
                    {"_id": 1, "ready_at": 1},
                )
                .sort([("ready_at", 1), ("_id", 1)])
                .limit(200)
                .explain()
            )
            scan_stages = _stage_names(scan_explain)
            assert "COLLSCAN" not in scan_stages
            assert "IXSCAN" in scan_stages
            assert "SORT" not in scan_stages
            assert _total_stat(scan_explain, "totalDocsExamined") <= 200
        finally:
            await client.close()

    asyncio.run(scenario())


def test_readiness_transition_creates_one_outbox_identity_and_claim_is_cheap(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """A ready preparation is delivered through its outbox identity, not a scan."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            media = database["media_items"]
            groups = database["media_groups"]
            preparations = database["content_preparations"]
            deliveries = database["approval_deliveries"]
            await initialize_content_preparation_indexes(media, groups, preparations)
            await initialize_operational_approval_indexes(deliveries)
            repository = MongoContentPreparationRepository(media, groups, preparations)
            operational = _repository(cast("Any", database))
            pipeline = PreparePostPipeline(repository, operational)
            request = PreparationInput(
                PostId("post-ready"),
                "متن آزمایشی",
                None,
                (TelegramEntity(0, 3, "bold"),),
                "source_name",
                (),
                (Category("news", "اخبار"),),
                (),
                "news",
                (DestinationSpec("d1", "dest_name"),),
                _NOW,
            )

            await asyncio.gather(pipeline.execute(request), pipeline.execute(request))

            stored = await deliveries.find_one({"_id": "post-ready"})
            assert stored is not None
            assert stored["status"] == "pending"
            assert stored["ready_at"] == _NOW
            assert stored["created_at"] == _NOW
            assert stored["claim_due_at"] == _NOW

            claim = await operational.claim_ready(
                owner="worker",
                now=_NOW,
                lease_until=_NOW + timedelta(seconds=30),
            )
            assert claim is not None
            assert claim.post_id == "post-ready"
            assert await operational.complete_delivery("post-ready", owner="worker")
            assert (
                await operational.claim_ready(
                    owner="worker",
                    now=_NOW + timedelta(seconds=31),
                    lease_until=_NOW + timedelta(seconds=61),
                )
                is None
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_reconciliation_backfills_legacy_preparations_once_and_in_order(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Legacy ready preparations gain identities once, in readiness order."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            preparations = database["content_preparations"]
            deliveries = database["approval_deliveries"]
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            await initialize_operational_approval_indexes(deliveries)
            await preparations.insert_many(
                [
                    {
                        "_id": f"legacy-{index:03d}",
                        "ready_at": _NOW + timedelta(seconds=index),
                    }
                    for index in range(5)
                ]
            )
            operational = _repository(cast("Any", database))

            first = await operational.reconcile_missing_deliveries(limit=2, at=_NOW)
            assert (first.scanned_count, first.created_count) == (2, 2)
            assert first.completed is False
            second = await operational.reconcile_missing_deliveries(limit=2, at=_NOW)
            assert (second.scanned_count, second.created_count) == (2, 2)
            third = await operational.reconcile_missing_deliveries(limit=2, at=_NOW)
            assert (third.scanned_count, third.created_count) == (1, 1)
            assert third.completed is True
            repeated = await operational.reconcile_missing_deliveries(limit=2, at=_NOW)
            assert (repeated.scanned_count, repeated.created_count) == (0, 0)
            assert repeated.completed is True
            assert await deliveries.count_documents({}) == 5

            claims = []
            for _ in range(5):
                claim = await operational.claim_ready(
                    owner="worker",
                    now=_NOW + timedelta(seconds=10),
                    lease_until=_NOW + timedelta(seconds=40),
                )
                assert claim is not None
                claims.append(claim.post_id)
                assert await operational.complete_delivery(
                    claim.post_id, owner="worker"
                )
            assert claims == [f"legacy-{index:03d}" for index in range(5)]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_concurrent_outbox_creation_creates_one_logical_delivery(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Concurrent readiness transitions must not duplicate a logical delivery."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            deliveries = database["approval_deliveries"]
            await initialize_operational_approval_indexes(deliveries)
            operational = _repository(cast("Any", database))
            results = await asyncio.gather(
                *(
                    operational.ensure_delivery("post-1", ready_at=_NOW)
                    for _ in range(8)
                )
            )
            assert sum(results) == 1
            assert await deliveries.count_documents({"_id": "post-1"}) == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_guard_window_reconciles_missing_identities_after_they_age_out(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """The trailing window heals a failed inline write without a historical scan."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            preparations = database["content_preparations"]
            deliveries = database["approval_deliveries"]
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            await initialize_operational_approval_indexes(deliveries)
            await preparations.insert_many(
                [
                    {"_id": "historical", "ready_at": _NOW - timedelta(seconds=400)},
                    {"_id": "recent", "ready_at": _NOW - timedelta(seconds=10)},
                ]
            )
            operational = _repository(cast("Any", database))

            first = await operational.reconcile_missing_deliveries(
                limit=100, at=_NOW, guard_seconds=300
            )
            assert (first.scanned_count, first.created_count) == (1, 1)
            assert await deliveries.count_documents({"_id": "recent"}) == 0

            later = await operational.reconcile_missing_deliveries(
                limit=100, at=_NOW + timedelta(minutes=6), guard_seconds=300
            )
            assert (later.scanned_count, later.created_count) == (1, 1)
            assert await deliveries.count_documents({"_id": "recent"}) == 1
            assert await deliveries.count_documents({"_id": "historical"}) == 1

            settled = await operational.reconcile_missing_deliveries(
                limit=100, at=_NOW + timedelta(minutes=6), guard_seconds=300
            )
            assert (settled.scanned_count, settled.created_count) == (0, 0)
            assert settled.completed is True

            guarded = outbox.outbox_reconciliation_filter(
                outbox.INITIAL_RECONCILIATION_WATERMARK,
                ready_before_or_at=_NOW - timedelta(seconds=300),
            )
            explain = (
                await preparations.find(guarded, {"_id": 1, "ready_at": 1})
                .sort([("ready_at", 1), ("_id", 1)])
                .limit(200)
                .explain()
            )
            stages = _stage_names(explain)
            assert "COLLSCAN" not in stages
            assert "IXSCAN" in stages
            assert _total_stat(explain, "totalDocsExamined") <= 2
        finally:
            await client.close()

    asyncio.run(scenario())


def test_reconciliation_rewind_heals_a_window_a_raised_guard_deferred(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """A durable watermark at the horizon is rewound instead of skipping work."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            preparations = database["content_preparations"]
            deliveries = database["approval_deliveries"]
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            await initialize_operational_approval_indexes(deliveries)
            await preparations.insert_many(
                [{"_id": "recent", "ready_at": _NOW - timedelta(seconds=10)}]
            )
            await database["approval_outbox_state"].insert_one(
                {
                    "_id": "content_preparation_outbox",
                    "watermark_ready_at": _NOW,
                    "watermark_id": "recent",
                }
            )
            operational = _repository(cast("Any", database))

            rewound = await operational.reconcile_missing_deliveries(
                limit=100, at=_NOW, guard_seconds=300
            )
            assert (rewound.scanned_count, rewound.created_count) == (0, 0)
            state = await database["approval_outbox_state"].find_one(
                {"_id": "content_preparation_outbox"}
            )
            assert state is not None
            assert state["watermark_ready_at"] == _NOW - timedelta(seconds=300)

            healed = await operational.reconcile_missing_deliveries(
                limit=100, at=_NOW + timedelta(minutes=6), guard_seconds=300
            )
            assert (healed.scanned_count, healed.created_count) == (1, 1)
            assert await deliveries.count_documents({"_id": "recent"}) == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_claim_indexes_match_the_bounded_query_paths(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """The initialized indexes must match the exact claim and sync predicates."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            deliveries = client[mongodb_test_settings.database_name][
                "approval_deliveries"
            ]
            await initialize_operational_approval_indexes(deliveries)
            information = await deliveries.index_information()
            assert information["ix_approval_delivery_claim_v3"]["key"] == [
                ("status", 1),
                ("claim_due_at", 1),
                ("created_at", 1),
                ("_id", 1),
                ("lease_until", 1),
                ("next_attempt_at", 1),
            ]
            assert information["ix_approval_delivery_sync_v1"]["key"] == [
                ("ready_at", 1),
                ("_id", 1),
            ]
            assert information["ix_approval_delivery_sync_v1"].get(
                "partialFilterExpression"
            ) == {"sync_required": True}
            assert "ix_approval_delivery_claim_v2" not in information
        finally:
            await client.close()

    asyncio.run(scenario())


def test_readiness_index_supports_the_reconciliation_scan(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """The readiness index must exist for the watermark-anchored scan."""

    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            preparations = database["content_preparations"]
            await initialize_content_preparation_indexes(
                database["media_items"], database["media_groups"], preparations
            )
            information = await preparations.index_information()
            assert information["ix_content_preparation_readiness_v1"]["key"] == [
                ("ready_at", 1),
                ("_id", 1),
            ]
        finally:
            await client.close()

    asyncio.run(scenario())
