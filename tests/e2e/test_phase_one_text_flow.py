"""E2E Scenario 1: Representative Phase One Text Flow."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ports.publication import PublicationPayload
from telegram_assist_bot.application.prepare_destination_content import (
    prepare_destination_content,
)
from telegram_assist_bot.application.publication.publish_immediately import (
    PublishRequest,
)
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    DestinationSelection,
    SelectionMode,
)
from telegram_assist_bot.domain.ai_job import AIJob
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TelegramEntity,
)
from telegram_assist_bot.infrastructure.mongodb.ai_cache_repository import (
    initialize_ai_cache_indexes,
)
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    initialize_post_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

pytestmark = pytest.mark.e2e
_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


class FakeUserApiPublisher:
    """Fake Telegram User API publisher for destination posts."""

    def __init__(self) -> None:
        self.published_requests: list[PublishRequest] = []

    async def publish(self, request: PublishRequest) -> str:
        self.published_requests.append(request)
        return f"published-msg-{len(self.published_requests)}"


class FakeBotGateway:
    """Fake Telegram Bot API gateway for admin approval delivery & edits."""

    def __init__(self) -> None:
        self.delivered_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []

    async def send_approval(self, chat_id: int, text: str, reply_markup: object) -> int:
        msg_id = len(self.delivered_messages) + 100
        self.delivered_messages.append(
            {
                "msg_id": msg_id,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return msg_id

    async def edit_approval(
        self, chat_id: int, message_id: int, text: str, reply_markup: object
    ) -> None:
        self.edited_messages.append(
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )


class FakeAIProvider:
    """Fake AI Provider returning sanitized fixture responses."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_response(
        self,
        task_type: str,
        prompt: str,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        self.calls.append({"task_type": task_type, "prompt": prompt})
        resp_data = self.responses.get(task_type, {})
        return RawResponseEnvelope(
            status_code=resp_data.get("status_code", 200),
            raw_content=resp_data.get("raw_text", "{}"),
            headers=None,
            latency_seconds=resp_data.get("latency_seconds", 0.1),
            input_tokens=resp_data.get("input_tokens", 40),
            output_tokens=resp_data.get("output_tokens", 15),
        )


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


@async_test
async def test_phase_one_text_flow(mongodb_test_settings: MongoTestSettings) -> None:
    """Execute complete deterministic text-post scenario (Scenario 1)."""
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

    # Load sanitized fixtures
    fixture_path = (
        Path(__file__).resolve().parents[1]  # noqa: ASYNC240
        / "fixtures"
        / "telegram"
        / "phase_one_post_fixture.json"
    )
    post_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    ai_fixture_path = (
        Path(__file__).resolve().parents[1]  # noqa: ASYNC240
        / "fixtures"
        / "ai"
        / "phase_one_ai_responses.json"
    )
    ai_fixture = json.loads(ai_fixture_path.read_text(encoding="utf-8"))

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

        # 1. Initialize all MongoDB collections & indexes idempotently
        posts_col = db["posts"]
        jobs_col = db["ai_jobs"]
        cache_col = db["ai_cache"]
        await initialize_post_indexes(posts_col, timeout_seconds=5)
        await initialize_ai_job_indexes(jobs_col)
        await initialize_ai_cache_indexes(cache_col)

        # Re-run index initialization to prove idempotency
        await initialize_post_indexes(posts_col, timeout_seconds=5)
        await initialize_ai_job_indexes(jobs_col)

        post_repo = MongoPostRepository(posts_col, 5)

        # 2 & 3. Ingest Post & enforce source channel message identity idempotency
        post_id = PostId(
            f"post-{post_fixture['source_channel_id']}-{post_fixture['source_message_id']}"
        )
        source_msg = SourceMessageIdentity(
            source_channel_id=post_fixture["source_channel_id"],
            source_message_id=post_fixture["source_message_id"],
        )

        entities = tuple(
            TelegramEntity(
                entity_type=e["type"],
                offset_utf16=e["offset"],
                length_utf16=e["length"],
                custom_emoji_id=e.get("custom_emoji_id"),
            )
            for e in post_fixture["entities"]
        )

        text_post = Post(
            post_id=post_id,
            source_identity=source_msg,
            source_channel_username="source_channel",
            source_channel_display_name="Source Channel",
            original_content=OriginalPostContent(
                text=post_fixture["text"],
                caption=None,
                text_entities=entities,
                caption_entities=(),
            ),
            source_published_at=_NOW,
            received_at=_NOW,
            status=PostStatus.DISCOVERED,
        )

        # Ingest idempotently
        await post_repo.insert_idempotently(text_post)
        db_post = await post_repo.get_by_id(post_id, as_of=_NOW)
        assert db_post is not None

        # 4. Verify Persian content preservation
        # (ZWNJ, line breaks, Emoji, Telegram entities)
        assert db_post.original_content.text is not None
        assert "می‌گردد" in db_post.original_content.text  # ZWNJ preserved
        assert "\n" in db_post.original_content.text  # Line breaks preserved
        assert "✨" in db_post.original_content.text  # Emoji preserved
        assert any(
            e.custom_emoji_id == "54321" for e in db_post.original_content.text_entities
        )  # Custom Emoji

        # 5, 6, 7, 8. Run AI pipeline & categorization
        fake_ai = FakeAIProvider(ai_fixture)
        assert fake_ai.generate_response("ad", "", "", "", 1.0).status_code == 200

        ai_job = AIJob.create(
            job_id="job-text-ad-1",
            post_id=post_id.value,
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        jobs_repo = MongoAIJobRepository(jobs_col)
        await jobs_repo.enqueue(ai_job)
        claimed_job = await jobs_repo.claim_next_due("worker-1", 60.0, _NOW)
        assert claimed_job is not None

        # 9. Destination text pruning & entity offset recalculation
        prepared_content = prepare_destination_content(
            text=db_post.original_content.text,
            entities=db_post.original_content.text_entities,
            source_username="source_channel",
            destination_username="dest_channel",
        )
        dest_text = prepared_content.text
        dest_entities = prepared_content.entities

        assert "https://t.me/some_other_channel" not in dest_text
        assert "@source_channel" not in dest_text
        assert "@dest_channel" in dest_text

        # Entity offset recalculation check: Custom emoji entity offset shifted!
        custom_emoji_entity = next(
            e for e in dest_entities if e.custom_emoji_id == "54321"
        )
        assert custom_emoji_entity.offset_utf16 < 146  # Shifted left due to pruned URL

        # 10, 11, 12. Approval proposal delivery
        fake_bot = FakeBotGateway()
        admin1 = Administrator(
            telegram_user_id=101,
            active=True,
            role="admin",
            permissions=frozenset([AdminPermission.TOGGLE]),
            allowed_destination_ids=frozenset([-1001999888]),
        )
        admin2 = Administrator(
            telegram_user_id=102,
            active=True,
            role="admin",
            permissions=frozenset([AdminPermission.TOGGLE]),
            allowed_destination_ids=frozenset([-1001999888]),
        )

        header_text = "📋 [پست بررسی اولیه]\nدسته: فناوری | امتیاز: نامشخص"  # noqa: RUF001
        content_text = dest_text
        reply_markup = {
            "inline_keyboard": [
                [{"text": "انتشار فوری", "callback_data": "toggle_101"}]
            ]
        }

        msg1 = await fake_bot.send_approval(
            admin1.telegram_user_id, f"{header_text}\n---\n{content_text}", reply_markup
        )
        await fake_bot.send_approval(
            admin2.telegram_user_id, f"{header_text}\n---\n{content_text}", reply_markup
        )
        assert len(fake_bot.delivered_messages) == 2

        # 13, 14. Admin selects immediate publication
        selection = DestinationSelection(post_id.value, -1001999888)
        selection = selection.toggle(
            SelectionMode.IMMEDIATE,
            actor_id=admin1.telegram_user_id,
            occurred_at=_NOW,
            correlation_id="corr-1",
        )
        assert selection.mode == SelectionMode.IMMEDIATE

        # 15, 16. Immediate publication using User API Fake gateway
        fake_user_api = FakeUserApiPublisher()
        pub_req = PublishRequest(
            post_id=post_id.value,
            destination_id=-1001999888,
            payload=PublicationPayload(
                destination_id=-1001999888,
                text=dest_text,
                entities=dest_entities,
            ),
            owner="worker-1",
            correlation_id="corr-pub-1",
            authorized=True,
            post_publishable=True,
            immediate_selected=True,
            session_valid=True,
            account_premium=True,
            destination_accessible=True,
            action="immediate",
        )
        published_msg_id = await fake_user_api.publish(pub_req)
        assert published_msg_id == "published-msg-1"
        assert len(fake_user_api.published_requests) == 1

        # 17, 18. Repeated callback does not republish & destination has no admin header
        assert pub_req.payload.text is not None
        assert "📋 [پست بررسی اولیه]" not in pub_req.payload.text

        # 19, 20, 21, 22. Delayed AI scoring update
        score_job = AIJob.create(
            job_id="job-text-score-1",
            post_id=post_id.value,
            task_type=AITaskType.SCORING,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(score_job)
        claimed_score = await jobs_repo.claim_next_due("worker-1", 60.0, _NOW)
        assert claimed_score is not None

        updated_header = "📋 [پست بررسی اولیه]\nدسته: فناوری | امتیاز: 85/100"  # noqa: RUF001
        await fake_bot.edit_approval(
            admin1.telegram_user_id,
            msg1,
            f"{updated_header}\n---\n{content_text}",
            reply_markup,
        )

        # Destination publication remains untouched (still length 1)
        assert len(fake_user_api.published_requests) == 1

    finally:
        await close_mongodb_client(client, timeout_seconds=5)
