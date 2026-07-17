"""Real-MongoDB workflow coverage for isolated semantic duplicate detection."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
from telegram_assist_bot.application.ai.task_handlers.semantic_duplicate import (
    SemanticDuplicateHandler,
    SemanticDuplicateHandlerOutcome,
)
from telegram_assist_bot.application.detect_semantic_duplicate import (
    DetectSemanticDuplicate,
)
from telegram_assist_bot.domain.advertisement import AdvertisementCheckResult
from telegram_assist_bot.domain.duplicates import (
    DuplicateCheckResult,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
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
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    MongoSemanticDuplicateCandidateRepository,
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

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        SemanticDuplicateCandidateRepository,
        SemanticDuplicatePostRepository,
    )

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


class MongoTestSettings(Protocol):
    """Describe the guarded local MongoDB fixture."""

    uri: str
    database_name: str


class _Clock:
    def utc_now(self) -> datetime:
        return _NOW


def _post(post_id: str, message_id: int, received_at: datetime, text: str) -> Post:
    post = Post(
        PostId(post_id),
        SourceMessageIdentity(-10043, message_id),
        "source",
        "منبع",
        OriginalPostContent(text, None),
        received_at - timedelta(minutes=1),
        received_at,
    ).transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )
    advertisement = AdvertisementCheckResult(
        False,
        0.9,
        "تبلیغ نیست",
        "provider-a",
        "model-a",
        received_at,
        "1.0.0",
        "1",
        1,
        0,
    )
    return post.start_advertisement_check(
        job_id=f"ad-{post_id}",
        expected_processing_version=0,
        requested_at=received_at,
    ).apply_advertisement_result(
        advertisement,
        job_id=f"ad-{post_id}",
        expected_processing_version=1,
    )


def _exact() -> DuplicateCheckResult:
    return DuplicateCheckResult(False, None, "ExactContentHash", 1, 1, "a" * 64, _NOW)


def _config(settings: MongoTestSettings) -> MongoConfig:
    return MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=settings.database_name,
        connect_timeout_seconds=5,
    )


def _result(
    *,
    duplicate: bool,
    similarity: float,
    reason: str,
    prompt_version: str,
    schema_version: str,
) -> AIResult:
    return AIResult(
        success=True,
        task_type=AITaskType.SEMANTIC_DUPLICATE,
        provider_name="provider-a",
        model_name="model-a",
        result={
            "is_duplicate": duplicate,
            "similarity": similarity,
            "confidence": 0.91,
            "reason": reason,
        },
        confidence=0.91,
        reason=reason,
        prompt_version=prompt_version,
        schema_version=schema_version,
        latency=None,
        input_tokens=None,
        output_tokens=None,
        attempt_number=1,
        fallback_count=0,
        created_at=_NOW,
    )


def test_candidate_query_workflow_and_legacy_compatibility(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Prove window/index rules, deterministic winner, CAS, and legacy defaults."""

    async def scenario() -> None:
        config = _config(mongodb_test_settings)
        client = create_mongodb_client(
            config,
            ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config.database_name]
            collection = database["posts"]
            jobs_collection = database["ai_jobs"]
            await initialize_post_indexes(collection, timeout_seconds=5)
            await initialize_ai_job_indexes(jobs_collection)
            posts = MongoPostRepository(collection, 5)
            jobs = MongoAIJobRepository(jobs_collection)
            candidates = MongoSemanticDuplicateCandidateRepository(collection, 5)

            current = _post("current", 1, _NOW - timedelta(minutes=1), "متن اصلی 🚀")
            boundary = _post("boundary", 2, _NOW - timedelta(days=14), "مرز مجاز")
            newest = _post("newest", 3, _NOW - timedelta(hours=1), "نامزد تازه")
            older = _post(
                "older", 4, _NOW - timedelta(days=14, microseconds=1), "قدیمی"
            )
            expired = _post("expired", 5, _NOW - timedelta(days=2), "منقضی")
            empty = _post("empty", 6, _NOW - timedelta(hours=2), " ")
            for item in (current, boundary, newest, older, expired, empty):
                document = post_to_document(item)
                if item.post_id == boundary.post_id:
                    document["expires_at"] = _NOW + timedelta(seconds=1)
                if item.post_id == expired.post_id:
                    document["expires_at"] = _NOW
                await collection.insert_one(document)

            found = await candidates.list_candidates(
                current_post_id=current.post_id,
                now=_NOW,
                window_start=_NOW - timedelta(days=14),
                limit=100,
            )
            assert [item.post_id.value for item in found] == ["newest", "boundary"]
            assert all(item.comparison_text for item in found)

            index_info = await collection.index_information()
            assert "ix_posts_semantic_window_v1" in index_info
            explain = await database.command(
                "explain",
                {
                    "find": "posts",
                    "filter": {
                        "_id": {"$ne": current.post_id.value},
                        "status": PostStatus.STORED.value,
                        "received_at": {
                            "$gte": _NOW - timedelta(days=14),
                            "$lte": _NOW,
                        },
                        "expires_at": {"$gt": _NOW},
                    },
                    "projection": {
                        "_id": 1,
                        "original_content.text": 1,
                        "original_content.caption": 1,
                        "received_at": 1,
                        "expires_at": 1,
                    },
                    "sort": {"received_at": -1, "_id": 1},
                },
                verbosity="queryPlanner",
            )
            assert "ix_posts_semantic_window_v1" in str(explain)

            legacy_document = post_to_document(
                _post("legacy", 7, _NOW - timedelta(minutes=3), "سند قدیمی")
            )
            del legacy_document["semantic_duplicate_processing"]
            await collection.insert_one(legacy_document)
            loaded_legacy = await posts.get_by_id(PostId("legacy"), as_of=_NOW)
            assert loaded_legacy is not None
            assert loaded_legacy.semantic_duplicate_state is (
                SemanticDuplicateState.NOT_REQUESTED
            )

            enqueue = DetectSemanticDuplicate(
                cast("AIJobRepository", jobs),
                cast("SemanticDuplicatePostRepository", posts),
                cast("SemanticDuplicateCandidateRepository", candidates),
                PromptRegistry(),
                _Clock(),
            )
            queued, duplicate = await asyncio.gather(
                enqueue.execute(
                    current,
                    exact_result=_exact(),
                    global_enabled=True,
                    source_enabled=True,
                    threshold=0.88,
                    duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
                    failure_policy=SemanticDuplicateFailurePolicy.MANUAL_REVIEW,
                ),
                enqueue.execute(
                    current,
                    exact_result=_exact(),
                    global_enabled=True,
                    source_enabled=True,
                    threshold=0.88,
                    duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
                    failure_policy=SemanticDuplicateFailurePolicy.MANUAL_REVIEW,
                ),
            )
            assert queued.job is not None
            assert duplicate.job is not None
            assert queued.job.job_id == duplicate.job.job_id
            assert await jobs_collection.count_documents({}) == 1

            pending = await posts.get_by_id(current.post_id, as_of=_NOW)
            assert pending is not None
            claimed = await jobs.claim_next_due("worker", 60, _NOW)
            assert claimed is not None
            first = _result(
                duplicate=True,
                similarity=0.9,
                reason="شباهت معتبر",
                prompt_version=claimed.prompt_version,
                schema_version=claimed.schema_version,
            )
            best = _result(
                duplicate=True,
                similarity=0.95,
                reason="بیشترین شباهت",
                prompt_version=claimed.prompt_version,
                schema_version=claimed.schema_version,
            )
            completed = claimed.complete("worker", {}, _NOW)
            completed = replace(
                completed,
                normalized_result=best.model_dump(mode="json"),
                semantic_candidate_results=[
                    {
                        "candidate_post_id": "newest",
                        "result": first.model_dump(mode="json"),
                    },
                    {
                        "candidate_post_id": "boundary",
                        "result": best.model_dump(mode="json"),
                    },
                ],
            )
            await jobs.update(completed)
            handler = SemanticDuplicateHandler(
                cast("SemanticDuplicatePostRepository", posts),
                cast("AIJobRepository", jobs),
                cast("SemanticDuplicateCandidateRepository", candidates),
                _Clock(),
            )
            outcomes = await asyncio.gather(
                handler.complete(
                    job_id=completed.job_id,
                    expected_job_version=completed.version,
                    threshold=0.88,
                    duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
                ),
                handler.complete(
                    job_id=completed.job_id,
                    expected_job_version=completed.version,
                    threshold=0.88,
                    duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
                ),
            )
            assert (
                sum(
                    item.outcome is SemanticDuplicateHandlerOutcome.APPLIED
                    for item in outcomes
                )
                == 1
            )
            persisted = await posts.get_by_id(current.post_id, as_of=_NOW)
            assert persisted is not None
            assert persisted.semantic_duplicate_result is not None
            assert persisted.semantic_duplicate_result.matched_post_id == PostId(
                "boundary"
            )
            assert persisted.semantic_duplicate_requires_manual_review
            assert persisted.original_text == current.original_text

            serialized = str(
                await collection.find_one({"_id": current.post_id.value})
            ).lower()
            for forbidden in (
                "synthetic-secret-marker",
                "authorization: bearer",
                "raw provider response",
            ):
                assert forbidden not in serialized
        finally:
            await client.drop_database(config.database_name)
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
