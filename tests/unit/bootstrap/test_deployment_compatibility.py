"""Tests for the Linux kernel and MongoDB deployment policy."""

from __future__ import annotations

import pytest

from telegram_assist_bot.bootstrap.cli import main
from telegram_assist_bot.bootstrap.deployment_compatibility import (
    DEFAULT_MONGODB_IMAGE,
    DeploymentCompatibilityError,
    NumericVersion,
    evaluate_mongodb_compatibility,
    parse_kernel_version,
    parse_mongodb_image_version,
    require_mongodb_compatibility,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6.18.12-generic", NumericVersion(6, 18, 12)),
        ("6.19.0", NumericVersion(6, 19, 0)),
        ("7.0", NumericVersion(7, 0, 0)),
    ],
)
def test_kernel_numeric_prefix_is_parsed(
    value: str,
    expected: NumericVersion,
) -> None:
    assert parse_kernel_version(value) == expected


@pytest.mark.parametrize("value", ["", "Linux 6.19", "6", "latest"])
def test_invalid_kernel_version_is_rejected(value: str) -> None:
    with pytest.raises(DeploymentCompatibilityError, match=r"MAJOR\.MINOR"):
        parse_kernel_version(value)


@pytest.mark.parametrize(
    "image",
    ["mongo:7", "mongo:7.0", "mongo:latest", "mongo", "mongo@sha256:abc"],
)
def test_floating_or_unparseable_mongodb_image_is_rejected(image: str) -> None:
    with pytest.raises(
        DeploymentCompatibilityError,
        match=r"MAJOR\.MINOR\.PATCH",
    ):
        parse_mongodb_image_version(image)


def test_kernel_618_allows_explicit_mongodb_8_selection() -> None:
    decision = evaluate_mongodb_compatibility(
        kernel_release="6.18.9-generic",
        mongodb_image="mongo:8.0.21",
    )
    assert decision.compatible is True


def test_kernel_619_blocks_mongodb_8_before_startup() -> None:
    decision = evaluate_mongodb_compatibility(
        kernel_release="6.19.0-generic",
        mongodb_image="mongo:8.0.21",
    )
    assert decision.compatible is False
    with pytest.raises(DeploymentCompatibilityError) as captured:
        require_mongodb_compatibility(
            kernel_release="6.19.0-generic",
            mongodb_image="mongo:8.0.21",
        )
    rendered = str(captured.value)
    assert "detected_kernel=6.19.0" in rendered
    assert "selected_mongodb_image=mongo:8.0.21" in rendered
    assert "compatibility_decision=blocked" in rendered


def test_kernel_619_allows_tested_production_pin() -> None:
    report = require_mongodb_compatibility(
        kernel_release="6.19.2-generic",
        mongodb_image=DEFAULT_MONGODB_IMAGE,
    )
    assert "selected_mongodb_image=mongo:7.0.32" in report
    assert "compatibility_decision=compatible" in report


def test_deployment_preflight_cli_reports_selected_pair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "deployment-preflight",
                "--kernel-version",
                "6.19.0-generic",
                "--mongodb-image",
                "mongo:7.0.32",
            ],
            environ={},
        )
        == 0
    )
    report = capsys.readouterr().out
    assert "detected_kernel=6.19.0" in report
    assert "selected_mongodb_image=mongo:7.0.32" in report


def test_deployment_preflight_cli_rejects_unsafe_pair() -> None:
    assert (
        main(
            [
                "deployment-preflight",
                "--kernel-version",
                "6.19.0-generic",
                "--mongodb-image",
                "mongo:8.0.21",
            ],
            environ={},
        )
        == 2
    )
