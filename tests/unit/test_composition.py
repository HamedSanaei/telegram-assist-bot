"""Unit tests for composition-root factories."""

from __future__ import annotations

import pytest

from src.composition import create_ai_service, create_price_source
from src.infrastructure.price.http_price_source import HttpJsonPriceSource
from src.infrastructure.price.nobitex_price_source import NobitexPriceSource
from src.shared.config import (
    AiConfig,
    AiProviderConfig,
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


class TestCreateAiService:
    """Tests for :func:`create_ai_service`."""

    def test_skips_disabled_and_incomplete_providers(self) -> None:
        config = _config(UsdPriceConfig())
        config = AppConfig(
            telegram=config.telegram,
            ai=AiConfig(
                providers=[
                    AiProviderConfig(
                        name="google_ai_studio",
                        enabled=True,
                        api_key="gk",
                        base_url=(
                            "https://generativelanguage.googleapis.com/v1beta/openai/"
                        ),
                        model="gemini-2.0-flash",
                    ),
                    AiProviderConfig(
                        name="groq",
                        enabled=False,
                        api_key="grk",
                        base_url="https://api.groq.com/openai/v1",
                        model="llama-3.3-70b-versatile",
                    ),
                    AiProviderConfig(
                        name="openrouter",
                        enabled=True,
                        api_key="",
                        base_url="https://openrouter.ai/api/v1",
                        model="openai/gpt-4o-mini",
                    ),
                ]
            ),
            database=config.database,
            storage=config.storage,
            vpn_testing=config.vpn_testing,
            scheduler=config.scheduler,
            usd_price=config.usd_price,
            logging=config.logging,
        )

        service = create_ai_service(config)

        assert [provider.name for provider in service._providers] == [
            "google_ai_studio"
        ]

    def test_rejects_when_no_provider_is_usable(self) -> None:
        config = _config(UsdPriceConfig())
        config = AppConfig(
            telegram=config.telegram,
            ai=AiConfig(
                providers=[
                    AiProviderConfig(name="zai", enabled=False),
                    AiProviderConfig(name="deepseek", enabled=True, api_key=""),
                ]
            ),
            database=config.database,
            storage=config.storage,
            vpn_testing=config.vpn_testing,
            scheduler=config.scheduler,
            usd_price=config.usd_price,
            logging=config.logging,
        )

        with pytest.raises(ConfigurationError, match="No enabled AI provider"):
            create_ai_service(config)
