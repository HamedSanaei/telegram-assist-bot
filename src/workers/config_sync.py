"""Runtime configuration hot-reload for channel and admin lists."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from src.composition import sync_config_to_sqlite
from src.infrastructure.db.sqlite.connection import Database
from src.shared.config import CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH, load_configuration
from src.shared.errors import ConfigurationError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class ConfigSyncWorker:
    """
    Watches ``configuration.json`` and mirrors runtime-safe lists to SQLite.

    Runtime-safe lists include source channels, destination channels, admin
    user ids, and recurring-forward campaign definitions. Secrets, API
    clients, database paths, AI provider chains, and Telegram sessions remain
    restart-only.

    Example:
        worker = ConfigSyncWorker(db)
        await worker.run()
    """

    def __init__(
        self,
        db: Database,
        config_path: str | Path | None = None,
        poll_seconds: float = 5.0,
        on_applied: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Args:
            db: Connected SQLite database.
            config_path: Optional explicit config file path. When omitted,
                the same environment/default path as ``load_configuration``
                is used.
            poll_seconds: Delay between mtime checks.
            on_applied: Optional async callback invoked after a valid config
                reload is applied to SQLite.
        """
        self._db = db
        self._path = Path(
            config_path or os.environ.get(CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH)
        )
        self._poll_seconds = max(1.0, poll_seconds)
        self._on_applied = on_applied
        self._last_mtime_ns: int | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Request the polling loop to stop."""
        self._stop_event.set()

    async def run(self) -> None:
        """
        Poll the config file until stopped or cancelled.

        Side effects:
            Applies authoritative channel/admin changes to SQLite whenever
            the config file mtime changes. Invalid config reload attempts are
            logged and ignored so the running bot keeps using the last good
            SQLite state.
        """
        logger.info("Runtime config sync watching path=%s", self._path)
        while not self._stop_event.is_set():
            try:
                await self.sync_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected runtime config sync failure")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def sync_if_changed(self) -> bool:
        """
        Apply a config reload when the file mtime changed.

        Returns:
            ``True`` when a new valid config was applied, otherwise ``False``.
        """
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            logger.error("Runtime config sync skipped; file not found path=%s", self._path)
            return False
        if self._last_mtime_ns == stat.st_mtime_ns:
            return False
        try:
            config = load_configuration(self._path)
        except ConfigurationError as exc:
            logger.error(
                "Runtime config reload ignored path=%s error=%s",
                self._path,
                exc,
            )
            return False
        await sync_config_to_sqlite(config, self._db)
        self._last_mtime_ns = stat.st_mtime_ns
        if self._on_applied is not None:
            try:
                await self._on_applied()
            except Exception:
                logger.exception("Runtime config reload callback failed")
        logger.info("Runtime config reload applied path=%s", self._path)
        return True
