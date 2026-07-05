"""UTF-8-safe structured logging setup.

All log files are opened with explicit UTF-8 encoding so Persian text
is never turned into Mojibake. Console output is also forced to UTF-8
where the stream supports reconfiguration (Windows terminals).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_RESET = "\033[0m"
_GREEN = "\033[32m"
_ORANGE = "\033[33m"
_RED = "\033[31m"


class ColorFormatter(logging.Formatter):
    """
    Console formatter that highlights important operational events.

    AI success/use events can pass ``extra={"event_kind": "ai_success"}``
    and AI fallback/error events can pass ``extra={"event_kind": "ai_error"}``.
    Error-level records are always red. The formatter is intentionally used
    only for console handlers so UTF-8 log files stay plain text.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return a formatted log line wrapped in ANSI color when needed."""
        message = super().format(record)
        color = self._color_for(record)
        if not color:
            return message
        return f"{color}{message}{_RESET}"

    @staticmethod
    def _color_for(record: logging.LogRecord) -> str:
        """Return the ANSI color for a record, or an empty string."""
        if record.levelno >= logging.ERROR:
            return _RED
        event_kind = getattr(record, "event_kind", "")
        if event_kind == "ai_success":
            return _GREEN
        if event_kind == "ai_error":
            return _ORANGE
        return ""


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    color_console: bool = True,
    entrypoint_name: str = "app",
) -> None:
    """
    Configure root logging with UTF-8-safe handlers.

    Args:
        level:
            Logging level name such as ``"INFO"`` or ``"DEBUG"``.
        log_file:
            Optional path of the log file. Parent directories are created
            automatically. When omitted, only console logging is enabled.
        color_console:
            Whether console output should use ANSI colors for important
            records. File logs are never colorized.
        entrypoint_name:
            Short process name used in the timestamped per-run log file.

    Side effects:
        Replaces handlers on the root logger and reconfigures ``sys.stdout``
        to UTF-8 when possible.

    Example:
        setup_logging("INFO", "logs/app.log", entrypoint_name="main")
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        ColorFormatter(_FORMAT) if color_console else logging.Formatter(_FORMAT)
    )
    handlers: list[logging.Handler] = [console]
    file_paths: list[Path] = []
    if log_file:
        path = Path(log_file)
        file_paths.append(path)
    run_path = _run_log_path(log_file, entrypoint_name)
    if run_path not in file_paths:
        file_paths.append(run_path)
    for path in file_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )


def _run_log_path(log_file: str | None, entrypoint_name: str) -> Path:
    """
    Build the timestamped log path for this process run.

    Args:
        log_file: Optional stable configured log path. Its parent directory is
            reused for the per-run file when present.
        entrypoint_name: Process name suffix.

    Returns:
        A path like ``logs/20260703-112233-main.log``.
    """
    base_dir = Path(log_file).parent if log_file else Path("logs")
    safe_name = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in entrypoint_name
    ).strip("_") or "app"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return base_dir / f"{stamp}-{safe_name}.log"


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Args:
        name:
            Logger name, normally the module ``__name__``.

    Returns:
        A standard :class:`logging.Logger` instance.
    """
    return logging.getLogger(name)
