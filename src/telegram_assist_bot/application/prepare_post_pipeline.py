"""Restart-safe orchestration of implemented content-preparation stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram_assist_bot.application.categorize_post import (
    KeywordCategoryRule,
    categorize_post,
)
from telegram_assist_bot.application.detect_exact_duplicate import DetectExactDuplicate
from telegram_assist_bot.application.ports import (
    ContentPreparationRepository,
    DestinationArtifact,
)
from telegram_assist_bot.application.prepare_destination_content import (
    prepare_destination_content,
)

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.categories import CategorizationResult, Category
    from telegram_assist_bot.domain.duplicates import DuplicateCheckResult
    from telegram_assist_bot.domain.posts import PostId, TelegramEntity


def validate_unimplemented_ai_flags(
    *,
    advertisement_enabled: bool,
    semantic_duplicate_enabled: bool,
    ai_categorization_enabled: bool,
) -> None:
    """Fail fast instead of silently fabricating results for future AI stages."""
    if advertisement_enabled or semantic_duplicate_enabled or ai_categorization_enabled:
        raise ValueError("An enabled AI content stage is not implemented yet.")


@dataclass(frozen=True, slots=True)
class DestinationSpec:
    """Describe one independent destination-content artifact."""

    destination_id: str
    username: str


@dataclass(frozen=True, slots=True)
class PreparationInput:
    """Provide immutable inputs to the implemented preparation stages."""

    post_id: PostId
    text: str
    caption: str | None
    entities: tuple[TelegramEntity, ...]
    source_username: str
    media_hashes: tuple[str, ...]
    categories: tuple[Category, ...]
    category_rules: tuple[KeywordCategoryRule, ...]
    source_default_category_id: str
    destinations: tuple[DestinationSpec, ...]
    now: datetime
    manual_category: CategorizationResult | None = None


@dataclass(frozen=True, slots=True)
class PreparationResult:
    """Return canonical results from the completed milestone stages."""

    duplicate: DuplicateCheckResult
    category: CategorizationResult
    artifacts: tuple[DestinationArtifact, ...]
    advertisement_ai_state: str = "NotRequested"
    semantic_duplicate_ai_state: str = "NotRequested"
    category_ai_state: str = "NotRequested"
    ready: bool = True


class PreparePostPipeline:
    """Resume idempotent stages using repository-owned canonical assignments."""

    def __init__(self, repository: ContentPreparationRepository) -> None:
        """Initialize the shared durable preparation repository."""
        self._repository = repository
        self._duplicates = DetectExactDuplicate(repository)

    async def execute(self, request: PreparationInput) -> PreparationResult:
        """Prepare exact duplicate, baseline category and destination artifacts."""
        duplicate = await self._repository.get_duplicate_result(request.post_id)
        if duplicate is None:
            duplicate = await self._duplicates.execute(
                post_id=request.post_id,
                text=request.text,
                caption=request.caption,
                media_hashes=request.media_hashes,
                checked_at=request.now,
            )
        category = await self._repository.get_category_result(request.post_id)
        if category is None or request.manual_category is not None:
            category = categorize_post(
                text=request.text,
                categories=request.categories,
                rules=request.category_rules,
                source_default_category_id=request.source_default_category_id,
                assigned_at=request.now,
                manual_override=request.manual_category,
            )
            category = await self._repository.save_category_result(
                request.post_id, category
            )
        artifacts: list[DestinationArtifact] = []
        for destination in request.destinations:
            existing = await self._repository.get_destination_artifact(
                request.post_id, destination.destination_id
            )
            if existing is not None:
                artifacts.append(existing)
                continue
            prepared = prepare_destination_content(
                text=request.text,
                entities=request.entities,
                source_username=request.source_username,
                destination_username=destination.username,
            )
            artifact = DestinationArtifact(
                request.post_id,
                destination.destination_id,
                prepared.text,
                prepared.entities,
                prepared.content_policy_version,
            )
            artifacts.append(await self._repository.save_destination_artifact(artifact))
        await self._repository.mark_preparation_ready(request.post_id, at=request.now)
        return PreparationResult(duplicate, category, tuple(artifacts))
