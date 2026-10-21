"""Verify resource sampling on the host without requiring another platform."""

from __future__ import annotations

import math
import sys

from telegram_assist_bot.shared.runtime_metrics import (
    _windows_memory,
    process_resources,
)


def test_resource_samples_are_finite_and_nonnegative() -> None:
    sample = process_resources()
    assert "cpu_seconds" in sample
    assert all(math.isfinite(value) and value >= 0 for value in sample.values())
    if "rss_bytes" in sample:
        assert sample["rss_bytes"] > 0
    if "peak_rss_bytes" in sample:
        assert sample["peak_rss_bytes"] > 0


def test_windows_memory_sampler_is_safe_on_other_platforms() -> None:
    sample = _windows_memory()
    if sys.platform == "win32":
        assert sample["rss_bytes"] > 0
        assert sample["peak_rss_bytes"] >= sample["rss_bytes"]
    else:
        assert sample == {}
