"""Single-command entrypoint running the whole main-server stack.

Runs, in one asyncio event loop:
  * the main application (approval bot + queue worker + scheduler), and
  * the collector (Telethon client reading the source channels),

each under a small supervisor that restarts a crashed component without
taking the other one down. The Iran VPN worker is not started here; it
runs on the Iran server as its own process
(``python -m src.workers.iran_vpn_worker``).

Note: on the very first collector run Telethon asks for the phone number
and login code on stdin. Do that first login separately with
``python -m src.workers.collector`` before switching to this entrypoint,
so the interactive prompt does not stall the approval bot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src import main as main_app
from src.shared.config import load_configuration
from src.shared.errors import ConfigurationError
from src.shared.logging_setup import get_logger, setup_logging
from src.workers import collector

logger = get_logger(__name__)

DEFAULT_RESTART_DELAY_SECONDS = 10.0


async def supervise(
    name: str,
    factory: Callable[[], Awaitable[None]],
    restart_delay_seconds: float = DEFAULT_RESTART_DELAY_SECONDS,
) -> None:
    """
    Run one component forever, restarting it whenever it crashes.

    Args:
        name:
            Component name used in log messages.
        factory:
            Zero-argument callable returning the component coroutine,
            e.g. ``src.main.run`` or ``src.workers.collector.run``.
        restart_delay_seconds:
            Pause between a crash (or clean exit) and the restart.

    Raises:
        asyncio.CancelledError:
            Propagated unchanged so shutdown cancels the loop.

    Side effects:
        Logs every crash and restart. A :class:`ConfigurationError` stops
        this component permanently (restarting cannot fix a bad config
        file) while the sibling components keep running.

    Example:
        await supervise("collector", collector.run)
    """
    while True:
        try:
            await factory()
            logger.warning(
                "Component '%s' exited; restarting in %.0fs",
                name,
                restart_delay_seconds,
            )
        except asyncio.CancelledError:
            raise
        except ConfigurationError as exc:
            logger.error(
                "Component '%s' has invalid configuration, not restarting: %s",
                name,
                exc,
            )
            return
        except Exception:
            logger.exception(
                "Component '%s' crashed; restarting in %.0fs",
                name,
                restart_delay_seconds,
            )
        await asyncio.sleep(restart_delay_seconds)


async def run() -> None:
    """
    Start every main-server component in this single process.

    Raises:
        ConfigurationError:
            When the configuration file itself cannot be loaded. Component
            specific configuration problems only stop that component.

    Side effects:
        Configures logging and runs until cancelled (Ctrl+C).
    """
    config = load_configuration()
    setup_logging(config.logging.level, config.logging.file)
    logger.info("Starting all main-server components in one process")
    await asyncio.gather(
        supervise("main-app", main_app.run),
        supervise("collector", collector.run),
    )
    logger.error("All components stopped; exiting")


def main() -> None:
    """Synchronous entrypoint for ``python -m src.run_all``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested; all components stopped")


if __name__ == "__main__":
    main()
