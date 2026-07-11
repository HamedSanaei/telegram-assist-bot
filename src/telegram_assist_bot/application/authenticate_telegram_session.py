"""Explicit application workflow for Telegram session authentication."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from telegram_assist_bot.application.ports.telegram_source_gateway import (
    TelegramAuthenticationGateway,
    TelegramLoginStep,
    TelegramSessionInvalidError,
    TelegramSessionStatus,
)


class TelegramLoginInput(Protocol):
    """Read login secrets only from an explicit interactive boundary."""

    async def read_verification_code(self) -> str:
        """Read the one-time Telegram verification code without echoing it."""
        ...

    async def read_two_factor_password(self) -> str:
        """Read the Telegram two-factor password without echoing it."""
        ...


class AuthenticationOutcome(StrEnum):
    """Describe whether authentication reused or created authorization."""

    SESSION_REUSED = "SessionReused"
    SESSION_CREATED = "SessionCreated"


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Return a non-sensitive result from explicit authentication."""

    outcome: AuthenticationOutcome


@dataclass(frozen=True, slots=True)
class AuthenticateTelegramSession:
    """Coordinate first login and subsequent non-interactive session reuse."""

    gateway: TelegramAuthenticationGateway = field(repr=False)

    async def execute(
        self,
        *,
        phone_number: str,
        login_input: TelegramLoginInput,
    ) -> AuthenticationResult:
        """Authenticate once, prompting only when no session exists."""
        if type(phone_number) is not str or not phone_number or phone_number.isspace():
            raise ValueError("phone_number must be a non-blank string")

        status = await self.gateway.inspect_session()
        if status is TelegramSessionStatus.AUTHORIZED:
            return AuthenticationResult(AuthenticationOutcome.SESSION_REUSED)
        if status is TelegramSessionStatus.INVALID:
            raise TelegramSessionInvalidError

        await self.gateway.begin_login(phone_number)
        try:
            code = await login_input.read_verification_code()
            self._require_secret(code, name="verification code")
            next_step = await self.gateway.submit_login_code(code)
            if next_step is TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED:
                password = await login_input.read_two_factor_password()
                self._require_secret(password, name="two-factor password")
                await self.gateway.submit_two_factor_password(password)
            return AuthenticationResult(AuthenticationOutcome.SESSION_CREATED)
        finally:
            await self.gateway.abort_login()

    @staticmethod
    def _require_secret(value: str, *, name: str) -> None:
        """Reject unusable prompt values without retaining their contents."""
        if type(value) is not str or not value or value.isspace():
            raise ValueError(f"{name} must be a non-blank string")


__all__ = (
    "AuthenticateTelegramSession",
    "AuthenticationOutcome",
    "AuthenticationResult",
    "TelegramLoginInput",
)
