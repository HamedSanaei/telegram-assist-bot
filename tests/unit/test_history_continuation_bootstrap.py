"""Prove a startup page budget schedules durable continuations, not source replay."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import (
    TelegramHistoryPage,
    TelegramHistoryQuery,
)
from telegram_assist_bot.bootstrap.text_ingestion import (
    TextIngestionApplication,
    TextIngestionDependencies,
)
from telegram_assist_bot.domain.posts import PostId
from tests.unit.application.test_history_recovery import Checkpoints
from tests.unit.test_text_ingestion_bootstrap import (
    Clock,
    Gateway,
    setup,
    source_message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest


def test_startup_finishes_page_limited_history_without_process_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass
    class CursorGateway(Gateway):
        offsets: list[int] = field(default_factory=list)

        async def iter_history_pages(
            self,
            query: TelegramHistoryQuery,
        ) -> AsyncIterator[TelegramHistoryPage]:
            self.offsets.append(query.offset_message_id)
            selected = tuple(
                item
                for item in self.history
                if not query.offset_message_id
                or item.source_message_id < query.offset_message_id
            )
            if selected:
                batch = selected[: query.page_size]
                yield TelegramHistoryPage(batch, batch[-1].source_message_id, True)

    async def scenario() -> None:
        foundation, _, order = setup()
        settings = foundation.loaded.settings
        settings = settings.model_copy(
            update={
                "telegram": settings.telegram.model_copy(
                    update={
                        "ingestion": settings.telegram.ingestion.model_copy(
                            update={
                                "history_page_size": 1,
                                "history_max_pages": 1,
                            }
                        ),
                    }
                ),
            }
        )
        foundation.loaded = replace(foundation.loaded, settings=settings)
        monkeypatch.setattr(
            foundation,
            "mongodb_client",
            SimpleNamespace(options=SimpleNamespace(event_listeners=[])),
            raising=False,
        )
        gateway = CursorGateway(
            tuple(source_message(index) for index in (3, 2, 1)), [], order
        )
        checkpoints = Checkpoints()
        application = TextIngestionApplication(
            TextIngestionDependencies(
                foundation=foundation,
                gateway_factory=lambda _: gateway,
                clock=Clock(),
                post_id_factory=lambda identity: PostId(
                    f"post-{identity.source_message_id}"
                ),
                sleeper=lambda _: asyncio.sleep(0),
                jitter_source=lambda: 0.5,
                checkpoint_factory=lambda _loaded, _foundation: checkpoints,
            )
        )
        try:
            await asyncio.wait_for(
                application.start(Path("synthetic.json"), environ={}), timeout=5
            )
            assert {
                identity.source_message_id for identity in foundation.repository.posts
            } == {1, 2, 3}
            assert len(foundation.repository.claims) == 3
            assert gateway.offsets == [0, 3, 2, 1]
            assert application.crawl_results[-1000000000101].created == 3
            assert application.crawl_results[-1000000000101].completed
        finally:
            await application.shutdown()

    asyncio.run(scenario())
