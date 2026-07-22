"""Unit tests for bounded T053 advertisement report queries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from telegram_assist_bot.application.advertisements.report_advertisement_runs import (
    AdvertisementReport,
    ReportAdvertisementRuns,
)
from telegram_assist_bot.application.ports import (
    AdvertisementReportKind,
    AdvertisementReportQuery,
    AdvertisementReportRecord,
)

NOW = datetime(2026, 7, 22, 20, 30, tzinfo=UTC)


class Repository:
    def __init__(self, records: tuple[AdvertisementReportRecord, ...] = ()) -> None:
        self.records = records
        self.queries: list[AdvertisementReportQuery] = []

    async def list_report_records(
        self, query: AdvertisementReportQuery
    ) -> tuple[AdvertisementReportRecord, ...]:
        self.queries.append(query)
        return self.records[: query.limit]


def run_report(
    repository: Repository,
    kind: AdvertisementReportKind,
    *,
    destinations: frozenset[int] = frozenset({-1001}),
    max_items: int = 2,
) -> AdvertisementReport:
    service = ReportAdvertisementRuns(
        repository,
        timezone="Asia/Tehran",
        upcoming_horizon_days=7,
        failure_horizon_days=7,
        max_items=max_items,
        clock=lambda: NOW,
    )
    return asyncio.run(service.execute(kind, allowed_destination_ids=destinations))


def record(index: int) -> AdvertisementReportRecord:
    return AdvertisementReportRecord(
        str(index),
        f"campaign-{index}",
        "مقصد خبر",
        -1001,
        "scheduled",
        NOW + timedelta(hours=index),
        None,
        (),
        0,
        None,
        None,
        None,
        None,
    )


def test_today_uses_calendar_day_in_explicit_timezone() -> None:
    repository = Repository()
    run_report(repository, AdvertisementReportKind.TODAY)
    query = repository.queries[0]
    assert query.starts_at == datetime(2026, 7, 22, 20, 30, tzinfo=UTC)
    assert query.ends_at == datetime(2026, 7, 23, 20, 30, tzinfo=UTC)


def test_upcoming_and_failure_boundaries_are_exact_and_bounded() -> None:
    upcoming = Repository()
    run_report(upcoming, AdvertisementReportKind.UPCOMING)
    assert upcoming.queries[0].starts_at == NOW
    assert upcoming.queries[0].ends_at == NOW + timedelta(days=7)
    assert upcoming.queries[0].limit == 3

    failures = Repository()
    run_report(failures, AdvertisementReportKind.FAILURES)
    assert failures.queries[0].starts_at == NOW - timedelta(days=7)
    assert failures.queries[0].ends_at == NOW


def test_max_items_plus_one_sets_truncation_without_returning_extra_item() -> None:
    repository = Repository(tuple(record(index) for index in range(3)))
    report = run_report(repository, AdvertisementReportKind.UPCOMING)
    assert len(report.records) == 2
    assert report.truncated is True
    assert repository.queries[0].limit == 3


def test_exact_max_items_is_not_truncated() -> None:
    repository = Repository((record(1), record(2)))
    report = run_report(repository, AdvertisementReportKind.UPCOMING)
    assert len(report.records) == 2
    assert report.truncated is False


def test_no_allowed_destinations_does_not_query_repository() -> None:
    repository = Repository((record(1),))
    report = run_report(
        repository, AdvertisementReportKind.TODAY, destinations=frozenset()
    )
    assert report.records == ()
    assert repository.queries == []
