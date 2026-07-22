"""Integration tests for advertisement configuration loading and reference
validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from telegram_assist_bot.application.config.advertisements import (
    map_advertisement_campaigns,
)
from telegram_assist_bot.domain.advertisements import AdvertisementCampaign
from telegram_assist_bot.shared.config.errors import ConfigurationValidationError
from telegram_assist_bot.shared.config.loader import load_configuration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE_PATH = _REPO_ROOT / "config" / "configuration.example.json"


def _synthetic_environ() -> dict[str, str]:
    return {
        "TAB_MONGODB_URI": "mongodb://127.0.0.1:27017/?directConnection=true",
        "TAB_TELEGRAM_API_ID": "123456",
        "TAB_TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
        "TAB_TELEGRAM_PHONE_NUMBER": "+15550199",
        "TAB_TELEGRAM_BOT_TOKEN": "123456789:abcdefghijklmnopqrstuvwxyz",
        "TAB_AI_PROVIDER_KEY": "secret-key",
        "TAB_ZAI_API_KEY": "secret-key-zai",
        "TAB_DEEPSEEK_API_KEY": "secret-key-deepseek",
    }


def _make_minimal_valid_config_doc() -> dict[str, Any]:
    """Return a raw configuration dictionary based on configuration.example.json."""
    return cast("dict[str, Any]", json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8")))


def test_load_real_example_configuration(tmp_path: Path) -> None:
    """Test reading real configuration.example.json file and mapping campaigns."""
    environ = _synthetic_environ()
    loaded = load_configuration(_EXAMPLE_PATH, environ=environ)

    assert loaded.settings.advertisements is not None
    assert len(loaded.settings.advertisements.campaigns) >= 1

    campaigns = map_advertisement_campaigns(loaded.settings.advertisements)
    assert len(campaigns) >= 1
    assert isinstance(campaigns[0], AdvertisementCampaign)
    assert campaigns[0].campaign_id == "daily-store-ad"
    assert "تبلیغ" in campaigns[0].name


def test_accumulated_path_aware_campaign_validation_errors(tmp_path: Path) -> None:
    """Verify that multiple campaign validation issues are collected together
    with exact paths."""
    doc = _make_minimal_valid_config_doc()
    # Inject multiple invalid fields into campaigns
    doc["advertisements"]["campaigns"] = [
        {
            "campaign_id": "INVALID SLUG!",
            "name": "   ",
            "enabled": True,
            "source_post_url": "https://example.com/123",  # Malformed
            "source_channel_username": "advertisement_example",
            "destination_names": ["non_existent_dest"],  # Unknown dest
            "weekdays": ["invalid_day"],
            "times": ["25:00"],  # Invalid time
            "start_date": "2026-12-31",
            "end_date": "2026-08-01",  # Reversed dates
            "timezone": "Invalid/TZ",
            "publication_mode": "forward",  # Unsupported mode
            "priority": -5,
            "minimum_gap_seconds": 0,
            "error_policy": "stop",
            "max_retries": 15,
        }
    ]

    config_file = tmp_path / "invalid_campaigns.json"
    config_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_configuration(config_file, environ=_synthetic_environ())

    issues = exc_info.value.issues
    paths = [issue.formatted_path for issue in issues]

    # Verify exact path formatting
    assert "advertisements.campaigns[0].campaign_id" in paths
    assert "advertisements.campaigns[0].name" in paths
    assert "advertisements.campaigns[0].source_post_url" in paths
    assert "advertisements.campaigns[0].destination_names[0]" in paths
    assert "advertisements.campaigns[0].weekdays[0]" in paths
    assert "advertisements.campaigns[0].times[0]" in paths
    assert "advertisements.campaigns[0].start_date" in paths
    assert "advertisements.campaigns[0].timezone" in paths
    assert "advertisements.campaigns[0].publication_mode" in paths
    assert "advertisements.campaigns[0].priority" in paths
    assert "advertisements.campaigns[0].minimum_gap_seconds" in paths
    assert "advertisements.campaigns[0].error_policy" in paths
    assert "advertisements.campaigns[0].max_retries" in paths


def test_enabled_campaign_referencing_disabled_destination_rejected(
    tmp_path: Path,
) -> None:
    """Test that an enabled campaign referencing a disabled destination is rejected."""
    doc = _make_minimal_valid_config_doc()
    # Disable the destination channel
    doc["destination_channels"][0]["enabled"] = False
    # Enabled campaign references destination-fa
    doc["advertisements"]["campaigns"][0]["enabled"] = True

    config_file = tmp_path / "disabled_dest_ref.json"
    config_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_configuration(config_file, environ=_synthetic_environ())

    issue_codes = [issue.code for issue in exc_info.value.issues]
    assert "disabled_destination" in issue_codes


def test_disabled_campaign_referencing_disabled_destination_accepted(
    tmp_path: Path,
) -> None:
    """Test that a disabled campaign referencing a disabled destination is accepted."""
    doc = _make_minimal_valid_config_doc()
    doc["destination_channels"][0]["enabled"] = False
    doc["advertisements"]["campaigns"][0]["enabled"] = False

    config_file = tmp_path / "disabled_campaign_ref.json"
    config_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    loaded = load_configuration(config_file, environ=_synthetic_environ())
    assert loaded.settings.advertisements.campaigns[0].enabled is False


def test_legacy_configuration_compatibility(tmp_path: Path) -> None:
    """Test backward compatibility with legacy configuration missing
    advertisements or campaigns."""
    doc = _make_minimal_valid_config_doc()

    # 1. Config with no advertisements key
    doc_no_adv = dict(doc)
    del doc_no_adv["advertisements"]
    f1 = tmp_path / "no_adv.json"
    f1.write_text(json.dumps(doc_no_adv, ensure_ascii=False), encoding="utf-8")
    loaded1 = load_configuration(f1, environ=_synthetic_environ())
    assert loaded1.settings.advertisements.campaigns == ()
    assert loaded1.settings.advertisements.routes == ()

    # 2. Config with only routes in advertisements
    doc_routes = dict(doc)
    doc_routes["advertisements"] = {
        "routes": [{"name": "آگهی‌نمونه ✨", "destination_names": ["destination-fa"]}]
    }
    f2 = tmp_path / "routes_only.json"
    f2.write_text(json.dumps(doc_routes, ensure_ascii=False), encoding="utf-8")
    loaded2 = load_configuration(f2, environ=_synthetic_environ())
    assert len(loaded2.settings.advertisements.routes) == 1
    assert loaded2.settings.advertisements.campaigns == ()

    # 3. Config with empty campaigns list
    doc_empty = dict(doc)
    doc_empty["advertisements"] = {"campaigns": []}
    f3 = tmp_path / "empty_campaigns.json"
    f3.write_text(json.dumps(doc_empty, ensure_ascii=False), encoding="utf-8")
    loaded3 = load_configuration(f3, environ=_synthetic_environ())
    assert loaded3.settings.advertisements.campaigns == ()


def test_security_and_error_message_url_redaction(tmp_path: Path) -> None:
    """Verify that error messages do not echo full rejected source URLs."""
    doc = _make_minimal_valid_config_doc()
    sensitive_url = "https://t.me/secret_channel_name_123/999?secret_token=abc123xyz"
    doc["advertisements"]["campaigns"][0]["source_post_url"] = sensitive_url

    config_file = tmp_path / "sensitive_url.json"
    config_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_configuration(config_file, environ=_synthetic_environ())

    error_str = str(exc_info.value)
    # Complete sensitive URL must not be echoed in error message
    assert sensitive_url not in error_str
    assert "abc123xyz" not in error_str
