"""Smoke tests for package metadata and architecture-layer imports."""

from __future__ import annotations

from importlib import import_module, metadata

import telegram_assist_bot

DISTRIBUTION_NAME = "telegram-assist-bot"
ARCHITECTURE_PACKAGES = (
    "application",
    "bootstrap",
    "domain",
    "infrastructure",
    "presentation",
    "shared",
    "workers",
)
T004_MODULES = (
    "application.ports",
    "application.ports.post_repository",
    "infrastructure.persistence",
    "infrastructure.persistence.mongodb",
    "infrastructure.persistence.mongodb.client",
    "infrastructure.persistence.mongodb.errors",
    "infrastructure.persistence.mongodb.indexes",
    "infrastructure.persistence.mongodb.post_mapper",
    "infrastructure.persistence.mongodb.post_repository",
)
T005_MODULES = (
    "shared.errors",
    "shared.observability",
    "shared.observability.context",
    "shared.observability.logging",
    "shared.observability.redaction",
    "shared.retry",
    "shared.retry.executor",
    "shared.retry.policy",
)
T006_MODULES = (
    "__main__",
    "bootstrap",
    "bootstrap.cli",
    "bootstrap.runtime",
)


def test_distribution_metadata_matches_import_package() -> None:
    """Keep public package version and runtime requirements synchronized."""
    assert metadata.version(DISTRIBUTION_NAME) == telegram_assist_bot.__version__
    assert frozenset(metadata.requires(DISTRIBUTION_NAME) or ()) == frozenset(
        {
            "pydantic<3,>=2.12.0",
            "pymongo<5,>=4.13.0",
            "tzdata>=2025.2",
        }
    )


def test_architecture_scaffolds_are_importable_and_documented() -> None:
    """Expose every planned layer without adding product behavior."""
    for package_name in ARCHITECTURE_PACKAGES:
        module = import_module(f"telegram_assist_bot.{package_name}")
        assert module.__doc__


def test_t004_repository_modules_are_importable_and_documented() -> None:
    """Keep the T004 repository boundary and MongoDB adapter importable."""
    for module_name in T004_MODULES:
        module = import_module(f"telegram_assist_bot.{module_name}")
        assert module.__doc__


def test_t005_foundation_modules_are_importable_and_documented() -> None:
    """Keep the T005 observability and retry foundation importable."""
    for module_name in T005_MODULES:
        module = import_module(f"telegram_assist_bot.{module_name}")
        assert module.__doc__


def test_t006_startup_modules_are_importable_and_documented() -> None:
    """Keep the T006 composition root and entry point import-safe."""
    for module_name in T006_MODULES:
        module = import_module(f"telegram_assist_bot.{module_name}")
        assert module.__doc__
