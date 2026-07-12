"""Exercise Milestone 3 atomic persistence against guarded test MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from telegram_assist_bot.application.approvals import (
    AuthorizationStatus,
    AuthorizeAdminAction,
    CallbackStatus,
    CallbackTokenService,
    ToggleDestinationSelection,
    ToggleStatus,
)
from telegram_assist_bot.application.ports import BotUpdate
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    ApprovalReference,
    CallbackAction,
    DestinationSelection,
    SelectionMode,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.approval_repository import (
    MongoApprovalRepository,
    initialize_approval_indexes,
)
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

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class MongoTestSettings(Protocol):
    uri: str
    database_name: str


def test_callback_selection_reference_concurrency_and_restart(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        config = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config.database_name]
            callbacks = database["approval_callbacks"]
            references = database["approval_references"]
            selections = database["destination_selections"]
            await initialize_approval_indexes(callbacks, references, selections)
            await initialize_approval_indexes(callbacks, references, selections)
            repository = MongoApprovalRepository(callbacks, references, selections)
            now = datetime(2026, 7, 12, tzinfo=UTC)
            service = CallbackTokenService(repository, lambda size: b"a" * size)
            token = await service.issue(
                actor_id=101,
                action=CallbackAction.TOGGLE_IMMEDIATE,
                post_id="post",
                destination_id=-202,
                now=now,
            )
            assert (
                await service.resolve(token, actor_id=101, now=now)
            ).status is CallbackStatus.VALID
            assert (
                await service.resolve(token, actor_id=101, now=now + timedelta(days=14))
            ).status is CallbackStatus.EXPIRED
            info = await callbacks.index_information()
            assert info["ttl_callbacks_v1"]["expireAfterSeconds"] == 0

            current = DestinationSelection("post", -201)
            first = current.toggle(
                SelectionMode.IMMEDIATE,
                actor_id=101,
                occurred_at=now,
                correlation_id="a",
            )
            second = current.toggle(
                SelectionMode.SCHEDULED,
                actor_id=102,
                occurred_at=now,
                correlation_id="b",
            )
            outcomes = await asyncio.gather(
                repository.compare_and_set_selection(current, first),
                repository.compare_and_set_selection(current, second),
            )
            assert outcomes.count(True) == 1
            stored = await repository.get_selection("post", -201)
            assert stored.mode in {SelectionMode.IMMEDIATE, SelectionMode.SCHEDULED}
            assert stored.version == 1

            administrator = Administrator(
                101,
                True,
                "admin",
                frozenset({AdminPermission.TOGGLE}),
                frozenset({-202}),
            )
            authorize = AuthorizeAdminAction((administrator,))
            trusted = BotUpdate(101, 101, "private", token, "query")
            decision = authorize.execute(
                trusted, AdminPermission.TOGGLE, destination_id=-202
            )
            assert decision.status is AuthorizationStatus.ALLOWED
            resolution = await service.resolve_authorized(
                token,
                update=trusted,
                now=now,
                authorize=authorize,
                post_actionable=True,
            )
            assert resolution.status is CallbackStatus.VALID
            toggle = await ToggleDestinationSelection(repository, authorize).execute(
                trusted,
                post_id="post",
                destination_id=-202,
                requested=SelectionMode.IMMEDIATE,
                expected_version=0,
                post_actionable=True,
                now=now,
                correlation_id="flow",
            )
            assert toggle.status is ToggleStatus.UPDATED

            reference = ApprovalReference("ref", 101, 101, "post", 10, (11,))
            assert await repository.save_reference(reference) == reference
            assert await repository.save_reference(reference) == reference
            await repository.mark_sync_failure(
                "ref", 1, category="timeout", next_retry_at=now, inactive=False
            )
            repository = MongoApprovalRepository(callbacks, references, selections)
            claims = await asyncio.gather(
                repository.claim_retry(
                    "ref", now=now, lease_until=now + timedelta(seconds=30)
                ),
                repository.claim_retry(
                    "ref", now=now, lease_until=now + timedelta(seconds=30)
                ),
            )
            assert claims.count(True) == 1
            assert await repository.mark_sync_success("ref", 2)
            assert not await repository.mark_sync_success("ref", 1)
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
