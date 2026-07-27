# mypy: disable-error-code="arg-type,redundant-cast"
"""Focused tests for fail-fast raw cross-reference validation."""

from __future__ import annotations

from typing import cast

import pytest

from telegram_assist_bot.shared.config import loader


def _paths(issues: list[object]) -> set[str]:
    return {
        cast("object", issue).formatted_path  # type: ignore[attr-defined]
        for issue in issues
    }


def _advertisement_issues(document: dict[str, object]) -> list[object]:
    return cast(
        "list[object]",
        loader._raw_advertisement_issues(
            document,
            {"destination"},
            {"destination"},
        ),
    )


@pytest.mark.parametrize(
    ("document", "expected_path"),
    [
        ({"advertisements": []}, "advertisements"),
        (
            {
                "advertisements": {
                    "campaigns": [{"enabled": True}],
                    "source_fetch": None,
                }
            },
            "advertisements.source_fetch",
        ),
        (
            {"advertisements": {"source_fetch": "bad"}},
            "advertisements.source_fetch",
        ),
        (
            {
                "advertisements": {
                    "source_fetch": {
                        "timeout_seconds": 0,
                        "max_attempts": 1,
                        "initial_backoff_seconds": 0,
                    }
                }
            },
            "advertisements.source_fetch.timeout_seconds",
        ),
        (
            {"advertisements": {"routes": "bad"}},
            "advertisements.routes",
        ),
        (
            {"advertisements": {"campaigns": "bad"}},
            "advertisements.campaigns",
        ),
    ],
)
def test_advertisement_root_and_fetch_shapes_are_explicit(
    document: dict[str, object], expected_path: str
) -> None:
    assert expected_path in _paths(_advertisement_issues(document))


def _campaign(**changes: object) -> dict[str, object]:
    campaign: dict[str, object] = {
        "campaign_id": "campaign",
        "name": "Campaign",
        "enabled": True,
        "source_post_url": "https://t.me/source_name/42",
        "source_channel_username": "source_name",
        "destination_names": ["destination"],
        "weekdays": ["monday"],
        "times": ["09:00"],
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "timezone": "UTC",
        "publication_mode": "copy",
        "priority": 1,
        "minimum_gap_seconds": 60,
        "error_policy": "retry_then_fail",
        "max_retries": 2,
        "source_cache_policy": "cached",
        "source_unavailable_policy": "fail_closed",
        "snapshot_retention_days": 7,
        "refresh_interval_seconds": None,
    }
    campaign.update(changes)
    return campaign


@pytest.mark.parametrize(
    ("changes", "expected_suffix"),
    [
        ({"enabled": 1}, ".enabled"),
        ({"source_post_url": ""}, ".source_post_url"),
        ({"source_channel_username": ""}, ".source_channel_username"),
        ({"source_channel_username": "other_name"}, ".source_channel_username"),
        ({"destination_names": []}, ".destination_names"),
        (
            {"destination_names": ["destination", "destination"]},
            ".destination_names[1]",
        ),
        ({"destination_names": [1]}, ".destination_names[0]"),
        ({"weekdays": []}, ".weekdays"),
        ({"weekdays": ["monday", "monday"]}, ".weekdays[1]"),
        ({"times": []}, ".times"),
        ({"times": ["09:00", "09:00"]}, ".times[1]"),
        ({"start_date": 1}, ".start_date"),
        ({"start_date": "not-a-date"}, ".start_date"),
        ({"end_date": 1}, ".end_date"),
        ({"end_date": "not-a-date"}, ".end_date"),
        ({"timezone": ""}, ".timezone"),
        (
            {
                "source_cache_policy": "periodic_refresh",
                "refresh_interval_seconds": 59,
            },
            ".refresh_interval_seconds",
        ),
        ({"refresh_interval_seconds": 60}, ".refresh_interval_seconds"),
    ],
)
def test_campaign_raw_errors_keep_exact_paths(
    changes: dict[str, object], expected_suffix: str
) -> None:
    document = {
        "destination_channels": [{"name": "destination"}],
        "advertisements": {"campaigns": [_campaign(**changes)]},
    }
    paths = _paths(_advertisement_issues(document))
    assert any(path.endswith(expected_suffix) for path in paths)


def test_duplicate_campaign_ids_are_rejected() -> None:
    document = {
        "destination_channels": [{"name": "destination"}],
        "advertisements": {"campaigns": [_campaign(), _campaign()]},
    }
    assert "advertisements.campaigns[1].campaign_id" in _paths(
        _advertisement_issues(document)
    )


@pytest.mark.parametrize(
    ("document", "expected_path"),
    [
        (
            {
                "features": {"ai_categorization_enabled": True},
                "categorization": [],
            },
            "categorization",
        ),
        (
            {
                "features": {"ai_categorization_enabled": True},
                "categorization": {"categories": []},
            },
            "categorization.categories",
        ),
        (
            {
                "categorization": {
                    "categories": [
                        {"category_id": "news", "active": True},
                        {"category_id": "news", "active": True},
                    ]
                }
            },
            "categorization.categories[1].category_id",
        ),
        (
            {
                "features": {"ai_categorization_enabled": True},
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}],
                    "fallback_policy": "stop",
                },
            },
            "categorization.fallback_policy",
        ),
        (
            {
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}],
                    "method_order": [],
                }
            },
            "categorization.method_order",
        ),
        (
            {
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}],
                    "method_order": ["ai", "ai"],
                }
            },
            "categorization.method_order",
        ),
        (
            {
                "features": {"ai_categorization_enabled": True},
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}]
                },
                "source_channels": [{"default_category_id": None}],
            },
            "source_channels[0].default_category_id",
        ),
        (
            {
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}]
                },
                "source_channels": [{"default_category_id": "missing"}],
            },
            "source_channels[0].default_category_id",
        ),
        (
            {
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}],
                    "keyword_rules": [{"category_id": "missing"}],
                }
            },
            "categorization.keyword_rules[0].category_id",
        ),
        (
            {
                "categorization": {
                    "categories": [{"category_id": "news", "active": True}],
                    "aliases": {"alias": "missing"},
                }
            },
            "categorization.aliases.alias",
        ),
    ],
)
def test_categorization_cross_references_keep_exact_paths(
    document: dict[str, object], expected_path: str
) -> None:
    assert expected_path in _paths(loader._raw_categorization_issues(document))
