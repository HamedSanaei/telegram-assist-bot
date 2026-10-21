"""Measure duplicate-history ingestion against a credential-free local MongoDB.

Run the same script with --source-root pointing at an extracted baseline checkout
and at the fixed checkout. No Telegram calls occur. All documents and media are
synthetic; only a uniquely named benchmark database is removed afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from pymongo import AsyncMongoClient
from pymongo.monitoring import (
    CommandFailedEvent,
    CommandListener,
    CommandStartedEvent,
    CommandSucceededEvent,
)
from pymongo.server_api import ServerApi

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class Commands(CommandListener):
    """Count attempted database commands, never their private payloads."""

    def __init__(self) -> None:
        """Initialize the aggregate."""
        self.count = 0

    def started(self, event: CommandStartedEvent) -> None:
        """Count a command attempt."""
        self.count += 1

    def succeeded(self, event: CommandSucceededEvent) -> None:
        """Retain no responses."""

    def failed(self, event: CommandFailedEvent) -> None:
        """Retain no error payloads."""


@dataclass
class Clock:
    """Keep source dates stable across repeated crawls."""

    now: datetime

    def utc_now(self) -> datetime:
        """Return the frozen crawl instant."""
        return self.now


@dataclass
class Logger:
    """Count events instead of retaining thousands of message log records."""

    events: int = 0
    retries: int = 0

    def emit(self, **event: object) -> None:
        """Count emitted records and retry notifications."""
        self.events += 1
        if event.get("event_name") in {"retry_scheduled", "history_retry_scheduled"}:
            self.retries += 1


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    # Imports deliberately follow --source-root selection so one workload executes
    # the actual baseline or fixed implementation, not a reimplementation.
    from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
    from telegram_assist_bot.application.crawl_today_text_posts import (
        CrawlTodayTextPosts,
    )
    from telegram_assist_bot.application.download_post_media import DownloadPostMedia
    from telegram_assist_bot.application.ingest_post_idempotently import (
        IngestPostIdempotently,
    )
    from telegram_assist_bot.application.ports import (
        MediaTooLargeError,
        TelegramHistoryPage,
        TelegramHistoryQuery,
        TelegramMediaReference,
        TelegramTextMessage,
    )
    from telegram_assist_bot.application.prepare_post_pipeline import (
        DestinationSpec,
        PreparePostPipeline,
    )
    from telegram_assist_bot.application.runtime_ingestion import (
        RuntimeMessageIngestor,
        RuntimePreparationPolicy,
        RuntimeSourcePolicy,
    )
    from telegram_assist_bot.domain.categories import Category
    from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
    from telegram_assist_bot.domain.posts import PostId
    from telegram_assist_bot.infrastructure.media import LocalMediaStorage
    from telegram_assist_bot.infrastructure.persistence.mongodb import (
        MongoPostRepository,
        content_repository,
        initialize_post_indexes,
    )
    from telegram_assist_bot.shared.retry import RetryPolicy

    source_id, incident_id = -1001648659094, 68568
    maximum_bytes = 100 * 1024 * 1024
    clock = Clock(datetime(2026, 10, 21, 12, tzinfo=UTC))
    logger, commands = Logger(), Commands()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        arguments.mongodb_uri,
        event_listeners=[commands],
        timeoutMS=5000,
        retryReads=False,
        retryWrites=False,
        tz_aware=True,
        server_api=ServerApi("1", strict=True),
    )
    database_name = f"tab_t102_benchmark_{uuid4().hex}"
    database = client[database_name]

    class Source:
        opens = 0
        oversized_opens = 0

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            self.opens += 1
            oversized = opaque_reference == "synthetic-oversized"
            if oversized:
                self.oversized_opens += 1

            async def chunks() -> AsyncIterator[bytes]:
                yield b"synthetic-media"
                if oversized:
                    raise MediaTooLargeError("Synthetic oversized stream.")

            return chunks()

    class Storage(LocalMediaStorage):
        checks = 0
        previews = 0
        stores = 0

        async def exists(self, storage_path: str) -> bool:
            self.checks += 1
            return await super().exists(storage_path)

        async def ensure_preview(self, media: StoredMedia) -> bool:
            self.previews += 1
            return await super().ensure_preview(media)

        async def store(
            self,
            identity: MediaIdentity,
            stream: AsyncIterator[bytes],
            *,
            maximum_bytes: int,
        ) -> tuple[str, int, str]:
            self.stores += 1
            return await super().store(identity, stream, maximum_bytes=maximum_bytes)

    def message(identifier: int) -> TelegramTextMessage:
        incident = identifier == incident_id
        return TelegramTextMessage(
            source_id,
            "synthetic_source",
            "Synthetic source",
            identifier,
            None,
            f"Synthetic caption {identifier}",
            (),
            (),
            clock.now
            - timedelta(seconds=incident_id + arguments.messages - identifier + 1),
            False,
            True,
            (
                TelegramMediaReference(
                    MediaType.DOCUMENT,
                    0,
                    maximum_bytes + 1 if incident else 15,
                    "application/octet-stream",
                    "synthetic.bin",
                    "synthetic-oversized" if incident else "synthetic-supported",
                ),
            ),
        )

    messages = (
        *(
            message(incident_id + arguments.messages - index)
            for index in range(arguments.messages)
        ),
        message(incident_id),
    )

    class Gateway:
        requests = 0

        async def iter_history_pages(
            self, query: TelegramHistoryQuery
        ) -> AsyncIterator[TelegramHistoryPage]:
            # Newest-first IDs, matching Telegram history. The incident follows
            # the completed records to reproduce the expensive baseline prefix.
            eligible = tuple(
                item
                for item in messages
                if not getattr(query, "offset_message_id", 0)
                or item.source_message_id < getattr(query, "offset_message_id", 0)
            )
            for offset in range(0, len(eligible), query.page_size):
                self.requests += 1
                batch = eligible[offset : offset + query.page_size]
                page_options: dict[str, Any] = {}
                if hasattr(TelegramHistoryPage, "newest_first"):
                    page_options = {
                        "next_offset_message_id": min(
                            item.source_message_id for item in batch
                        ),
                        "newest_first": True,
                    }
                yield TelegramHistoryPage(batch, **page_options)

    gateway, source = Gateway(), Source()
    try:
        await initialize_post_indexes(database["posts"], timeout_seconds=5)
        await content_repository.initialize_content_preparation_indexes(
            database["media_items"],
            database["media_groups"],
            database["content_preparations"],
        )
        with tempfile.TemporaryDirectory(prefix="tab-t102-benchmark-") as directory:
            storage = Storage(Path(directory))
            posts = MongoPostRepository(database["posts"], 5)
            content = content_repository.MongoContentPreparationRepository(
                database["media_items"],
                database["media_groups"],
                database["content_preparations"],
            )
            runtime = RuntimeMessageIngestor(
                post_ingestor=IngestPostIdempotently(
                    posts,
                    clock,
                    lambda identity: PostId(str(identity.source_message_id)),
                    logger,
                ),
                post_repository=posts,
                content_repository=content,
                storage=storage,
                downloader=DownloadPostMedia(
                    source,
                    storage,
                    content,
                    maximum_bytes=maximum_bytes,
                    timeout_seconds=5,
                ),
                assembler=AssembleMediaGroup(
                    content,
                    quiet_window=timedelta(seconds=2),
                    maximum_wait=timedelta(seconds=10),
                ),
                pipeline=PreparePostPipeline(content),
                policy=RuntimePreparationPolicy(
                    categories=(Category("general", "General"),),
                    category_rules=(),
                    sources=(
                        RuntimeSourcePolicy(
                            source_id,
                            "synthetic_source",
                            "general",
                            (DestinationSpec("destination", "synthetic_destination"),),
                        ),
                    ),
                ),
                clock=clock,
                logger=logger,
                correlation_id="synthetic-benchmark",
            )
            checkpoint_options: dict[str, Any] = {}
            checkpoint_module = (
                "telegram_assist_bot.infrastructure.persistence.mongodb."
                "history_checkpoint"
            )
            if importlib.util.find_spec(checkpoint_module) is not None:
                checkpoint_adapter = importlib.import_module(checkpoint_module)
                checkpoint_options["checkpoints"] = (
                    checkpoint_adapter.MongoHistoryCheckpointRepository(
                        database["history_checkpoints"], database["history_failures"]
                    )
                )
            for item in messages[:-1]:
                await runtime.execute(item, correlation_id="synthetic-seed")
            commands.count = 0
            logger.events = logger.retries = 0
            source.opens = source.oversized_opens = 0
            storage.checks = storage.previews = storage.stores = 0
            wall_start, cpu_start = time.perf_counter(), time.process_time()
            failures: list[str] = []
            cycles: list[dict[str, object]] = []
            for _ in range(arguments.cycles):
                cycle_wall, cycle_cpu = time.perf_counter(), time.process_time()
                cycle_commands = commands.count
                cycle_checks, cycle_previews = storage.checks, storage.previews
                cycle_requests = gateway.requests
                crawler = CrawlTodayTextPosts(
                    gateway=gateway,
                    ingestor=runtime,
                    clock=clock,
                    timezone=ZoneInfo("UTC"),
                    retry_policy=RetryPolicy(
                        max_attempts=3,
                        initial_delay_seconds=0.1,
                        max_delay_seconds=1,
                    ),
                    logger=logger,
                    sleeper=asyncio.sleep,
                    jitter_source=lambda: 0.5,
                    page_size=100,
                    max_pages=100,
                    **checkpoint_options,
                )
                try:
                    await crawler.execute(source_id, correlation_id="synthetic-crawl")
                except MediaTooLargeError:
                    failures.append("MediaTooLargeError")
                cycles.append(
                    {
                        "wall_seconds": round(time.perf_counter() - cycle_wall, 6),
                        "cpu_seconds": round(time.process_time() - cycle_cpu, 6),
                        "mongodb_commands": commands.count - cycle_commands,
                        "history_requests": gateway.requests - cycle_requests,
                        "storage_checks": storage.checks - cycle_checks,
                        "preview_calls": storage.previews - cycle_previews,
                    }
                )
            wall, cpu = (
                time.perf_counter() - wall_start,
                time.process_time() - cpu_start,
            )
            return {
                "messages_previously_completed": arguments.messages,
                "oversized_messages": 1,
                "crawl_invocations": arguments.cycles,
                "wall_seconds": round(wall, 6),
                "cpu_seconds": round(cpu, 6),
                "cpu_percent_one_core": round(100 * cpu / wall, 2),
                "mongodb_commands": commands.count,
                "media_opens": source.opens,
                "oversized_media_opens": source.oversized_opens,
                "storage_checks": storage.checks,
                "preview_calls": storage.previews,
                "duplicate_media_operations": storage.checks + storage.previews,
                "store_calls": storage.stores,
                "history_requests": gateway.requests,
                "logged_events": logger.events,
                "retry_events": logger.retries,
                "crawl_failures": failures,
                "policy_maximum_bytes": maximum_bytes,
                "cycles": cycles,
                "scope": (
                    "client process only; synthetic provider, real loopback MongoDB"
                ),
            }
    finally:
        # Only this generated database can reach deletion; user data is never used.
        await client.drop_database(database_name)
        await client.close()


def main() -> None:
    """Validate local-only arguments and print measured results as UTF-8 JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--messages", type=int, default=2000)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--mongodb-uri", default=os.environ.get("TEST_MONGODB_URI", ""))
    arguments = parser.parse_args()
    parsed = urlsplit(arguments.mongodb_uri)
    if (
        parsed.scheme != "mongodb"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query != "directConnection=true"
    ):
        parser.error("a credential-free loopback MongoDB test URI is required")
    if not 1000 <= arguments.messages <= 9000 or not 1 <= arguments.cycles <= 10:
        parser.error("messages must be 1000..9000 and cycles 1..10")
    if arguments.source_root:
        sys.path.insert(0, str(arguments.source_root.resolve() / "src"))
    print(json.dumps(asyncio.run(_run(arguments)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
