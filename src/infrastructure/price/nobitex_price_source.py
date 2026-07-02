"""Nobitex USD price source.

Fetches the USDT/IRR rate from the public Nobitex market-stats API
(https://apiv2.nobitex.ir). USDT (Tether) is the standard free-market
proxy for the USD rate in Iran. No API key is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from src.domain.entities import DollarPrice
from src.shared.errors import PriceFetchError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_NOBITEX_STATS_URL = "https://apiv2.nobitex.ir/market/stats"
_SRC_CURRENCY = "usdt"
_DST_CURRENCY = "rls"
_RIALS_PER_TOMAN = 10


class NobitexPriceSource:
    """
    Fetches the USD (USDT) price in Toman from the Nobitex exchange.

    Calls ``GET /market/stats?srcCurrency=usdt&dstCurrency=rls`` and reads
    ``stats["usdt-rls"]["latest"]``, which is expressed in Rials, then
    converts it to Toman to match the published message format.

    Example:
        source = NobitexPriceSource()
        price = await source.fetch_price()  # DollarPrice in Toman
    """

    def __init__(
        self,
        url: str = DEFAULT_NOBITEX_STATS_URL,
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            url: Nobitex market-stats endpoint (override for mirrors).
            timeout_seconds: HTTP timeout.
            transport: Optional httpx transport, used by unit tests to
                mock the API without real network access.
        """
        self.name = "nobitex"
        self._url = url
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch_price(self) -> DollarPrice:
        """
        Fetch and parse the current USDT/Toman price.

        Returns:
            A :class:`DollarPrice` in Toman stamped with the current UTC time.

        Raises:
            PriceFetchError: When the endpoint is unreachable, returns
                invalid JSON, or the price value is missing/non-numeric.
        """
        params = {"srcCurrency": _SRC_CURRENCY, "dstCurrency": _DST_CURRENCY}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(self._url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise PriceFetchError(f"Nobitex request failed: {exc}") from exc
        except ValueError as exc:
            raise PriceFetchError("Nobitex returned non-JSON response") from exc

        symbol = f"{_SRC_CURRENCY}-{_DST_CURRENCY}"
        try:
            latest = data["stats"][symbol]["latest"]
        except (KeyError, TypeError) as exc:
            raise PriceFetchError(
                f"Nobitex response is missing stats['{symbol}']['latest']"
            ) from exc

        try:
            rials = Decimal(str(latest).replace(",", "").strip())
        except InvalidOperation as exc:
            raise PriceFetchError(f"Nobitex price is not numeric: {latest!r}") from exc

        toman = rials / _RIALS_PER_TOMAN
        logger.info("Fetched Nobitex USD price: %s Toman", toman)
        return DollarPrice(
            price=toman, source=self.name, fetched_at=datetime.now(timezone.utc)
        )
