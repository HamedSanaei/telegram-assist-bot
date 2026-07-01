"""Use case: fetch, record, and publish USD price updates."""

from __future__ import annotations

from decimal import Decimal

from src.domain.entities import DollarPrice
from src.domain.interfaces import (
    ChannelRepository,
    MessagePublisher,
    PriceHistoryRepository,
    PriceSource,
)
from src.shared.errors import TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def format_price_message(price: Decimal, previous: Decimal | None) -> str:
    """
    Build the Persian USD price message including the change indicator.

    Args:
        price: The newly fetched price.
        previous: The previously recorded price, or ``None`` on first run.

    Returns:
        A UTF-8 Persian message ready to publish.

    Example:
        format_price_message(Decimal("61500"), Decimal("61000"))
    """
    lines = [f"💵 نرخ دلار آمریکا: {price:,.0f} تومان"]
    if previous is None:
        lines.append("ℹ️ اولین ثبت قیمت؛ مقایسه‌ای موجود نیست.")
    else:
        diff = price - previous
        if diff > 0:
            lines.append(f"🔺 افزایش {diff:,.0f} تومان نسبت به اعلام قبلی")
        elif diff < 0:
            lines.append(f"🔻 کاهش {-diff:,.0f} تومان نسبت به اعلام قبلی")
        else:
            lines.append("⏸ بدون تغییر نسبت به اعلام قبلی")
    return "\n".join(lines)


class UsdPriceService:
    """
    Fetches the USD price twice a day, stores it in SQLite history, and
    publishes it (with the change vs. the previous record) to every
    destination channel flagged with ``publish_usd_price``.

    Example:
        service = UsdPriceService(source, history, channels, publisher)
        await service.publish_usd_price()
    """

    def __init__(
        self,
        source: PriceSource,
        history: PriceHistoryRepository,
        channels: ChannelRepository,
        publisher: MessagePublisher,
    ) -> None:
        """
        Args:
            source: Configured USD price source.
            history: SQLite price history repository.
            channels: Channel repository (used to find price channels).
            publisher: Telegram publisher.
        """
        self._source = source
        self._history = history
        self._channels = channels
        self._publisher = publisher

    async def publish_usd_price(self) -> DollarPrice:
        """
        Run one full price publish cycle.

        Returns:
            The newly stored :class:`DollarPrice`.

        Raises:
            PriceFetchError: When the price source fails.
            RepositoryError: When storing the price fails.

        Side effects:
            Sends one Telegram message per configured price channel.
            Per-channel send failures are logged but do not abort the
            cycle for the remaining channels.
        """
        price = await self._source.fetch_price()
        previous = await self._history.get_latest()
        text = format_price_message(price.price, previous.price if previous else None)
        await self._history.save(price)

        for channel in await self._channels.list_price_channels():
            try:
                await self._publisher.publish_text(channel.chat_id, text)
                logger.info(
                    "Published USD price channel=%s price=%s", channel.chat_id, price.price
                )
            except TelegramPublishError as exc:
                logger.error(
                    "USD price publish failed channel=%s error=%s", channel.chat_id, exc
                )
        return price
