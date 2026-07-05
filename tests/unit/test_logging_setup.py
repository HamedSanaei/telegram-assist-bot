"""Unit tests for UTF-8-safe colored logging helpers."""

from __future__ import annotations

import logging
import re

from src.shared.logging_setup import ColorFormatter, setup_logging


def _record(level: int, message: str, event_kind: str = "") -> logging.LogRecord:
    """Build a log record for formatter tests."""
    record = logging.LogRecord("test", level, __file__, 1, message, (), None)
    if event_kind:
        record.event_kind = event_kind
    return record


class TestColorFormatter:
    """Tests for console color selection."""

    def test_ai_success_is_green(self) -> None:
        formatter = ColorFormatter("%(message)s")
        text = formatter.format(_record(logging.INFO, "ai ok", "ai_success"))
        assert text.startswith("\033[32m")
        assert text.endswith("\033[0m")

    def test_ai_error_is_orange(self) -> None:
        formatter = ColorFormatter("%(message)s")
        text = formatter.format(_record(logging.WARNING, "ai failed", "ai_error"))
        assert text.startswith("\033[33m")
        assert text.endswith("\033[0m")

    def test_error_is_red(self) -> None:
        formatter = ColorFormatter("%(message)s")
        text = formatter.format(_record(logging.ERROR, "boom"))
        assert text.startswith("\033[31m")
        assert text.endswith("\033[0m")


class TestSetupLogging:
    """Tests for process-run log file creation."""

    def test_creates_plain_utf8_timestamped_run_log(self, tmp_path) -> None:
        stable = tmp_path / "app.log"
        setup_logging(
            "INFO",
            str(stable),
            color_console=True,
            entrypoint_name="unit",
        )
        logging.getLogger("test").info(
            "سلام", extra={"event_kind": "ai_success"}
        )
        for handler in logging.getLogger().handlers:
            handler.flush()

        run_logs = list(tmp_path.glob("*-unit.log"))
        assert stable.exists()
        assert len(run_logs) == 1
        content = run_logs[0].read_text(encoding="utf-8")
        assert "سلام" in content
        assert not re.search(r"\x1b\[[0-9;]*m", content)
