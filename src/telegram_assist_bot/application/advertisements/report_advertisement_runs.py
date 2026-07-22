"""Bounded read-only administrator queries for advertisement execution reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar
from zoneinfo import ZoneInfo

from telegram_assist_bot.application.ports.advertisement_repository import (
    AdvertisementReportKind,
    AdvertisementReportQuery,
    AdvertisementReportRecord,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.ports import AdvertisementReportRepository

_MAX_TELEGRAM_TEXT = 4096
_OMITTED_FOOTER = "\nموارد بیشتری وجود دارد؛ ادامهٔ فهرست نمایش داده نشد."  # noqa: RUF001


class AdvertisementReportStatus(StrEnum):
    """Stable application result for one report request."""

    READY = "ready"
    NO_DESTINATIONS = "no_destinations"


@dataclass(frozen=True, slots=True)
class AdvertisementReport:
    """Return safe records and overflow state without presentation dependencies."""

    kind: AdvertisementReportKind
    records: tuple[AdvertisementReportRecord, ...]
    truncated: bool
    timezone: str
    status: AdvertisementReportStatus = AdvertisementReportStatus.READY


class ReportAdvertisementRuns:
    """Build approved UTC ranges and execute one bounded read-only query."""

    def __init__(
        self,
        repository: AdvertisementReportRepository,
        *,
        timezone: str,
        upcoming_horizon_days: int,
        failure_horizon_days: int,
        max_items: int,
        clock: Callable[[], datetime],
    ) -> None:
        """Store already-validated explicit report policy."""
        self._repository = repository
        self._timezone = ZoneInfo(timezone)
        self._timezone_name = timezone
        self._upcoming_days = upcoming_horizon_days
        self._failure_days = failure_horizon_days
        self._max_items = max_items
        self._clock = clock

    async def execute(
        self,
        kind: AdvertisementReportKind,
        *,
        allowed_destination_ids: frozenset[int],
    ) -> AdvertisementReport:
        """Return no records for empty access and otherwise query max plus one."""
        if not allowed_destination_ids:
            return AdvertisementReport(
                kind,
                (),
                False,
                self._timezone_name,
                AdvertisementReportStatus.NO_DESTINATIONS,
            )
        now = self._now()
        starts_at, ends_at = self._bounds(kind, now)
        records = await self._repository.list_report_records(
            AdvertisementReportQuery(
                kind,
                starts_at,
                ends_at,
                allowed_destination_ids,
                self._max_items + 1,
            )
        )
        return AdvertisementReport(
            kind,
            records[: self._max_items],
            len(records) > self._max_items,
            self._timezone_name,
        )

    def _bounds(
        self, kind: AdvertisementReportKind, now: datetime
    ) -> tuple[datetime, datetime]:
        if kind is AdvertisementReportKind.TODAY:
            local_now = now.astimezone(self._timezone)
            local_start = datetime.combine(
                local_now.date(), time.min, tzinfo=self._timezone
            )
            return (
                local_start.astimezone(UTC),
                (local_start + timedelta(days=1)).astimezone(UTC),
            )
        if kind is AdvertisementReportKind.UPCOMING:
            return now, now + timedelta(days=self._upcoming_days)
        return now - timedelta(days=self._failure_days), now

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("report clock must return an aware instant")
        return value.astimezone(UTC)


class RenderAdvertisementReport:
    """Render short Persian reports independently from the Telegram Bot SDK."""

    _EMPTY: ClassVar[dict[AdvertisementReportKind, str]] = {
        AdvertisementReportKind.TODAY: "امروز هیچ اجرای تبلیغاتی ثبت نشده است.",
        AdvertisementReportKind.UPCOMING: (
            "در بازهٔ آینده هیچ Slot تبلیغاتی وجود ندارد."
        ),
        AdvertisementReportKind.FAILURES: (
            "در بازهٔ اخیر هیچ خطای تبلیغاتی ثبت نشده است."
        ),
    }
    _TITLES: ClassVar[dict[AdvertisementReportKind, str]] = {
        AdvertisementReportKind.TODAY: "گزارش اجرای تبلیغات امروز",
        AdvertisementReportKind.UPCOMING: (
            "Slotهای تبلیغاتی آینده"  # noqa: RUF001
        ),
        AdvertisementReportKind.FAILURES: "خطاهای اخیر تبلیغات",
    }

    def render(self, report: AdvertisementReport) -> str:
        """Render complete item boundaries within Telegram's safe text limit."""
        if not report.records:
            return self._EMPTY[report.kind]
        timezone = ZoneInfo(report.timezone)
        lines = [self._TITLES[report.kind]]
        omitted = report.truncated
        for record in report.records:
            item = self._render_record(record, timezone)
            candidate = "\n\n".join((*lines, item))
            reserved = len(_OMITTED_FOOTER) if omitted else 0
            if len(candidate) + reserved > _MAX_TELEGRAM_TEXT:
                omitted = True
                break
            lines.append(item)
        result = "\n\n".join(lines)
        if omitted and len(result) + len(_OMITTED_FOOTER) <= _MAX_TELEGRAM_TEXT:
            result += _OMITTED_FOOTER
        return result

    @staticmethod
    def _render_record(record: AdvertisementReportRecord, timezone: ZoneInfo) -> str:
        scheduled = record.scheduled_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
        parts = [
            f"• {scheduled} | {record.destination_name}",
            f"کمپین: {record.campaign_id}",
            f"وضعیت: {record.status} | Retry: {record.retry_count}",
        ]
        if record.published_at is not None:
            actual = record.published_at.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
            parts.append(f"انتشار واقعی: {actual}")
        if record.message_ids:
            parts.append("شناسه پیام: " + ", ".join(map(str, record.message_ids)))
        if record.execution_delay_seconds is not None:
            parts.append(f"تأخیر اجرا: {record.execution_delay_seconds:.0f} ثانیه")
        if record.failure_category is not None:
            parts.append(f"خطا: {record.failure_category}")
        return "\n".join(parts)


__all__ = (
    "AdvertisementReport",
    "AdvertisementReportStatus",
    "RenderAdvertisementReport",
    "ReportAdvertisementRuns",
)
