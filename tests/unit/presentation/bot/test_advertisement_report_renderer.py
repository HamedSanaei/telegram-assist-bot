"""Unit tests for the SDK-independent Persian advertisement report renderer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telegram_assist_bot.application.advertisements.report_advertisement_runs import (
    AdvertisementReport,
    RenderAdvertisementReport,
)
from telegram_assist_bot.application.ports import (
    AdvertisementReportKind,
    AdvertisementReportRecord,
)

NOW = datetime(2026, 7, 22, 8, tzinfo=UTC)


def record(*, score: int = 1, status: str = "completed") -> AdvertisementReportRecord:
    return AdvertisementReportRecord(
        f"record-{score}",
        "کمپین‌ویژه✨",
        "کانال مقصد",
        -1001,
        status,
        NOW,
        NOW if status == "completed" else None,
        (701,) if status == "completed" else (),
        score,
        12.0 if status == "completed" else None,
        "temporary_failure" if status != "completed" else None,
        "SAFE_REASON",
        NOW if status != "completed" else None,
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (AdvertisementReportKind.TODAY, "امروز هیچ اجرای تبلیغاتی ثبت نشده است."),
        (
            AdvertisementReportKind.UPCOMING,
            "در بازهٔ آینده هیچ Slot تبلیغاتی وجود ندارد.",
        ),
        (
            AdvertisementReportKind.FAILURES,
            "در بازهٔ اخیر هیچ خطای تبلیغاتی ثبت نشده است.",
        ),
    ],
)
def test_exact_empty_messages(kind: AdvertisementReportKind, expected: str) -> None:
    rendered = RenderAdvertisementReport().render(
        AdvertisementReport(kind, (), False, "Asia/Tehran")
    )
    assert rendered == expected


def test_success_preserves_persian_zwnj_emoji_and_execution_metadata() -> None:
    rendered = RenderAdvertisementReport().render(
        AdvertisementReport(
            AdvertisementReportKind.TODAY, (record(),), False, "Asia/Tehran"
        )
    )
    assert "کمپین‌ویژه✨" in rendered
    assert "شناسه پیام: 701" in rendered
    assert "تأخیر اجرا: 12 ثانیه" in rendered


def test_retry_and_final_failure_show_only_sanitized_category() -> None:
    rendered = RenderAdvertisementReport().render(
        AdvertisementReport(
            AdvertisementReportKind.FAILURES,
            (
                record(status="waiting_for_retry"),
                record(score=3, status="permanent_failed"),
            ),
            False,
            "Asia/Tehran",
        )
    )
    assert "waiting_for_retry" in rendered
    assert "permanent_failed" in rendered
    assert rendered.count("خطا: temporary_failure") == 2
    assert "SAFE_REASON" not in rendered


def test_truncation_footer_is_complete_and_bounded() -> None:
    rendered = RenderAdvertisementReport().render(
        AdvertisementReport(
            AdvertisementReportKind.UPCOMING,
            (record(status="scheduled"),),
            True,
            "Asia/Tehran",
        )
    )
    assert rendered.endswith("ادامهٔ فهرست نمایش داده نشد.")
    assert len(rendered) <= 4096
