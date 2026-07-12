"""Verify deterministic schedule validation and repository delegation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from telegram_assist_bot.application.ports import (
    ScheduleRepository,
    ScheduleReservation,
)
from telegram_assist_bot.application.scheduling import SchedulePost, ScheduleRequest
from telegram_assist_bot.domain import (
    ScheduledPublication,
    schedule_identity,
    validate_interval,
)

NOW = datetime(2026, 7, 12, 23, 59, tzinfo=UTC)


class Repository:
    """Capture one schedule reservation."""

    def __init__(self) -> None:
        self.values: dict[str, object] | None = None

    async def reserve(self, **values: object) -> ScheduleReservation:
        self.values = values
        due_at = values["now"]
        interval = values["interval"]
        assert isinstance(due_at, datetime)
        assert isinstance(interval, timedelta)
        destination_id = values["destination_id"]
        assert type(destination_id) is int
        return ScheduleReservation(
            ScheduledPublication(
                str(values["job_id"]),
                str(values["post_id"]),
                destination_id,
                due_at + interval,
            ),
            True,
        )


def test_delegates_aware_utc_clock_and_configured_interval() -> None:
    repository = Repository()
    use_case = SchedulePost(
        cast("ScheduleRepository", repository),
        clock=lambda: NOW,
        interval_seconds=300,
    )
    result = asyncio.run(
        use_case.execute(ScheduleRequest("post", -1001, True, True, True))
    )
    assert result.created
    assert result.job.due_at == NOW + timedelta(seconds=300)
    assert repository.values is not None
    assert repository.values["interval"] == timedelta(seconds=300)


@pytest.mark.parametrize("seconds", [0, -1, 86_401, float("inf"), float("nan")])
def test_rejects_invalid_intervals(seconds: float) -> None:
    with pytest.raises(ValueError, match="outside the supported bounds"):
        validate_interval(seconds)


@pytest.mark.parametrize(
    "field", ["authorized", "post_publishable", "scheduled_selected"]
)
def test_rejects_invalid_action_before_repository(field: str) -> None:
    repository = Repository()
    request_values = {
        "post_id": "post",
        "destination_id": -1,
        "authorized": True,
        "post_publishable": True,
        "scheduled_selected": True,
    }
    request_values[field] = False
    use_case = SchedulePost(
        cast("ScheduleRepository", repository),
        clock=lambda: NOW,
        interval_seconds=10,
    )
    with pytest.raises(PermissionError):
        asyncio.run(use_case.execute(ScheduleRequest(**request_values)))  # type: ignore[arg-type]
    assert repository.values is None


def test_schedule_identity_is_stable_and_destination_scoped() -> None:
    assert schedule_identity("post", -1) == schedule_identity("post", -1)
    assert schedule_identity("post", -1) != schedule_identity("post", -2)
