"""Strict distribution membership tests."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from scripts.check_distribution import (
    EXPECTED_IMPORT_PACKAGE,
    DistributionValidationError,
    validate_wheel,
)


def test_distribution_checker_rejects_an_unexpected_top_level_member(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "unexpected.whl"
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{EXPECTED_IMPORT_PACKAGE}/__init__.py", "")
        archive.writestr("unexpected_payload/config.json", "{}")
        archive.writestr("package-1.1.2.dist-info/METADATA", "")
        archive.writestr("package-1.1.2.dist-info/WHEEL", "")
        archive.writestr("package-1.1.2.dist-info/RECORD", "")

    with pytest.raises(
        DistributionValidationError,
        match="unexpected wheel top-level entries",
    ):
        validate_wheel(wheel)


def test_portable_url_fallback_has_one_exact_distribution_entry() -> None:
    distribution = (
        Path(__file__).parents[3] / "scripts" / "check_distribution.py"
    ).read_text(encoding="utf-8")

    assert distribution.count("infrastructure/telegram/url_button_fallback.py") == 1
