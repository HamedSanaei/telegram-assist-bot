"""Strict distribution membership tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from scripts.check_distribution import (
    EXPECTED_IMPORT_PACKAGE,
    DistributionValidationError,
    validate_wheel,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_distribution_checker_rejects_an_unexpected_top_level_member(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "unexpected.whl"
    with ZipFile(wheel, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(f"{EXPECTED_IMPORT_PACKAGE}/__init__.py", "")
        archive.writestr("unexpected_payload/config.json", "{}")
        archive.writestr("package-1.1.0.dist-info/METADATA", "")
        archive.writestr("package-1.1.0.dist-info/WHEEL", "")
        archive.writestr("package-1.1.0.dist-info/RECORD", "")

    with pytest.raises(
        DistributionValidationError,
        match="unexpected wheel top-level entries",
    ):
        validate_wheel(wheel)
