"""Direct typed-model tests for cross-field configuration invariants."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from telegram_assist_bot.shared.config.models import (
    AdvertisementCampaignConfig,
    AdvertisementSourceFetchConfig,
    AiConfig,
    ApplicationConfig,
    PublishingConfig,
)

type JsonObject = dict[str, object]


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_timeout_seconds": 31, "publication_lease_seconds": 30},
        {"retry_initial_delay_seconds": 31, "retry_maximum_delay_seconds": 30},
        {
            "native_schedule_timeout_seconds": 300,
            "native_schedule_lease_seconds": 300,
        },
    ],
)
def test_publishing_cross_field_bounds_are_enforced(
    changes: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "scheduled_publication_interval_seconds": 60,
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        PublishingConfig.model_validate(values)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("timeout_seconds", True),
        ("timeout_seconds", 0),
        ("max_attempts", 11),
        ("initial_backoff_seconds", 301),
    ],
)
def test_source_fetch_config_rejects_bool_and_out_of_range_values(
    field_name: str, value: object
) -> None:
    values: dict[str, object] = {
        "timeout_seconds": 10,
        "max_attempts": 2,
        "initial_backoff_seconds": 1,
    }
    values[field_name] = value
    with pytest.raises(ValidationError):
        AdvertisementSourceFetchConfig.model_validate(values)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start_date", "2026/01/01"),
        ("source_cache_policy", "unknown"),
        ("source_unavailable_policy", "unknown"),
        ("snapshot_retention_days", 0),
        ("refresh_interval_seconds", 59),
    ],
)
def test_campaign_config_rejects_invalid_optional_policy_values(
    field_name: str,
    value: object,
    valid_payload: JsonObject,
) -> None:
    advertisements = deepcopy(valid_payload["advertisements"])
    assert isinstance(advertisements, dict)
    campaigns = advertisements["campaigns"]
    assert isinstance(campaigns, list)
    campaign = campaigns[0]
    assert isinstance(campaign, dict)
    campaign[field_name] = value
    with pytest.raises(ValidationError):
        AdvertisementCampaignConfig.model_validate(campaign)


def test_ai_config_rejects_duplicate_task_policies(
    valid_payload: JsonObject,
) -> None:
    entries = {
        "cache_policies": {
            "task": "advertisement_detection",
            "enabled": True,
            "ttl_seconds": 60,
        },
        "failure_policies": {
            "task": "advertisement_detection",
            "action": "manual_review",
        },
    }
    for field_name, entry in entries.items():
        raw_ai = deepcopy(valid_payload["ai"])
        assert isinstance(raw_ai, dict)
        raw_ai[field_name] = [deepcopy(entry), deepcopy(entry)]
        with pytest.raises(ValidationError):
            AiConfig.model_validate(raw_ai)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_category",
        "duplicate_rule",
        "unknown_source_default",
        "missing_source_duplicate_flag",
        "missing_semantic_policy",
        "duplicate_admin_permission",
        "unknown_admin_destination",
    ],
)
def test_application_model_rejects_ambiguous_cross_references(
    mutation: str,
    valid_payload: JsonObject,
) -> None:
    raw = deepcopy(valid_payload)
    categories = raw["categorization"]
    sources = raw["source_channels"]
    features = raw["features"]
    admins = raw["admins"]
    assert isinstance(categories, dict)
    assert isinstance(sources, list)
    assert isinstance(features, dict)
    assert isinstance(admins, list)
    category_items = categories["categories"]
    rule_items = categories["keyword_rules"]
    assert isinstance(category_items, list)
    assert isinstance(rule_items, list)
    source = sources[0]
    admin = admins[0]
    assert isinstance(source, dict)
    assert isinstance(admin, dict)

    if mutation == "duplicate_category":
        category_items.append(deepcopy(category_items[0]))
    elif mutation == "duplicate_rule":
        rule_items.append(deepcopy(rule_items[0]))
    elif mutation == "unknown_source_default":
        source["default_category_id"] = "missing"
    elif mutation == "missing_source_duplicate_flag":
        features["duplicate_detection_enabled"] = True
        source["duplicate_detection_enabled"] = None
    elif mutation == "missing_semantic_policy":
        features["duplicate_detection_enabled"] = True
        source["duplicate_detection_enabled"] = True
        raw["semantic_duplicate"] = None
    elif mutation == "duplicate_admin_permission":
        permissions = admin["permissions"]
        assert isinstance(permissions, list)
        permissions.append(permissions[0])
    else:
        admin["allowed_destination_ids"] = [-999999]

    with pytest.raises(ValidationError):
        ApplicationConfig.model_validate(raw)
