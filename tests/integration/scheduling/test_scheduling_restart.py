"""Reuse the concrete lease-restart scenario as a stabilization gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.integration.scheduling import test_schedule_worker_restart

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_restart_recovers_expired_claim(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Execute the same guarded real-MongoDB recovery contract."""
    test_schedule_worker_restart.test_expired_claim_is_recovered_by_new_worker(
        mongodb_test_settings
    )
