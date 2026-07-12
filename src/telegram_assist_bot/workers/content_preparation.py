"""Thin worker trigger for the content-preparation use case."""

from telegram_assist_bot.application.prepare_post_pipeline import (
    PreparationInput,
    PreparationResult,
    PreparePostPipeline,
)


async def prepare_content_once(
    pipeline: PreparePostPipeline, request: PreparationInput
) -> PreparationResult:
    """Run one explicitly supplied preparation request."""
    return await pipeline.execute(request)
