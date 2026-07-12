"""Composition-root wiring for the explicit Telegram login command."""

from __future__ import annotations

import asyncio
import getpass
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from telegram_assist_bot.application import (
    AuthenticateTelegramSession,
    AuthenticationOutcome,
    TelegramLoginInput,
)
from telegram_assist_bot.application.ports import TelegramGatewayError
from telegram_assist_bot.bootstrap.runtime import FoundationExitCode
from telegram_assist_bot.infrastructure.telegram.user import TelethonSessionAdapter
from telegram_assist_bot.shared.config import (
    ConfigurationError,
    LogLevel,
    load_configuration,
)
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    Redactor,
    StructuredLogger,
    bind_log_context,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram_assist_bot.shared.observability import EventSink


class SecretReader(Protocol):
    """Read one secret from a terminal without retaining it."""

    def __call__(self, prompt: str, /) -> str:
        """Return the secret entered for a safe fixed prompt."""
        ...


@dataclass(frozen=True, slots=True)
class TerminalTelegramLoginInput(TelegramLoginInput):
    """Read one-time authentication secrets from a non-echoing terminal."""

    reader: SecretReader = field(default=getpass.getpass, repr=False)

    async def read_verification_code(self) -> str:
        """Read a verification code only during the explicit login command."""
        return await asyncio.to_thread(self.reader, "Telegram verification code: ")

    async def read_two_factor_password(self) -> str:
        """Read a 2FA password only when Telegram explicitly requests it."""
        return await asyncio.to_thread(self.reader, "Telegram 2FA password: ")


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def run_telegram_login(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    login_input: TelegramLoginInput | None = None,
) -> FoundationExitCode:
    """Run opt-in Telegram authentication without opening MongoDB."""
    context = CorrelationContext(correlation_id=uuid4().hex)
    try:
        loaded = load_configuration(configuration_path, environ=environ)
    except ConfigurationError:
        return FoundationExitCode.CONFIGURATION_ERROR

    settings = loaded.settings.telegram.user
    secret_values = tuple(
        loaded.secrets.get(reference).get_secret_value()
        for reference in (settings.api_id, settings.api_hash, settings.phone_number)
    )
    logger = StructuredLogger(
        sink=sink,
        clock=_utc_now,
        redactor=Redactor(secret_values=secret_values),
        minimum_level=LogLevel.DEBUG,
    )
    gateway = TelethonSessionAdapter(
        session_path=settings.session_path,
        runtime_root=Path("var/sessions"),
        api_id=int(secret_values[0]),
        api_hash=secret_values[1],
        timeout_seconds=float(
            loaded.settings.telegram.ingestion.operation_timeout_seconds
        ),
    )
    with bind_log_context(context):
        logger.emit(level=LogLevel.INFO, event_name="telegram_login_begun")
        try:
            result = await AuthenticateTelegramSession(gateway).execute(
                phone_number=secret_values[2],
                login_input=login_input or TerminalTelegramLoginInput(),
            )
        except asyncio.CancelledError:
            await gateway.close()
            raise
        except TelegramGatewayError as error:
            logger.emit(
                level=LogLevel.ERROR,
                event_name="telegram_login_failed",
                error=error,
            )
            await gateway.close()
            return FoundationExitCode.INFRASTRUCTURE_ERROR
        await gateway.close()
        logger.emit(
            level=LogLevel.INFO,
            event_name="telegram_login_succeeded",
            fields={
                "session_reused": result.outcome is AuthenticationOutcome.SESSION_REUSED
            },
        )
    return FoundationExitCode.SUCCESS


__all__ = ("TerminalTelegramLoginInput", "run_telegram_login")
