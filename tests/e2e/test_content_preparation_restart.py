"""Verify preparation restart reuses canonical results without external calls."""

import asyncio
from datetime import UTC, datetime

import pytest
from tests.unit.application.m2_fakes import FakePreparationRepository

from telegram_assist_bot.application.categorize_post import KeywordCategoryRule
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparationInput,
    PreparePostPipeline,
    validate_unimplemented_ai_flags,
)
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.domain.posts import PostId


def test_restart_reuses_all_completed_results_and_ai_flags_fail_fast() -> None:
    repository = FakePreparationRepository()
    request = PreparationInput(
        PostId("p"),
        "متن‌فارسی",
        None,
        (),
        "source_name",
        (),
        (Category("other", "سایر"),),
        (KeywordCategoryRule("r", "other", "فارسی", 1),),
        "other",
        (DestinationSpec("d", "dest_name"),),
        datetime(2026, 1, 1, tzinfo=UTC),
    )

    async def scenario() -> None:
        first = await PreparePostPipeline(repository).execute(request)
        second = await PreparePostPipeline(repository).execute(request)
        assert first == second
        assert len(repository.artifacts) == 1

    asyncio.run(scenario())
    validate_unimplemented_ai_flags(
        advertisement_enabled=False,
        semantic_duplicate_enabled=False,
        ai_categorization_enabled=False,
    )
    with pytest.raises(ValueError, match="not implemented"):
        validate_unimplemented_ai_flags(
            advertisement_enabled=True,
            semantic_duplicate_enabled=False,
            ai_categorization_enabled=False,
        )
