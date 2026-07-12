"""Verify deterministic non-AI baseline categorization precedence."""

from datetime import UTC, datetime

import pytest

from telegram_assist_bot.application.categorize_post import (
    KeywordCategoryRule,
    categorize_post,
)
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
    Category,
)


def test_manual_keyword_default_and_tie_break() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    categories = (Category("default", "سایر"), Category("tech", "فناوری"))
    rules = (
        KeywordCategoryRule("z", "tech", "Python", 10),
        KeywordCategoryRule("a", "default", "python", 10),
    )
    keyword = categorize_post(
        text="PYTHON آموزش",
        categories=categories,
        rules=rules,
        source_default_category_id="default",
        assigned_at=now,
    )
    assert keyword.rule_id == "a"
    default = categorize_post(
        text="پایتون‌کار",
        categories=categories,
        rules=rules,
        source_default_category_id="default",
        assigned_at=now,
    )
    assert default.method is CategorizationMethod.SOURCE_DEFAULT
    manual = CategorizationResult("tech", CategorizationMethod.MANUAL, 1, now)
    assert (
        categorize_post(
            text="",
            categories=categories,
            rules=rules,
            source_default_category_id="default",
            assigned_at=now,
            manual_override=manual,
        )
        is manual
    )


def test_invalid_rules_and_manual_reference_are_rejected() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    categories = (Category("default", "سایر"),)
    with pytest.raises(ValueError, match="invalid"):
        KeywordCategoryRule("", "default", "", 1)
    with pytest.raises(ValueError, match="unknown category"):
        categorize_post(
            text="خبر",
            categories=categories,
            rules=(KeywordCategoryRule("r", "missing", "خبر", 1),),
            source_default_category_id="default",
            assigned_at=now,
        )
    manual = CategorizationResult("missing", CategorizationMethod.MANUAL, 1, now)
    with pytest.raises(ValueError, match="Manual"):
        categorize_post(
            text="",
            categories=categories,
            rules=(),
            source_default_category_id="default",
            assigned_at=now,
            manual_override=manual,
        )
