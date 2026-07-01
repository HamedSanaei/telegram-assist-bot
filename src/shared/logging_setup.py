"""UTF-8-safe structured logging setup.

All log files are opened with explicit UTF-8 encoding so Persian text
is never turned into Mojibake. Console output is also forced to UTF-8
where the stream supports reconfiguration (Windows terminals).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """
    Configure root logging with UTF-8-safe handlers.

    Args:
        level:
            Logging level name such as ``"INFO"`` or ``"DEBUG"``.
        log_file:
            Optional path of the log file. Parent directories are created
            automatically. When omitted, only console logging is enabled.

    Side effects:
        Replaces handlers on the root logger and reconfigures ``sys.stdout``
        to UTF-8 when possible.

    Example:
        setup_logging("INFO", "logs/app.log")
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=_FORMAT,
        handlers=handlers,
        force=True,
    )


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
