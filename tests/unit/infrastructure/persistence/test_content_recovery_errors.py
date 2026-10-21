"""Keep known Mongo outages recoverable without hiding programming failures."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from pymongo.errors import ConnectionFailure, ExecutionTimeout

from telegram_assist_bot.application.ports.media import MediaTransientError
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
)


@pytest.mark.parametrize(
    "failure", [ConnectionFailure("offline"), ExecutionTimeout("timeout")]
)
def test_content_readiness_outage_is_transient(failure: Exception) -> None:
    class Collection:
        async def find_one(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise failure

    async def scenario() -> None:
        collection = cast("Any", Collection())
        repository = MongoContentPreparationRepository(
            collection, collection, collection
        )
        with pytest.raises(MediaTransientError, match="temporarily unavailable"):
            await repository.get_preparation_state(PostId("recovery"))

    asyncio.run(scenario())


def test_content_programming_failure_is_not_assumed_retryable() -> None:
    class Collection:
        async def find_one(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise ValueError("malformed operation")

    async def scenario() -> None:
        collection = cast("Any", Collection())
        repository = MongoContentPreparationRepository(
            collection, collection, collection
        )
        with pytest.raises(ValueError, match="malformed"):
            await repository.get_preparation_state(PostId("recovery"))

    asyncio.run(scenario())
