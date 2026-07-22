"""MongoDB workflow tests for durable delayed AI scoring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol
from unittest.mock import MagicMock

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.use_cases.apply_ai_score import (
    ApplyAIScore,
    ApplyScoreOutcome,
)
from telegram_assist_bot.application.use_cases.schedule_ai_scoring import (
    ApprovalScoringBoundary,
    ScheduleAIScoring,
    ScoringScheduleOutcome,
)
from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckResult,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
)
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicateResult,
    SemanticDuplicateState,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)
from telegram_assist_bot.domain.scoring import ScoringState
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalPostLoader,
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    initialize_post_indexes,
    post_to_document,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


class MongoTestSettings(Protocol):
    """Describe the guarded local MongoDB fixture."""

    uri: str
    database_name: str


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def utc_now(self) -> datetime:
        return self.value


def _eligible_post() -> Post:
    advertisement = AdvertisementCheckResult(
        False,
        0.9,
        "تبلیغ نیست",
        "provider",
        "model",
        _NOW,
        "1.0.0",
        "1",
        1,
        0,
    )
    duplicate = SemanticDuplicateResult(
        False,
        0.2,
        0.9,
        None,
        "تکراری نیست",
        "provider",
        "model",
        _NOW,
        "2.0.0",
        "2",
        1,
        0,
    )
    category = CategorizationResult(
        "news",
        CategorizationMethod.SOURCE_DEFAULT,
        1,
        _NOW,
        reason="source_default",
    )
    post = Post(
        PostId("post-delayed-score"),
        SourceMessageIdentity(-1001, 9001),
        "source",
        "منبع",
        OriginalPostContent("متن فارسی با نیم‌فاصله و Emoji ✨", None),
        _NOW - timedelta(minutes=30),
        _NOW - timedelta(minutes=29),
        advertisement_state=AdvertisementProcessingState.PASSED,
        advertisement_processing_version=1,
        advertisement_job_id="job-ad",
        advertisement_result=advertisement,
        semantic_duplicate_state=SemanticDuplicateState.PASSED,
        semantic_duplicate_version=1,
        semantic_duplicate_job_id="job-sem",
        semantic_duplicate_result=duplicate,
        categorization_state=CategorizationState.SOURCE_DEFAULT_FALLBACK,
        categorization_processing_version=1,
        categorization_result=category,
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW - timedelta(minutes=29),
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def _configuration() -> MagicMock:
    config = MagicMock()
    config.features.ai_scoring_enabled = True
    config.scoring.delay_seconds = 1200
    config.scoring.failure_policy.value = "retry_later"
    config.ai.queue.max_attempts = 3
    return config


def test_delayed_scoring_is_restart_safe_and_persists_once(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Persist one due Job and one score across repository reconstruction."""

    async def scenario() -> None:
        mongo = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            mongo,
            ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[mongo.database_name]
            posts_collection = database["posts"]
            jobs_collection = database["ai_jobs"]
            preparations_collection = database["content_preparations"]
            media_collection = database["media_items"]
            groups_collection = database["media_groups"]
            await initialize_post_indexes(posts_collection, timeout_seconds=5)
            await initialize_ai_job_indexes(jobs_collection)
            posts = MongoPostRepository(posts_collection, 5)
            jobs = MongoAIJobRepository(jobs_collection)
            post = _eligible_post()
            await posts_collection.insert_one(post_to_document(post))
            clock = _Clock(_NOW)

            scheduled = await ScheduleAIScoring(
                _configuration(), posts, EnqueueAIJob(jobs, clock)
            ).execute(post, boundary=ApprovalScoringBoundary.READY)
            assert scheduled.outcome is ScoringScheduleOutcome.SCHEDULED
            assert scheduled.job is not None
            due_at = post.source_published_at + timedelta(seconds=1200)
            assert scheduled.job.next_run_at == due_at
            assert (
                await jobs.claim_next_due(
                    "worker-before", 60, due_at - timedelta(microseconds=1)
                )
                is None
            )

            restarted_jobs = MongoAIJobRepository(jobs_collection)
            claimed = await restarted_jobs.claim_next_due("worker-a", 60, due_at)
            assert claimed is not None
            assert claimed.job_id == scheduled.job.job_id
            result = AIResult(
                success=True,
                task_type=AITaskType.SCORING,
                result={
                    "score": 0,
                    "confidence": 1.0,
                    "reason": "امتیاز صفر معتبر است ✨",
                },
                confidence=None,
                reason=None,
                latency=None,
                input_tokens=None,
                output_tokens=None,
                provider_name="provider-a",
                model_name="model-a",
                prompt_version="2.0.0",
                schema_version="2",
                cache_hit=False,
                created_at=_NOW,
                attempt_number=1,
                fallback_count=0,
            )
            await jobs_collection.update_one(
                {"_id": claimed.job_id, "version": claimed.version},
                {
                    "$set": {
                        "status": AIJobStatus.COMPLETED.value,
                        "normalized_result": result.model_dump(mode="json"),
                        "updated_at": _NOW,
                    }
                },
            )
            use_case = ApplyAIScore(posts, restarted_jobs, clock, _configuration())
            first = await use_case.complete(
                job_id=claimed.job_id, expected_job_version=claimed.version
            )
            second = await use_case.complete(
                job_id=claimed.job_id, expected_job_version=claimed.version
            )
            assert first.outcome is ApplyScoreOutcome.APPLIED
            assert second.outcome is ApplyScoreOutcome.IDEMPOTENT
            loaded = await posts.get_by_id(post.post_id, as_of=_NOW)
            assert loaded is not None
            assert loaded.scoring_state is ScoringState.COMPLETED
            assert loaded.scoring_result is not None
            assert loaded.scoring_result.score == 0
            await preparations_collection.insert_one(
                {"_id": post.post_id.value, "ready_at": _NOW, "artifacts": {}}
            )
            approval_post = await MongoApprovalPostLoader(
                posts_collection,
                preparations_collection,
                media_collection,
                groups_collection,
                destination_names=(),
            ).load(post.post_id.value)
            assert approval_post.score == "0"
            assert (
                await jobs_collection.count_documents(
                    {
                        "post_id": post.post_id.value,
                        "task_type": AITaskType.SCORING.value,
                    }
                )
                == 1
            )
        finally:
            await client.drop_database(mongo.database_name)
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_legacy_post_without_scoring_fields_loads_not_requested(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Keep additive mapper compatibility with pre-T045 Post documents."""

    async def scenario() -> None:
        mongo = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            mongo,
            ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            collection = client[mongo.database_name]["posts"]
            document = post_to_document(_eligible_post())
            document.pop("scoring_processing", None)
            await collection.insert_one(document)
            loaded = await MongoPostRepository(collection, 5).get_by_id(
                PostId("post-delayed-score"), as_of=_NOW
            )
            assert loaded is not None
            assert loaded.scoring_state is ScoringState.NOT_REQUESTED
            assert loaded.scoring_result is None
        finally:
            await client.drop_database(mongo.database_name)
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
