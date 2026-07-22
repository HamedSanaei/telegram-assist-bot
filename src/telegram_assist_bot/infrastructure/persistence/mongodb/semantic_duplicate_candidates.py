"""MongoDB candidate query with a minimal projection and deterministic order."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from telegram_assist_bot.application.ports.post_repository import (
    PostRepositoryUnavailableError,
)
from telegram_assist_bot.application.ports.semantic_duplicate_candidates import (
    SemanticDuplicateCandidate,
)
from telegram_assist_bot.application.text_normalization import normalize_exact_text
from telegram_assist_bot.domain.posts import PostId, PostStatus

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_PROJECTION = {
    "_id": 1,
    "original_content.text": 1,
    "original_content.caption": 1,
    "received_at": 1,
    "expires_at": 1,
}


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Candidate query timestamps must be aware.")
    return value.astimezone(UTC)


@dataclass(slots=True)
class MongoSemanticDuplicateCandidateRepository:
    """Read only eligible Post projections without exposing Telegram metadata."""

    _collection: AsyncCollection[MongoDocument] = field(repr=False)
    _timeout_seconds: int = 5

    async def list_candidates(
        self,
        *,
        current_post_id: PostId,
        now: datetime,
        window_start: datetime,
        limit: int,
    ) -> tuple[SemanticDuplicateCandidate, ...]:
        """Query the inclusive lower boundary and explicitly reject expiry at now."""
        now_utc = _utc(now)
        start_utc = _utc(window_start)
        if start_utc > now_utc or not 1 <= limit <= 1000:
            raise ValueError("Invalid semantic candidate query.")
        query = {
            "_id": {"$ne": current_post_id.value},
            "status": PostStatus.STORED.value,
            "received_at": {"$gte": start_utc, "$lte": now_utc},
            "expires_at": {"$gt": now_utc},
        }
        candidates: list[SemanticDuplicateCandidate] = []
        try:
            async with asyncio.timeout(self._timeout_seconds):
                cursor = (
                    self._collection.find(query, _PROJECTION)
                    .sort([("received_at", DESCENDING), ("_id", ASCENDING)])
                    .limit(limit)
                )
                async for document in cursor:
                    content = document.get("original_content")
                    if not isinstance(content, dict):
                        continue
                    raw = content.get("text") or content.get("caption")
                    if not isinstance(raw, str):
                        continue
                    normalized = normalize_exact_text(raw)
                    if normalized is None or not normalized or normalized.isspace():
                        continue
                    post_id = document.get("_id")
                    received_at = document.get("received_at")
                    expires_at = document.get("expires_at")
                    if (
                        not isinstance(post_id, str)
                        or not isinstance(received_at, datetime)
                        or not isinstance(expires_at, datetime)
                    ):
                        continue
                    candidates.append(
                        SemanticDuplicateCandidate(
                            post_id=PostId(post_id),
                            comparison_text=normalized,
                            received_at=_utc(received_at),
                            expires_at=_utc(expires_at),
                        )
                    )
        except (PyMongoError, TimeoutError):
            raise PostRepositoryUnavailableError from None
        return tuple(candidates)


__all__ = ("MongoSemanticDuplicateCandidateRepository",)
