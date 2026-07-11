from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from tests.integration.test_crawl_today_text_posts import (
    Clock,
    MongoTestSettings,
    resources,
)
from tests.unit.test_text_ingestion_bootstrap import Gateway, source_message

from telegram_assist_bot.bootstrap.runtime import create_foundation_application
from telegram_assist_bot.bootstrap.text_ingestion import (
    TextIngestionApplication,
    TextIngestionDependencies,
)
from telegram_assist_bot.domain.posts import PostId, TelegramEntity

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.shared.observability import RedactedValue

pytestmark = pytest.mark.e2e


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


class Sink:
    def __init__(self) -> None:
        self.events: list[Mapping[str, RedactedValue]] = []

    def __call__(self, event: Mapping[str, RedactedValue]) -> None:
        self.events.append(event)


def write_configuration(
    tmp_path: Path,
    settings: MongoTestSettings,
    session_path: Path,
) -> Path:
    root = Path(__file__).resolve().parents[2]
    payload = cast(
        "dict[str, object]",
        json.loads(
            (root / "config" / "configuration.example.json").read_text(encoding="utf-8")
        ),
    )
    payload = deepcopy(payload)
    cast("dict[str, object]", payload["mongodb"])["database_name"] = (
        settings.database_name
    )
    telegram = cast("dict[str, object]", payload["telegram"])
    cast("dict[str, object]", telegram["user"])["session_path"] = str(session_path)
    path = tmp_path / "milestone-one.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def environment(settings: MongoTestSettings) -> dict[str, str]:
    return {
        "TAB_MONGODB_URI": settings.uri,
        "TAB_TELEGRAM_API_ID": "123456",
        "TAB_TELEGRAM_API_HASH": "synthetic-api-hash",
        "TAB_TELEGRAM_PHONE_NUMBER": "synthetic-phone",
        "TAB_TELEGRAM_BOT_TOKEN": "synthetic-bot-value",
        "TAB_AI_PROVIDER_KEY": "synthetic-provider-value",
    }


async def no_sleep(_delay: float) -> None:
    return None


def create_application(gateway: Gateway, sink: Sink) -> TextIngestionApplication:
    return TextIngestionApplication(
        TextIngestionDependencies(
            foundation=create_foundation_application(sink=sink),
            gateway_factory=lambda _loaded: gateway,
            clock=Clock(),
            post_id_factory=lambda identity: PostId(
                f"post-{identity.source_channel_id}-{identity.source_message_id}"
            ),
            sleeper=no_sleep,
            jitter_source=lambda: 0.5,
        )
    )


def test_two_lifecycles_reuse_session_database_and_overlap_without_duplicates(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        session_path = tmp_path / "synthetic.session"
        session_value = "synthetic-session-v1"
        session_path.write_text(session_value, encoding="utf-8")
        config_path = write_configuration(
            tmp_path,
            mongodb_test_settings,
            session_path,
        )
        environ = environment(mongodb_test_settings)
        first_sink = Sink()
        rich_message = replace(
            source_message(1),
            text="سلام‌پایدار\nPremium 😀",
            text_entities=(TelegramEntity(13, 2, "custom_emoji", "987654"),),
        )
        first_gateway = Gateway(
            history=(rich_message,),
            live=[rich_message, source_message(2)],
            order=[],
        )
        first = create_application(first_gateway, first_sink)

        await first.start(config_path, environ=environ)
        await first.wait()
        await first.shutdown()

        second_sink = Sink()
        second_gateway = Gateway(
            history=(rich_message, source_message(2)),
            live=[source_message(3)],
            order=[],
        )
        second = create_application(second_gateway, second_sink)
        await second.start(config_path, environ=environ)
        await second.wait()
        await second.shutdown()

        async with resources(mongodb_test_settings) as owned:
            documents = await owned.collection.find({}).to_list(length=10)
            assert len(documents) == 3
            assert all(
                document["next_stage_claimed_at"] is not None for document in documents
            )
            assert all(document["expires_at"] is not None for document in documents)
            assert (
                sum(1 for document in documents if document["source_message_id"] == 1)
                == 1
            )
            first_document = next(
                document for document in documents if document["source_message_id"] == 1
            )
            original = cast("dict[str, object]", first_document["original_content"])
            assert original["text"] == "سلام‌پایدار\nPremium 😀"
            entities = cast("list[dict[str, object]]", original["text_entities"])
            assert entities[0]["custom_emoji_id"] == "987654"
        assert session_path.read_text(encoding="utf-8") == session_value
        rendered_logs = repr(first_sink.events) + repr(second_sink.events)
        for secret in environment(mongodb_test_settings).values():
            assert secret not in rendered_logs
        assert session_value not in rendered_logs
        assert first_gateway.close_calls == 1
        assert second_gateway.close_calls == 1

    run(scenario())
