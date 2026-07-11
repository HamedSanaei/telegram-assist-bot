"""Expose the guarded loopback MongoDB fixture to controlled E2E tests."""

from tests.integration.infrastructure.persistence.conftest import (
    mongodb_test_settings as mongodb_test_settings,
)

__all__ = ("mongodb_test_settings",)
