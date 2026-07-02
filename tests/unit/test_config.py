"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.shared.config import load_configuration, validate_main_app_config
from src.shared.errors import ConfigurationError


def _valid_config() -> dict:
    """Return a minimal valid configuration dictionary."""
    return {
        "telegram": {
            "bot_token": "123:abc",
            "approval_bot_token": "456:def",
            "api_id": "11111",
            "api_hash": "hash",
            "source_channels": ["@منبع_خبر"],
            "destination_channels": [
                {
                    "chat_id": -1001,
                    "title": "کانال خبری",
                    "kind": "news",
                    "publish_usd_price": True,
                }
            ],
            "admin_user_ids": [42],
        },
        "ai": {
            "primary_provider": "zai",
            "fallback_provider": "deepseek",
            "zai_api_key": "zk",
            "deepseek_api_key": "dk",
        },
        "database": {
            "sqlite_path": "data/app.db",
            "mongodb_connection_string": "mongodb://localhost:27017",
            "mongodb_database": "telegram_admin_bot",
        },
    }


def _write(tmp_path: Path, data: dict) -> Path:
    """Write a config dict to a UTF-8 JSON file."""
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestLoadConfiguration:
    """Tests for :func:`load_configuration`."""

    def test_loads_valid_config_with_persian_values(self, tmp_path: Path) -> None:
        config = load_configuration(_write(tmp_path, _valid_config()))
        assert config.telegram.bot_token == "123:abc"
        assert config.telegram.source_channels == ["@منبع_خبر"]
        assert config.telegram.destination_channels[0].title == "کانال خبری"
        assert config.telegram.destination_channels[0].publish_usd_price is True
        assert config.ai.primary_provider == "zai"
        assert config.storage.retention_days == 14
        assert config.scheduler.usd_price_publish_times == ["09:00", "21:00"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            load_configuration(tmp_path / "missing.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "configuration.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration(path)

    def test_missing_section_raises(self, tmp_path: Path) -> None:
        data = _valid_config()
        del data["telegram"]
        with pytest.raises(ConfigurationError):
            load_configuration(_write(tmp_path, data))

    def test_destination_without_chat_id_raises(self, tmp_path: Path) -> None:
        data = _valid_config()
        data["telegram"]["destination_channels"] = [{"title": "بدون آیدی"}]
        with pytest.raises(ConfigurationError):
            load_configuration(_write(tmp_path, data))

    def test_example_template_is_loadable(self) -> None:
        """The committed template must always stay parseable."""
        example = Path(__file__).resolve().parents[2] / "config" / "configuration.example.json"
        config = load_configuration(example)
        assert config.ai.primary_provider == "zai"
        assert config.ai.fallback_provider == "deepseek"
        assert config.usd_price.provider == "nobitex"

    def test_usd_price_provider_defaults_to_nobitex(self, tmp_path: Path) -> None:
        config = load_configuration(_write(tmp_path, _valid_config()))
        assert config.usd_price.provider == "nobitex"


class TestValidateMainAppConfig:
    """Tests for :func:`validate_main_app_config`."""

    def test_valid_config_passes(self, tmp_path: Path) -> None:
        config = load_configuration(_write(tmp_path, _valid_config()))
        validate_main_app_config(config)

    def test_empty_bot_token_rejected(self, tmp_path: Path) -> None:
        data = _valid_config()
        data["telegram"]["bot_token"] = ""
        config = load_configuration(_write(tmp_path, data))
        with pytest.raises(ConfigurationError):
            validate_main_app_config(config)

    def test_no_admins_rejected(self, tmp_path: Path) -> None:
        data = _valid_config()
        data["telegram"]["admin_user_ids"] = []
        config = load_configuration(_write(tmp_path, data))
        with pytest.raises(ConfigurationError):
            validate_main_app_config(config)
