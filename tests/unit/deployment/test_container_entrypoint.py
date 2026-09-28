"""Unit proofs for the container entrypoint restart contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.bootstrap.runtime import FoundationExitCode
from telegram_assist_bot.container_entrypoint import (
    container_exit_code,
    container_main,
    report_terminal_startup_failure,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def test_terminal_startup_failure_stops_the_container_cleanly() -> None:
    assert container_exit_code(int(FoundationExitCode.PERMANENT_STARTUP_FAILURE)) == 0


@pytest.mark.parametrize(
    ("application_exit_code", "expected"),
    [
        (int(FoundationExitCode.SUCCESS), 0),
        (int(FoundationExitCode.CONFIGURATION_ERROR), 2),
        (int(FoundationExitCode.INFRASTRUCTURE_ERROR), 3),
        (7, 7),
        (-1, -1),
    ],
)
def test_other_exit_codes_are_preserved_for_bounded_restarts(
    application_exit_code: int, expected: int
) -> None:
    assert container_exit_code(application_exit_code) == expected


def test_permanent_failure_reports_once_and_returns_success() -> None:
    reported: list[FoundationExitCode] = []

    def runner(_argv: Sequence[str] | None) -> int:
        return int(FoundationExitCode.PERMANENT_STARTUP_FAILURE)

    exit_code = container_main(["runtime"], runner=runner, reporter=reported.append)

    assert exit_code == 0
    assert reported == [FoundationExitCode.PERMANENT_STARTUP_FAILURE]


def test_transient_failure_is_not_reported_as_terminal() -> None:
    reported: list[FoundationExitCode] = []

    def runner(_argv: Sequence[str] | None) -> int:
        return int(FoundationExitCode.INFRASTRUCTURE_ERROR)

    exit_code = container_main(["runtime"], runner=runner, reporter=reported.append)

    assert exit_code == 3
    assert reported == []


def test_reporting_failure_never_masks_a_terminal_stop() -> None:
    def reporter(_code: FoundationExitCode) -> None:
        raise RuntimeError("synthetic reporting failure")

    exit_code = container_main(
        ["runtime"],
        runner=lambda _argv: int(FoundationExitCode.PERMANENT_STARTUP_FAILURE),
        reporter=reporter,
    )

    assert exit_code == 0


def test_terminal_event_is_structured_and_secret_free(
    capfd: pytest.CaptureFixture[str],
) -> None:
    report_terminal_startup_failure(FoundationExitCode.PERMANENT_STARTUP_FAILURE)

    captured = capfd.readouterr().err.strip().splitlines()
    assert len(captured) == 1
    event = json.loads(captured[0])
    assert event["event_name"] == "container_terminal_startup_failure"
    assert event["level"] == "CRITICAL"
    assert event["application_exit_code"] == 4
    assert event["restart_policy_result"] == "stopped"
    assert "token" not in json.dumps(event).casefold()
