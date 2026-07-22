"""E2E Scenario 3: Restart, Failure Recovery, Idempotency & Security."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.approvals import (
    AuthorizationStatus,
    AuthorizeAdminAction,
    CallbackStatus,
    CallbackTokenService,
)
from telegram_assist_bot.application.ports.admin import BotUpdate
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    CallbackAction,
)
from telegram_assist_bot.domain.ai_job import AIJob
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
)
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    initialize_post_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.approval_repository import (
    MongoApprovalRepository,
    initialize_approval_indexes,
)
from telegram_assist_bot.shared.observability import Redactor

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

pytestmark = pytest.mark.e2e
_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


@async_test
async def test_phase_one_restart_idempotency(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Execute restart recovery, concurrency, idempotency & security."""
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        close_mongodb_client,
        create_mongodb_client,
        verify_mongodb_connection,
    )
    from telegram_assist_bot.shared.config import (
        MongoConfig,
        ResolvedSecrets,
        SecretReference,
    )

    uri_env = "TEST_MONGODB_URI"
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=uri_env),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo, ResolvedSecrets({uri_env: mongodb_test_settings.uri})
    )

    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        db = client[config_mongo.database_name]

        posts_col = db["posts"]
        jobs_col = db["ai_jobs"]
        callbacks_col = db["approval_callbacks"]
        references_col = db["approval_references"]
        selections_col = db["destination_selections"]

        await initialize_post_indexes(posts_col, timeout_seconds=5)
        await initialize_ai_job_indexes(jobs_col)
        await initialize_approval_indexes(callbacks_col, references_col, selections_col)

        post_repo = MongoPostRepository(posts_col, 5)
        jobs_repo = MongoAIJobRepository(jobs_col)
        approval_repo = MongoApprovalRepository(
            callbacks_col, references_col, selections_col
        )

        # 1. Ingestion Idempotency & Shutdown/Restart Continuity
        source = SourceMessageIdentity(
            source_channel_id=-1001555444, source_message_id=77
        )
        post_id = PostId(f"post-{source.source_channel_id}-{source.source_message_id}")

        text_post = Post(
            post_id=post_id,
            source_identity=source,
            source_channel_username="source_channel",
            source_channel_display_name="Source Channel",
            original_content=OriginalPostContent(
                text="تست بازیابی و هم‌زمانی پس از Restart",
                caption=None,
                text_entities=(),
                caption_entities=(),
            ),
            source_published_at=_NOW,
            received_at=_NOW,
            status=PostStatus.DISCOVERED,
        )

        res1 = await post_repo.insert_idempotently(text_post)
        assert res1 is not None

        # Duplicate Telegram event for same post_id
        res2 = await post_repo.insert_idempotently(text_post)
        assert res2 is not None

        # 2. AI Job Claim, Lease Expiration & Stale Owner Rejection
        ai_job = AIJob.create(
            job_id="job-sec-1",
            post_id=post_id.value,
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(ai_job)

        # Worker 1 claims job with a short 5-second lease
        claimed_w1 = await jobs_repo.claim_next_due("worker-1", 5.0, _NOW)
        assert claimed_w1 is not None
        assert claimed_w1.lease_owner == "worker-1"

        # Worker 2 attempts claim before lease expiry -> gets None (mutual exclusion)
        claimed_w2_early = await jobs_repo.claim_next_due(
            "worker-2", 5.0, _NOW + timedelta(seconds=2)
        )
        assert claimed_w2_early is None

        # Time passes beyond 5-second lease expiry -> Worker 2 claims expired lease
        claimed_w2_after_expiry = await jobs_repo.claim_next_due(
            "worker-2", 5.0, _NOW + timedelta(seconds=6)
        )
        assert claimed_w2_after_expiry is not None
        assert claimed_w2_after_expiry.lease_owner == "worker-2"

        # Stale Worker 1 attempts to complete job using old version
        # -> update fails (optimistic concurrency)
        stale_completed_w1 = claimed_w1.complete(
            "worker-1", {"is_ad": False}, _NOW + timedelta(seconds=3)
        )
        with pytest.raises(Exception, match=r".*"):
            await jobs_repo.update(stale_completed_w1)

        # 3. Security Acceptance: Unauthorized Admin Callback & Forged Token Rejection
        token_service = CallbackTokenService(approval_repo, lambda size: b"b" * size)

        # Token issued for authorized admin 101
        valid_token = await token_service.issue(
            actor_id=101,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id=post_id.value,
            destination_id=-1001555444,
            now=_NOW,
        )

        # Unauthorized Admin 999 attempts to resolve valid token
        auth_admin = Administrator(
            telegram_user_id=101,
            active=True,
            role="admin",
            permissions=frozenset([AdminPermission.TOGGLE]),
            allowed_destination_ids=frozenset([-1001555444]),
        )
        auth_use_case = AuthorizeAdminAction(administrators=(auth_admin,))
        unauth_update = BotUpdate(
            actor_id=999,
            chat_id=999,
            chat_type="private",
            callback_data=None,
        )
        auth_decision = auth_use_case.execute(unauth_update, AdminPermission.TOGGLE)
        assert auth_decision.status is AuthorizationStatus.DENIED

        # Forged/invalid callback token resolution
        forged_result = await token_service.resolve(
            "forged-invalid-token", actor_id=101, now=_NOW
        )
        assert forged_result.status is CallbackStatus.INVALID

        # Resolve valid token for authorized actor 101
        valid_result = await token_service.resolve(valid_token, actor_id=101, now=_NOW)
        assert valid_result.status is CallbackStatus.VALID

        # Resolving expired token
        expired_result = await token_service.resolve(
            valid_token, actor_id=101, now=_NOW + timedelta(days=30)
        )
        assert expired_result.status is CallbackStatus.EXPIRED

        # 4. Structured Logging & Secret Redaction Safety
        secret_sample = (
            "Auth failure: bot_token=12345:abcdefghijklmnopqrstuvwxyz "  # noqa: S105
            "Authorization: Bearer secret_tok"
        )
        redacted = Redactor().redact(secret_sample)
        assert "12345:abcdefghijklmnopqrstuvwxyz" not in str(redacted)
        assert "secret_tok" not in str(redacted)

    finally:
        await close_mongodb_client(client, timeout_seconds=5)
