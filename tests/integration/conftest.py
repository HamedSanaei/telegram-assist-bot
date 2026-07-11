"""Expose the guarded T004 MongoDB fixture to foundation integration tests."""

from tests.integration.infrastructure.persistence.conftest import (
    mongodb_test_settings as mongodb_test_settings,
)

__all__ = ("mongodb_test_settings",)
