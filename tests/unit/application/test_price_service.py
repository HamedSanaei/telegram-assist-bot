"""Unit tests for USD price publishing and change formatting."""

from __future__ import annotations

from decimal import Decimal

from src.application.price_service import UsdPriceService, format_price_message
from src.domain.entities import DestinationChannel
from src.domain.enums import ChannelKind
from tests.unit.application.fakes import (
    FakeChannelRepository,
    FakePriceHistoryRepository,
    FakePriceSource,
    FakePublisher,
)


class TestFormatPriceMessage:
    """Tests for :func:`format_price_message`."""

    def test_first_record_has_no_comparison(self) -> None:
        text = format_price_message(Decimal("61500"), None)
        assert "61,500" in text
        assert "مقایسه‌ای موجود نیست" in text

    def test_increase_shows_up_arrow(self) -> None:
        text = format_price_message(Decimal("62000"), Decimal("61500"))
        assert "🔺" in text
        assert "500" in text

    def test_decrease_shows_down_arrow(self) -> None:
        text = format_price_message(Decimal("61000"), Decimal("61500"))
        assert "🔻" in text
        assert "500" in text

    def test_no_change(self) -> None:
        text = format_price_message(Decimal("61500"), Decimal("61500"))
        assert "بدون تغییر" in text


class TestUsdPriceService:
    """Tests for :class:`UsdPriceService`."""

    def _build(
        self, prices: list[Decimal]
    ) -> tuple[UsdPriceService, FakePriceHistoryRepository, FakePublisher]:
        history = FakePriceHistoryRepository()
        publisher = FakePublisher()
        channels = FakeChannelRepository(
            [
                DestinationChannel(
                    chat_id=-100, title="News", kind=ChannelKind.NEWS, publish_usd_price=True
                ),
                DestinationChannel(chat_id=-200, title="VPN", kind=ChannelKind.VPN),
            ]
        )
        service = UsdPriceService(FakePriceSource(prices), history, channels, publisher)
        return service, history, publisher

    async def test_publishes_only_to_price_channels(self) -> None:
        service, history, publisher = self._build([Decimal("61500")])
        await service.publish_usd_price()
        assert len(publisher.texts) == 1
        assert publisher.texts[0][0] == -100
        assert len(history.prices) == 1

    async def test_second_publish_includes_change(self) -> None:
        service, history, publisher = self._build(
            [Decimal("61500"), Decimal("62100")]
        )
        await service.publish_usd_price()
        await service.publish_usd_price()
        assert len(history.prices) == 2
        second_text = publisher.texts[1][1]
        assert "🔺" in second_text
        assert "600" in second_text
