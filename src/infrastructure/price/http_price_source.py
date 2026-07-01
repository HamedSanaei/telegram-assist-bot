"""Configurable HTTP JSON price source for USD price publishing."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from src.domain.entities import DollarPrice
from src.shared.errors import PriceFetchError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def extract_json_path(data: Any, path: str) -> Any:
    """
    Walk a dotted path through nested dicts/lists.

    Args:
        data: Parsed JSON data.
        path: Dotted path such as ``"data.usd.price"`` or ``"rates.0.value"``.
            Numeric segments index into lists.

    Returns:
        The value at the path.

    Raises:
        PriceFetchError: When a segment is missing or has the wrong type.

    Example:
        extract_json_path({"data": {"usd": 61500}}, "data.usd") == 61500
    """
    current = data
    for segment in [s for s in path.split(".") if s]:
        try:
            if isinstance(current, list):
                current = current[int(segment)]
            elif isinstance(current, dict):
                current = current[segment]
            else:
                raise KeyError(segment)
        except (KeyError, IndexError, ValueError) as exc:
            raise PriceFetchError(
                f"JSON path segment '{segment}' not found in price response"
            ) from exc
    return current


class HttpJsonPriceSource:
    """
    Fetches the USD price from a configurable JSON HTTP endpoint.

    The endpoint URL and the JSON path of the price value both come
    from ``configuration.json``, so the source can be swapped without
    code changes.

    Example:
        source = HttpJsonPriceSource("navasan", "https://api...", "usd.value")
        price = await source.fetch_price()
    """

    def __init__(
        self,
        name: str,
        url: str,
        price_json_path: str,
        timeout_seconds: int = 20,
    ) -> None:
        """
        Args:
            name: Source name stored with each price record.
            url: Full endpoint URL returning JSON.
            price_json_path: Dotted path of the price value in the response.
            timeout_seconds: HTTP timeout.
        """
        self.name = name
        self._url = url
        self._path = price_json_path
        self._timeout = timeout_seconds

    async def fetch_price(self) -> DollarPrice:
        """
        Fetch and parse the current USD price.

        Returns:
            A :class:`DollarPrice` stamped with the current UTC time.

        Raises:
            PriceFetchError: When the endpoint is unreachable, returns
                invalid JSON, or the price value cannot be parsed.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise PriceFetchError(f"Price source '{self.name}' failed: {exc}") from exc
        except ValueError as exc:
            raise PriceFetchError(f"Price source '{self.name}' returned non-JSON") from exc

        value = extract_json_path(data, self._path)
        try:
            price = Decimal(str(value).replace(",", "").strip())
        except InvalidOperation as exc:
            raise PriceFetchError(
                f"Price source '{self.name}' value is not numeric: {value!r}"
            ) from exc
        return DollarPrice(
            price=price, source=self.name, fetched_at=datetime.now(timezone.utc)
        )
