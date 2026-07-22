"""Unit tests for advertisement configuration models, domain mapping, and
validation rules."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from telegram_assist_bot.application.config.advertisements import (
    map_advertisement_campaign,
    map_advertisement_campaigns,
)
from telegram_assist_bot.domain.advertisements import (
    AdvertisementCampaign,
    AdvertisementErrorPolicy,
    AdvertisementPublicationMode,
    SourceAdvertisementPost,
    Weekday,
)
from telegram_assist_bot.shared.config.models import (
    AdvertisementCampaignConfig,
    AdvertisementConfig,
)


def _make_valid_campaign_dict() -> dict[str, object]:
    return {
        "campaign_id": "daily-store-ad",
        "name": "تبلیغ روزانه فروشگاه ✨",
        "enabled": True,
        "source_post_url": "https://t.me/advertisement_example/123",
        "source_channel_username": "advertisement_example",
        "destination_names": ["destination-fa"],
        "weekdays": ["saturday", "monday", "wednesday"],
        "times": ["10:00", "16:30", "22:00"],
        "start_date": "2026-08-01",
        "end_date": "2026-12-31",
        "timezone": "Asia/Tehran",
        "publication_mode": "copy",
        "priority": 100,
        "minimum_gap_seconds": 300,
        "error_policy": "retry_then_fail",
        "max_retries": 3,
    }


def test_valid_campaign_mapping_and_immutability() -> None:
    """Test mapping a valid configuration campaign dict to a domain entity."""
    c_dict = _make_valid_campaign_dict()
    c_config = AdvertisementCampaignConfig.model_validate(c_dict)
    adv_config = AdvertisementConfig(campaigns=(c_config,))

    campaigns = map_advertisement_campaigns(adv_config)
    assert len(campaigns) == 1

    campaign = campaigns[0]
    assert isinstance(campaign, AdvertisementCampaign)
    assert campaign.campaign_id == "daily-store-ad"
    assert campaign.name == "تبلیغ روزانه فروشگاه ✨"
    assert campaign.enabled is True
    assert isinstance(campaign.source_post, SourceAdvertisementPost)
    assert campaign.source_post.url == "https://t.me/advertisement_example/123"
    assert campaign.source_post.channel_username == "advertisement_example"
    assert campaign.source_post.message_id == 123
    assert campaign.destination_names == ("destination-fa",)
    assert campaign.weekdays == (Weekday.SATURDAY, Weekday.MONDAY, Weekday.WEDNESDAY)
    assert campaign.times == (time(10, 0), time(16, 30), time(22, 0))
    assert campaign.start_date == date(2026, 8, 1)
    assert campaign.end_date == date(2026, 12, 31)
    assert campaign.timezone == ZoneInfo("Asia/Tehran")
    assert campaign.publication_mode is AdvertisementPublicationMode.COPY
    assert campaign.priority == 100
    assert campaign.minimum_gap_seconds == 300
    assert campaign.error_policy is AdvertisementErrorPolicy.RETRY_THEN_FAIL
    assert campaign.max_retries == 3

    with pytest.raises(AttributeError):
        campaign.enabled = False  # type: ignore[misc]


def test_persian_character_and_zwnj_preservation() -> None:
    """Verify Persian text, ZWNJ, and Emoji preservation without alteration."""
    c_dict = _make_valid_campaign_dict()
    persian_name = "تبلیغ ویژه فروشگاه‌ آنلاین ✨"
    c_dict["name"] = persian_name

    c_config = AdvertisementCampaignConfig.model_validate(c_dict)
    campaign = map_advertisement_campaign(c_config)

    assert campaign.name == persian_name
    assert "‌" in campaign.name  # ZWNJ preserved
    assert "✨" in campaign.name  # Emoji preserved


@pytest.mark.parametrize(
    "url",
    [
        "http://t.me/advertisement_example/123",  # Non-HTTPS
        "https://example.com/123",  # Non t.me
        "t.me/advertisement_example/123",  # Missing scheme
        "https://t.me/advertisement_example/123?ref=abc",  # Query string
        "https://t.me/advertisement_example/123#sec",  # Fragment
        "https://user:pass@t.me/advertisement_example/123",  # Credentials
        "https://t.me/c/123456/789",  # Private channel form
        "https://t.me/advertisement_example/0",  # Zero message ID
        "https://t.me/advertisement_example/-5",  # Negative message ID
    ],
)
def test_invalid_source_post_url_rejected(url: str) -> None:
    """Test strict rejection of non-canonical or unsafe public post URLs."""
    c_dict = _make_valid_campaign_dict()
    c_dict["source_post_url"] = url

    with pytest.raises(ValidationError, match="source_post_url"):
        AdvertisementCampaignConfig.model_validate(c_dict)


def test_source_username_mismatch_rejected() -> None:
    """Test that source_channel_username must match the URL username segment."""
    with pytest.raises(ValueError, match="source_channel_username does not match"):
        SourceAdvertisementPost(
            url="https://t.me/advertisement_example/123",
            channel_username="different_channel",
            message_id=123,
        )


def test_invalid_timezone_rejected() -> None:
    """Test rejection of non-IANA or invalid timezone strings."""
    c_dict = _make_valid_campaign_dict()
    c_dict["timezone"] = "Invalid/Timezone_Name"

    with pytest.raises(ValidationError, match="timezone"):
        AdvertisementCampaignConfig.model_validate(c_dict)


@pytest.mark.parametrize(
    "weekday_list",
    [
        ["شنبه"],  # Localized
        ["Monday"],  # Capitalized
        ["1"],  # Numeric string
        [1],  # Integer
        ["mon"],  # Alias
        [],  # Empty
    ],
)
def test_invalid_weekdays_rejected(weekday_list: list[object]) -> None:
    """Test strict rejection of non-canonical weekday strings."""
    c_dict = _make_valid_campaign_dict()
    c_dict["weekdays"] = weekday_list

    with pytest.raises(ValidationError):
        AdvertisementCampaignConfig.model_validate(c_dict)


@pytest.mark.parametrize(
    "time_list",
    [
        ["1:30"],  # Single-digit hour
        ["24:00"],  # Invalid hour 24
        ["12:60"],  # Invalid minute 60
        ["12:00:00"],  # Includes seconds
        [" 10:00 "],  # Whitespace padding
        [1000],  # Integer
        [],  # Empty
    ],
)
def test_invalid_times_rejected(time_list: list[object]) -> None:
    """Test strict rejection of non-24h HH:MM time strings."""
    c_dict = _make_valid_campaign_dict()
    c_dict["times"] = time_list

    with pytest.raises(ValidationError):
        AdvertisementCampaignConfig.model_validate(c_dict)


def test_duplicate_weekdays_and_times_rejected() -> None:
    """Test rejection of duplicate weekdays or duplicate times in one campaign."""
    c_dict1 = _make_valid_campaign_dict()
    c_dict1["weekdays"] = ["saturday", "monday", "saturday"]
    with pytest.raises(ValidationError, match="weekdays must be unique"):
        AdvertisementCampaignConfig.model_validate(c_dict1)

    c_dict2 = _make_valid_campaign_dict()
    c_dict2["times"] = ["10:00", "16:30", "10:00"]
    with pytest.raises(ValidationError, match="times must be unique"):
        AdvertisementCampaignConfig.model_validate(c_dict2)


def test_date_range_equal_boundary_accepted() -> None:
    """Test that start_date equal to end_date (one-day campaign) is valid."""
    c_dict = _make_valid_campaign_dict()
    c_dict["start_date"] = "2026-08-01"
    c_dict["end_date"] = "2026-08-01"

    c_config = AdvertisementCampaignConfig.model_validate(c_dict)
    campaign = map_advertisement_campaign(c_config)
    assert campaign.start_date == date(2026, 8, 1)
    assert campaign.end_date == date(2026, 8, 1)


def test_reversed_date_range_rejected() -> None:
    """Test rejection of reversed date range (start_date > end_date)."""
    c_dict = _make_valid_campaign_dict()
    c_dict["start_date"] = "2026-12-31"
    c_dict["end_date"] = "2026-08-01"

    c_config = AdvertisementCampaignConfig.model_validate(c_dict)
    with pytest.raises(
        ValueError, match="start_date must be before or equal to end_date"
    ):
        map_advertisement_campaign(c_config)


def test_priority_zero_accepted_and_negative_rejected() -> None:
    """Test priority boundaries (0 allowed, negative rejected)."""
    c_dict0 = _make_valid_campaign_dict()
    c_dict0["priority"] = 0
    c_config0 = AdvertisementCampaignConfig.model_validate(c_dict0)
    campaign0 = map_advertisement_campaign(c_config0)
    assert campaign0.priority == 0

    c_dict_neg = _make_valid_campaign_dict()
    c_dict_neg["priority"] = -1
    with pytest.raises(ValidationError, match="priority"):
        AdvertisementCampaignConfig.model_validate(c_dict_neg)


def test_minimum_gap_positive_accepted_and_zero_rejected() -> None:
    """Test minimum_gap_seconds boundaries (>0 allowed, 0 or negative rejected)."""
    c_dict_zero = _make_valid_campaign_dict()
    c_dict_zero["minimum_gap_seconds"] = 0
    with pytest.raises(ValidationError, match="minimum_gap_seconds"):
        AdvertisementCampaignConfig.model_validate(c_dict_zero)


def test_max_retries_bounds() -> None:
    """Test max_retries boundaries (0 and 10 allowed, -1 and 11 rejected)."""
    for val in (0, 10):
        c_dict = _make_valid_campaign_dict()
        c_dict["max_retries"] = val
        c_config = AdvertisementCampaignConfig.model_validate(c_dict)
        assert map_advertisement_campaign(c_config).max_retries == val

    for val in (-1, 11):
        c_dict = _make_valid_campaign_dict()
        c_dict["max_retries"] = val
        with pytest.raises(ValidationError, match="max_retries"):
            AdvertisementCampaignConfig.model_validate(c_dict)


def test_boolean_rejected_as_integer() -> None:
    """Test that booleans True/False are strictly rejected for int fields."""
    for field in ("priority", "minimum_gap_seconds", "max_retries"):
        c_dict = _make_valid_campaign_dict()
        c_dict[field] = True
        with pytest.raises(ValidationError):
            AdvertisementCampaignConfig.model_validate(c_dict)


def test_unsupported_publication_mode_and_error_policy_rejected() -> None:
    """Test rejection of non-copy mode or non-retry_then_fail policy."""
    c_dict_mode = _make_valid_campaign_dict()
    c_dict_mode["publication_mode"] = "forward"
    with pytest.raises(ValidationError, match="publication_mode"):
        AdvertisementCampaignConfig.model_validate(c_dict_mode)

    c_dict_pol = _make_valid_campaign_dict()
    c_dict_pol["error_policy"] = "stop"
    with pytest.raises(ValidationError, match="error_policy"):
        AdvertisementCampaignConfig.model_validate(c_dict_pol)


def test_deterministic_campaign_mapping() -> None:
    """Verify that multiple mappings of the same config produce equal domain objects."""
    c_dict = _make_valid_campaign_dict()
    c_config = AdvertisementCampaignConfig.model_validate(c_dict)
    adv_config = AdvertisementConfig(campaigns=(c_config,))

    res1 = map_advertisement_campaigns(adv_config)
    res2 = map_advertisement_campaigns(adv_config)

    assert res1 == res2
