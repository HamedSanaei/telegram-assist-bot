"""Unit tests for composition-root factories."""

from __future__ import annotations

import pytest

from src.composition import create_price_source
from src.infrastructure.price.http_price_source import HttpJsonPriceSource
from src.infrastructure.price.nobitex_price_source import NobitexPriceSource
from src.shared.config import (
    AiConfig,
    AppConfig,
    DatabaseConfig,
    LoggingConfig,
    SchedulerConfig,
    StorageConfig,
    TelegramConfig,
    UsdPriceConfig,
    VpnTestingConfig,
)
from src.shared.errors import ConfigurationError


def _config(usd_price: UsdPriceConfig) -> AppConfig:
    """Build a minimal AppConfig with the given usd_price section."""
    return AppConfig(
        telegram=TelegramConfig(
            bot_token="t", approval_bot_token="a", api_id="1", api_hash="h"
        ),
        ai=AiConfig(),
        database=DatabaseConfig(),
        storage=StorageConfig(),
        vpn_testing=VpnTestingConfig(),
        scheduler=SchedulerConfig(),
        usd_price=usd_price,
        logging=LoggingConfig(),
    )


class TestCreatePriceSource:
    """Tests for :func:`create_price_source`."""

    def test_nobitex_provider_selected(self) -> None:
        source = create_price_source(_config(UsdPriceConfig(provider="nobitex")))
        assert isinstance(source, NobitexPriceSource)

    def test_http_json_provider_selected(self) -> None:
        source = create_price_source(
            _config(
                UsdPriceConfig(
                    provider="http_json",
                    source_url="https://example.com/rates",
                    price_json_path="usd.value",
                )
            )
        )
        assert isinstance(source, HttpJsonPriceSource)

    def test_http_json_without_url_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="http_json"):
            create_price_source(_config(UsdPriceConfig(provider="http_json")))

    def test_unknown_provider_rejected(self) -> None:
        with pytest.raises(ConfigurationError, match="Unknown USD price provider"):
            create_price_source(_config(UsdPriceConfig(provider="bonbast")))
