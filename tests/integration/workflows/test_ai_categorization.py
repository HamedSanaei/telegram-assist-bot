"""MongoDB workflow integration tests for AI categorization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ai.task_handlers.categorization import (
    CategorizationHandler,
    CategorizationHandlerOutcome,
)
from telegram_assist_bot.application.prepare_post_pipeline import PreparationInput
from telegram_assist_bot.application.use_cases.categorize_with_ai import (
    CategorizeWithAI,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.domain.categories import (
    Category,
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
    MongoApprovalPostLoader,
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    initialize_post_indexes,
    post_to_document,
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

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class MongoTestSettings(Protocol):
    """Describe the guarded local MongoDB fixture."""

    uri: str
    database_name: str


class _Clock:
    def __init__(self, time: datetime) -> None:
        self.time = time

    def utc_now(self) -> datetime:
        return self.time


def _post(post_id: str, message_id: int) -> Post:
    post = Post(
        post_id=PostId(post_id),
        source_identity=SourceMessageIdentity(-1001, message_id),
        source_channel_username="source",
        source_channel_display_name="منبع",
        original_content=OriginalPostContent("متن پست تستی", None),
        source_published_at=_NOW - timedelta(minutes=1),
        received_at=_NOW,
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def test_ai_categorization_full_workflow(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify enqueuing, completing, and loading rich approval headers."""

    async def scenario() -> None:
        config_mongo = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            config_mongo,
            ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config_mongo.database_name]

            posts_collection = database["posts"]
            jobs_collection = database["ai_jobs"]
            media_collection = database["media_items"]
            groups_collection = database["media_groups"]
            preps_collection = database["content_preparations"]

            await initialize_post_indexes(posts_collection, timeout_seconds=5)
            await initialize_ai_job_indexes(jobs_collection)
            await initialize_content_preparation_indexes(
                media_collection, groups_collection, preps_collection
            )

            posts = MongoPostRepository(posts_collection, 5)
            jobs = MongoAIJobRepository(jobs_collection)
            preps = MongoContentPreparationRepository(
                media_collection, groups_collection, preps_collection
            )

            clock = _Clock(_NOW + timedelta(seconds=1))

            # Create config
            from unittest.mock import MagicMock

            config = MagicMock()
            config.features.ai_categorization_enabled = True
            config.features.advertisement_detection_enabled = False
            config.features.duplicate_detection_enabled = False
            config.categorization.categories = [
                Category("news", "اخبار"),
                Category("sports", "ورزش"),
            ]
            config.categorization.keyword_rules = []
            config.categorization.method_order = ("ai", "source_default")
            config.categorization.fallback_policy = "fallback_baseline"
            config.categorization.aliases = {"sport": "sports"}

            source_chan_config = MagicMock()
            source_chan_config.telegram_channel_id = -1001
            source_chan_config.default_category_id = "news"
            config.source_channels = [source_chan_config]

            post = _post("post-cat-test-1", 101)
            await posts_collection.insert_one(post_to_document(post))

            # 1. Enqueue AI categorization job
            enqueue = EnqueueAIJob(jobs, clock)
            categorize = CategorizeWithAI(
                config=config,
                content_repo=preps,
                post_repo=posts,
                enqueue_job=enqueue,
                clock=clock,
            )

            request = PreparationInput(
                post_id=post.post_id,
                text="متن پست تستی",
                caption=None,
                entities=(),
                source_username="source",
                media_hashes=(),
                categories=(Category("news", "اخبار"), Category("sports", "ورزش")),
                category_rules=(),
                source_default_category_id="news",
                destinations=(),
                now=_NOW,
            )

            res = await categorize.execute(request, post)
            assert res is None

            # Verify job was enqueued
            job_doc = await jobs_collection.find_one({"post_id": post.post_id.value})
            assert job_doc is not None
            assert job_doc["task_type"] == "categorization"

            # Verify post is pending
            post_doc = await posts_collection.find_one({"_id": post.post_id.value})
            assert post_doc is not None
            pending_processing = post_doc["categorization_processing"]
            assert isinstance(pending_processing, dict)
            assert pending_processing["state"] == "CategorizationPending"

            # 2. Complete the AI job with a valid output (sports category)
            loaded_post = await posts.get_by_id(post.post_id, as_of=clock.utc_now())
            assert loaded_post is not None

            claimed = await jobs.claim_next_due("worker-a", 60, clock.utc_now())
            assert claimed is not None

            # Set normalized result on job (simulate AI runner output)
            ai_res = AIResult(
                success=True,
                task_type=AITaskType.CATEGORIZATION,
                result={
                    "category_id": "sports",
                    "confidence": 0.95,
                    "reason": "ورزشی است",
                },
                confidence=0.95,
                reason="ورزشی است",
                latency=None,
                input_tokens=None,
                output_tokens=None,
                provider_name="deepseek",
                model_name="deepseek-v4-flash",
                prompt_version="2.0.0",
                schema_version="2",
                cache_hit=False,
                created_at=clock.utc_now(),
                attempt_number=1,
                fallback_count=0,
            )

            await jobs_collection.update_one(
                {"_id": claimed.job_id},
                {
                    "$set": {
                        "status": AIJobStatus.COMPLETED.value,
                        "normalized_result": ai_res.model_dump(),
                        "updated_at": clock.utc_now(),
                    }
                },
            )

            handler = CategorizationHandler(
                posts=posts,
                ai_jobs=jobs,
                content_preparations=preps,
                clock=clock,
                config=config,
            )

            handle_res = await handler.complete(
                job_id=claimed.job_id, expected_job_version=claimed.version
            )
            assert handle_res.outcome is CategorizationHandlerOutcome.APPLIED

            # 3. Load Post and verify categorization_state is AiAssigned
            post_doc = await posts_collection.find_one({"_id": post.post_id.value})
            assert post_doc is not None
            assigned_processing = post_doc["categorization_processing"]
            assert isinstance(assigned_processing, dict)
            assert assigned_processing["state"] == "AiAssigned"

            # 4. Load via MongoApprovalPostLoader and verify rich header text
            # Set preparation ready_at so loader can read it
            await preps_collection.update_one(
                {"_id": post.post_id.value}, {"$set": {"ready_at": clock.utc_now()}}
            )

            loader = MongoApprovalPostLoader(
                posts_collection,
                preps_collection,
                media_collection,
                groups_collection,
                destination_names=("dest1",),
                categories=(Category("news", "اخبار"), Category("sports", "ورزش")),
            )

            approval_post = await loader.load(post.post_id.value)
            assert (
                approval_post.category
                == "ورزش (هوش مصنوعی: 95% - deepseek/deepseek-v4-flash)"
            )

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
