"""Reuse cancellation races as the milestone stabilization gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.integration.mongodb import test_schedule_cancellation

from telegram_assist_bot.domain import CancellationPolicy

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_cancel_claim_race(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Execute the concrete atomic cancel/claim race."""
    test_schedule_cancellation.test_cancel_claim_race_has_one_consistent_winner(
        mongodb_test_settings
    )


@pytest.mark.parametrize("policy", list(CancellationPolicy))
def test_both_queue_policies_survive_restart(
    mongodb_test_settings: MongoTestSettings,
    policy: CancellationPolicy,
) -> None:
    """Execute both destination-scoped cancellation policy contracts."""
    test_schedule_cancellation.test_cancel_policy_is_destination_scoped_and_restart_safe(
        mongodb_test_settings,
        policy,
    )
