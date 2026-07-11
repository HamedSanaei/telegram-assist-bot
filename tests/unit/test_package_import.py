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


def test_distribution_metadata_matches_import_package() -> None:
    """Keep public package version and runtime requirements synchronized."""
    assert metadata.version(DISTRIBUTION_NAME) == telegram_assist_bot.__version__
    assert frozenset(metadata.requires(DISTRIBUTION_NAME) or ()) == frozenset(
        {
            "pydantic<3,>=2.12.0",
            "tzdata>=2025.2",
        }
    )


def test_architecture_scaffolds_are_importable_and_documented() -> None:
    """Expose every planned layer without adding product behavior."""
    for package_name in ARCHITECTURE_PACKAGES:
        module = import_module(f"telegram_assist_bot.{package_name}")
        assert module.__doc__
