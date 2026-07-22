"""MongoDB workflow tests for isolated advertisement detection."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
from telegram_assist_bot.application.ai.task_handlers.advertisement_detection import (
    AdvertisementDetectionHandler,
    AdvertisementHandlerOutcome,
)
from telegram_assist_bot.application.detect_advertisement import DetectAdvertisement
from telegram_assist_bot.domain.advertisement import (
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
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
        AdvertisementPostRepository,
        AIJobRepository,
    )

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


class MongoTestSettings(Protocol):
    """Describe the guarded local MongoDB fixture."""

    uri: str
    database_name: str


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def utc_now(self) -> datetime:
        return self.now


def _post(post_id: str, message_id: int) -> Post:
    discovered = Post(
        post_id=PostId(post_id),
        source_identity=SourceMessageIdentity(-10042, message_id),
        source_channel_username="source",
        source_channel_display_name="منبع",
        original_content=OriginalPostContent(
            "متن فارسی با نیم‌فاصله و Emoji 🚀",
            None,
        ),
        source_published_at=_NOW - timedelta(minutes=1),
        received_at=_NOW - timedelta(seconds=1),
    )
    return discovered.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def _mongo_config(settings: MongoTestSettings) -> MongoConfig:
    return MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=settings.database_name,
        connect_timeout_seconds=5,
    )


def test_advertisement_success_failure_concurrency_and_legacy_compatibility(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Prove durable unique work, CAS outcomes, failure policy, and legacy reads."""

    async def scenario() -> None:
        config = _mongo_config(mongodb_test_settings)
        client = create_mongodb_client(
            config,
            ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config.database_name]
            posts_collection = database["posts"]
            jobs_collection = database["ai_jobs"]
            await initialize_post_indexes(posts_collection, timeout_seconds=5)
            await initialize_ai_job_indexes(jobs_collection)
            posts = MongoPostRepository(posts_collection, 5)
            jobs = MongoAIJobRepository(jobs_collection)
            clock = _Clock(_NOW + timedelta(seconds=1))

            legacy = _post("legacy-ad-post", 1)
            legacy_document = post_to_document(legacy)
            del legacy_document["advertisement_processing"]
            await posts_collection.insert_one(legacy_document)
            loaded_legacy = await posts.get_by_id(legacy.post_id, as_of=clock.utc_now())
            assert loaded_legacy is not None
            assert loaded_legacy.advertisement_state is (
                AdvertisementProcessingState.NOT_REQUESTED
            )

            enqueue = DetectAdvertisement(
                cast("AIJobRepository", jobs),
                cast("AdvertisementPostRepository", posts),
                PromptRegistry(),
                clock,
            )
            first, duplicate = await asyncio.gather(
                enqueue.execute(
                    loaded_legacy,
                    global_enabled=True,
                    source_enabled=True,
                    failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
                ),
                enqueue.execute(
                    loaded_legacy,
                    global_enabled=True,
                    source_enabled=True,
                    failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
                ),
            )
            assert first.job is not None
            assert duplicate.job is not None
            assert first.job.job_id == duplicate.job.job_id
            assert await jobs_collection.count_documents({}) == 1

            claimed = await jobs.claim_next_due(
                "worker-a",
                60,
                clock.utc_now(),
            )
            assert claimed is not None
            ai_result = AIResult(
                success=True,
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                provider_name="provider-a",
                model_name="model-a",
                result={
                    "is_advertisement": True,
                    "confidence": 0.2,
                    "reason": "تبلیغ تشخیص داده شد",
                },
                confidence=0.2,
                reason="تبلیغ تشخیص داده شد",
                prompt_version=claimed.prompt_version,
                schema_version=claimed.schema_version,
                latency=None,
                input_tokens=None,
                output_tokens=None,
                attempt_number=1,
                fallback_count=0,
                created_at=clock.utc_now(),
            )
            completed = claimed.complete(
                "worker-a",
                ai_result.result or {},
                clock.utc_now(),
            )
            completed = replace(
                completed,
                normalized_result=ai_result.model_dump(mode="json"),
            )
            await jobs.update(completed)
            handler = AdvertisementDetectionHandler(
                cast("AdvertisementPostRepository", posts),
                cast("AIJobRepository", jobs),
                clock,
            )
            outcomes = await asyncio.gather(
                handler.complete(
                    job_id=completed.job_id,
                    expected_job_version=completed.version,
                ),
                handler.complete(
                    job_id=completed.job_id,
                    expected_job_version=completed.version,
                ),
            )
            assert (
                sum(
                    item.outcome is AdvertisementHandlerOutcome.APPLIED
                    for item in outcomes
                )
                == 1
            )
            persisted = await posts.get_by_id(legacy.post_id, as_of=clock.utc_now())
            assert persisted is not None
            assert persisted.advertisement_state is (
                AdvertisementProcessingState.REJECTED_AS_ADVERTISEMENT
            )
            assert persisted.advertisement_result is not None
            assert persisted.original_text == legacy.original_text

            failed_post = _post("failed-ad-post", 2)
            await posts.insert_idempotently(failed_post)
            queued = await enqueue.execute(
                failed_post,
                global_enabled=True,
                source_enabled=True,
                failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
                max_attempts=1,
            )
            assert queued.job is not None
            claimed_failure = await jobs.claim_next_due(
                "worker-b",
                60,
                clock.utc_now(),
            )
            assert claimed_failure is not None
            terminal = claimed_failure.fail(
                "worker-b",
                "safe_failure",
                30,
                clock.utc_now(),
            )
            terminal = replace(
                terminal,
                attempted_candidates_count=2,
                retry_count=1,
                fallback_count=1,
                safe_last_failure_code="timeout",
            )
            assert terminal.status is AIJobStatus.ALL_PROVIDERS_FAILED
            await jobs.update(terminal)
            failed = await handler.fail(
                job_id=terminal.job_id,
                expected_job_version=terminal.version,
                policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
            )
            assert failed.outcome is AdvertisementHandlerOutcome.APPLIED
            failed_persisted = await posts.get_by_id(
                failed_post.post_id,
                as_of=clock.utc_now(),
            )
            assert failed_persisted is not None
            assert failed_persisted.advertisement_requires_manual_review
            assert (
                failed_persisted.advertisement_manual_review_reason
                == "advertisement_check_failed"
            )
            assert failed_persisted.advertisement_result is None

            non_advertising_post = _post("non-advertising-post", 3)
            await posts.insert_idempotently(non_advertising_post)
            non_advertising_job = await enqueue.execute(
                non_advertising_post,
                global_enabled=True,
                source_enabled=True,
                failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
            )
            assert non_advertising_job.job is not None
            claimed_non_advertising = await jobs.claim_next_due(
                "worker-non-advertising",
                60,
                clock.utc_now(),
            )
            assert claimed_non_advertising is not None
            non_advertising_result = AIResult(
                success=True,
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                provider_name="provider-a",
                model_name="model-a",
                result={
                    "is_advertisement": False,
                    "confidence": 0.0,
                    "reason": "تبلیغ نیست",
                },
                confidence=0.0,
                reason="تبلیغ نیست",
                prompt_version=claimed_non_advertising.prompt_version,
                schema_version=claimed_non_advertising.schema_version,
                latency=None,
                input_tokens=None,
                output_tokens=None,
                attempt_number=1,
                fallback_count=0,
                created_at=clock.utc_now(),
            )
            completed_non_advertising = claimed_non_advertising.complete(
                "worker-non-advertising",
                non_advertising_result.result or {},
                clock.utc_now(),
            )
            completed_non_advertising = replace(
                completed_non_advertising,
                normalized_result=non_advertising_result.model_dump(mode="json"),
            )
            await jobs.update(completed_non_advertising)
            non_advertising_handled = await handler.complete(
                job_id=completed_non_advertising.job_id,
                expected_job_version=completed_non_advertising.version,
            )
            assert non_advertising_handled.outcome is (
                AdvertisementHandlerOutcome.APPLIED
            )
            non_advertising_persisted = await posts.get_by_id(
                non_advertising_post.post_id,
                as_of=clock.utc_now(),
            )
            assert non_advertising_persisted is not None
            assert non_advertising_persisted.advertisement_state is (
                AdvertisementProcessingState.PASSED
            )

            policy_states = (
                (
                    AdvertisementFailurePolicy.CONTINUE_PROCESSING,
                    AdvertisementProcessingState.FAILED_CONTINUE,
                ),
                (
                    AdvertisementFailurePolicy.STOP_PROCESSING,
                    AdvertisementProcessingState.PROCESSING_STOPPED,
                ),
                (
                    AdvertisementFailurePolicy.RETRY_LATER,
                    AdvertisementProcessingState.RETRY_PENDING,
                ),
            )
            for offset, (policy, expected_state) in enumerate(
                policy_states,
                start=4,
            ):
                policy_post = _post(f"failure-policy-{policy.value}", offset)
                await posts.insert_idempotently(policy_post)
                policy_job = await enqueue.execute(
                    policy_post,
                    global_enabled=True,
                    source_enabled=True,
                    failure_policy=policy,
                    max_attempts=(
                        2 if policy is AdvertisementFailurePolicy.RETRY_LATER else 1
                    ),
                )
                assert policy_job.job is not None
                claimed_policy = await jobs.claim_next_due(
                    f"worker-{offset}",
                    60,
                    clock.utc_now(),
                )
                assert claimed_policy is not None
                failed_policy = claimed_policy.fail(
                    f"worker-{offset}",
                    "safe_failure",
                    30,
                    clock.utc_now(),
                )
                failed_policy = replace(
                    failed_policy,
                    attempted_candidates_count=1,
                    retry_count=1,
                    fallback_count=0,
                    safe_last_failure_code="timeout",
                )
                await jobs.update(failed_policy)
                handled_policy = await handler.fail(
                    job_id=failed_policy.job_id,
                    expected_job_version=failed_policy.version,
                    policy=policy,
                )
                expected_outcome = (
                    AdvertisementHandlerOutcome.RETRY_SCHEDULED
                    if policy is AdvertisementFailurePolicy.RETRY_LATER
                    else AdvertisementHandlerOutcome.APPLIED
                )
                assert handled_policy.outcome is expected_outcome
                policy_persisted = await posts.get_by_id(
                    policy_post.post_id,
                    as_of=clock.utc_now(),
                )
                assert policy_persisted is not None
                assert policy_persisted.advertisement_state is expected_state
                assert policy_persisted.advertisement_result is None

            job_document = await jobs_collection.find_one({"_id": terminal.job_id})
            post_document = await posts_collection.find_one(
                {"_id": failed_post.post_id.value}
            )
            serialized = f"{job_document}{post_document}".lower()
            for forbidden in (
                "synthetic-secret-marker",
                "authorization: bearer",
                "raw provider response",
            ):
                assert forbidden not in serialized
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
