"""Verify safe approval queue inspection and explicit retry composition."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

import telegram_assist_bot.bootstrap.approval_queue as module

if TYPE_CHECKING:
    from collections.abc import Mapping

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, values: object) -> Cursor:
        del values
        return self

    def __aiter__(self) -> Cursor:
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iterator)
        except StopIteration as error:
            raise StopAsyncIteration from error


class Collection:
    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
        *,
        found: dict[str, Any] | None = None,
    ) -> None:
        self.documents = documents or []
        self.found = found
        self.writes = 0

    def find(self, query: dict[str, object]) -> Cursor:
        assert query == {}
        return Cursor(self.documents)

    async def find_one(
        self, query: dict[str, object], *, projection: dict[str, object]
    ) -> dict[str, Any] | None:
        del query, projection
        return self.found


class ReferenceCollection(Collection):
    async def find_one(
        self, query: dict[str, object], *, projection: dict[str, object]
    ) -> dict[str, Any] | None:
        del projection
        if str(query.get("_id", "")).endswith(":8"):
            return {"active": True, "delivery_state": "completed"}
        return None


class Foundation:
    def __init__(self, database: dict[str, object]) -> None:
        admins = (
            SimpleNamespace(telegram_user_id=7),
            SimpleNamespace(telegram_user_id=8),
        )
        bot = SimpleNamespace(approval_retry_max_attempts=3)
        settings = SimpleNamespace(
            mongodb=SimpleNamespace(database_name="test"),
            telegram=SimpleNamespace(bot=bot),
            admins=admins,
        )
        self.configuration = SimpleNamespace(settings=settings)
        self.mongodb_client = {"test": database}
        self.started = 0
        self.stopped = 0

    async def start(self, path: Path, *, environ: object) -> None:
        del path, environ
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1


def test_inspection_is_read_only_and_renders_only_safe_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliveries = Collection(
        [
            {
                "_id": "proposal-identity-long",
                "status": "retry",
                "attempt_count": 2,
                "next_attempt_at": NOW,
                "administrator_deliveries": {
                    "7": {
                        "status": "retry",
                        "attempt_count": 2,
                        "delivery_phase": "content_message_send",
                        "next_attempt_at": NOW,
                        "failure_type": "ApprovalDeliveryTransientError",
                    }
                },
                "text": "محرمانه",
                "media_path": "private.jpg",
            }
        ]
    )
    foundation = Foundation(
        {
            "approval_deliveries": deliveries,
            "posts": Collection(
                found={
                    "source_channel_id": -1001,
                    "source_message_id": 42,
                    "text": "محرمانه",
                }
            ),
            "approval_references": ReferenceCollection(),
            "media_groups": Collection(found={"members": [1, 2]}),
            "media_items": Collection(found={"media_type": "Document"}),
        }
    )
    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )

    rows = asyncio.run(
        module.inspect_approval_queue(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            status="retry",
        )
    )

    assert foundation.started == foundation.stopped == 1
    assert len(rows) == 1
    assert "approval_post_id=proposal-ide" in rows[0]
    assert "source_message_id=42" in rows[0]
    assert "content_kind=album" in rows[0]
    assert "administrator_id=7" in rows[0]
    assert "ApprovalDeliveryTransientError" in rows[0]
    assert "محرمانه" not in rows[0]
    assert "private.jpg" not in rows[0]
    assert deliveries.writes == 0


def test_explicit_retry_is_idempotently_delegated_and_closes_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation(
        {"content_preparations": object(), "approval_deliveries": object()}
    )
    calls: list[tuple[str, datetime]] = []

    class Repository:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def retry_delivery(self, post_id: str, *, now: datetime) -> bool:
            calls.append((post_id, now))
            return False

    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )
    monkeypatch.setattr(module, "MongoOperationalApprovalRepository", Repository)

    result = asyncio.run(
        module.retry_approval_delivery(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            approval_post_id="exact-proposal",
        )
    )

    assert not result
    assert calls[0][0] == "exact-proposal"
    assert calls[0][1].tzinfo is UTC
    assert foundation.started == foundation.stopped == 1


def test_document_recovery_validates_scope_before_opening_resources() -> None:
    common: dict[str, Any] = {
        "configuration_path": Path("configuration.json"),
        "environ": {},
        "sink": cast("Any", object()),
        "approval_post_id": None,
        "started_at": None,
        "ended_at": None,
        "dry_run": True,
        "limit": 1,
    }

    async def invoke(**changes: object) -> None:
        values = {**common, **changes}
        await cast("Any", module.recover_rejected_document_deliveries)(**values)

    with pytest.raises(ValueError, match="limit"):
        asyncio.run(invoke(limit=0))
    with pytest.raises(ValueError, match="exact Post ID"):
        asyncio.run(invoke())
    with pytest.raises(ValueError, match="exact Post ID"):
        asyncio.run(
            invoke(
                approval_post_id="post",
                started_at=NOW,
                ended_at=NOW + timedelta(hours=1),
            )
        )
    with pytest.raises(ValueError, match="aware time range"):
        asyncio.run(
            invoke(
                started_at=NOW.replace(tzinfo=None),
                ended_at=(NOW + timedelta(hours=1)).replace(tzinfo=None),
            )
        )
    with pytest.raises(ValueError, match="aware time range"):
        asyncio.run(
            invoke(
                started_at=NOW + timedelta(hours=1),
                ended_at=NOW,
            )
        )


def test_document_recovery_delegates_and_closes_foundation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = Foundation({"approval_deliveries": object()})
    captured: list[Mapping[str, object]] = []

    async def recover(_database: object, **values: object) -> object:
        captured.append(values)
        return module.ApprovalDocumentRecoveryResult(("post",), ())

    monkeypatch.setattr(
        module, "create_foundation_application", lambda **_kwargs: foundation
    )
    monkeypatch.setattr(module, "_recover_rejected_documents_in_database", recover)
    result = asyncio.run(
        module.recover_rejected_document_deliveries(
            Path("configuration.json"),
            environ={},
            sink=cast("Any", object()),
            approval_post_id="post",
            started_at=None,
            ended_at=None,
            dry_run=True,
            limit=3,
        )
    )
    assert result.matching_post_ids == ("post",)
    assert captured[0]["approval_post_id"] == "post"
    assert captured[0]["dry_run"] is True
    assert foundation.started == foundation.stopped == 1


def test_queue_safe_helper_fallbacks() -> None:
    assert module._administrator_status({"status": "pending"}, None, {}) == "pending"
    assert module._safe_time(None) == "none"

    async def scenario() -> None:
        database = cast(
            "Any",
            {
                "media_groups": Collection(found=None),
                "media_items": Collection(found={"media_type": "Video"}),
            },
        )
        assert await module._content_kind(database, None) == "unknown"
        assert (
            await module._content_kind(
                database,
                {"source_channel_id": -1001, "source_message_id": 42},
            )
            == "video"
        )

    asyncio.run(scenario())
