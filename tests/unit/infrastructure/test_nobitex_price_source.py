"""Unit tests for the Nobitex USD price source."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from src.infrastructure.price.nobitex_price_source import NobitexPriceSource
from src.shared.errors import PriceFetchError


def _mock_source(
    status_code: int = 200,
    body: dict | str | None = None,
) -> NobitexPriceSource:
    """Build a source whose HTTP layer returns the given canned response."""

    def respond(request: httpx.Request) -> httpx.Response:
        content = body if isinstance(body, str) else json.dumps(body or {})
        return httpx.Response(status_code, text=content)

    return NobitexPriceSource(transport=httpx.MockTransport(respond))


class TestNobitexPriceSource:
    """Tests for :class:`NobitexPriceSource`."""

    async def test_fetch_converts_rials_to_toman(self) -> None:
        source = _mock_source(
            body={"status": "ok", "stats": {"usdt-rls": {"latest": "1075000"}}}
        )
        price = await source.fetch_price()
        assert price.price == Decimal("107500")
        assert price.source == "nobitex"

    async def test_numeric_latest_value_is_accepted(self) -> None:
        source = _mock_source(body={"stats": {"usdt-rls": {"latest": 1075000}}})
        price = await source.fetch_price()
        assert price.price == Decimal("107500")

    async def test_missing_symbol_raises(self) -> None:
        source = _mock_source(body={"status": "ok", "stats": {}})
        with pytest.raises(PriceFetchError, match="usdt-rls"):
            await source.fetch_price()

    async def test_http_error_raises(self) -> None:
        source = _mock_source(status_code=503, body={})
        with pytest.raises(PriceFetchError, match="request failed"):
            await source.fetch_price()

    async def test_non_json_response_raises(self) -> None:
        source = _mock_source(body="<html>blocked</html>")
        with pytest.raises(PriceFetchError, match="non-JSON"):
            await source.fetch_price()

    async def test_non_numeric_price_raises(self) -> None:
        source = _mock_source(body={"stats": {"usdt-rls": {"latest": "N/A"}}})
        with pytest.raises(PriceFetchError, match="not numeric"):
            await source.fetch_price()
