"""Deterministic non-AI baseline categorization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
    Category,
)

if TYPE_CHECKING:
    from datetime import datetime

CATEGORY_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True)
class KeywordCategoryRule:
    """Define one bounded deterministic keyword rule."""

    rule_id: str
    category_id: str
    keyword: str
    priority: int

    def __post_init__(self) -> None:
        """Validate bounded stable identifiers and keyword text."""
        if (
            not self.rule_id
            or not self.category_id
            or not self.keyword.strip()
            or len(self.keyword) > 128
        ):
            raise ValueError("Keyword category rule is invalid.")


def categorize_post(
    *,
    text: str,
    categories: tuple[Category, ...],
    rules: tuple[KeywordCategoryRule, ...],
    source_default_category_id: str,
    assigned_at: datetime,
    manual_override: CategorizationResult | None = None,
) -> CategorizationResult:
    """Apply manual, keyword, then source-default precedence."""
    valid_ids = {category.category_id for category in categories}
    if source_default_category_id not in valid_ids:
        raise ValueError("Source default category is unknown.")
    if manual_override is not None:
        if (
            manual_override.method is not CategorizationMethod.MANUAL
            or manual_override.category_id not in valid_ids
        ):
            raise ValueError("Manual category override is invalid.")
        return manual_override
    matches: list[KeywordCategoryRule] = []
    for rule in rules:
        if rule.category_id not in valid_ids:
            raise ValueError("Keyword rule references an unknown category.")
        pattern = re.compile(
            rf"(?<![\w‌]){re.escape(rule.keyword)}(?![\w‌])", re.IGNORECASE
        )
        if pattern.search(text):
            matches.append(rule)
    if matches:
        winner = min(matches, key=lambda rule: (-rule.priority, rule.rule_id))
        return CategorizationResult(
            winner.category_id,
            CategorizationMethod.KEYWORD,
            CATEGORY_POLICY_VERSION,
            assigned_at,
            rule_id=winner.rule_id,
            reason="keyword_rule",
        )
    return CategorizationResult(
        source_default_category_id,
        CategorizationMethod.SOURCE_DEFAULT,
        CATEGORY_POLICY_VERSION,
        assigned_at,
        reason="source_default",
    )
