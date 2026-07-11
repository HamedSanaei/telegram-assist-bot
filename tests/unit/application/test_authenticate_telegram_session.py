from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import (
    AuthenticateTelegramSession,
    AuthenticationOutcome,
)
from telegram_assist_bot.application.ports import (
    TelegramLoginStep,
    TelegramSessionInvalidError,
    TelegramSessionStatus,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class FakeGateway:
    status: TelegramSessionStatus
    login_step: TelegramLoginStep = TelegramLoginStep.AUTHORIZED
    calls: list[str] = field(default_factory=list)

    async def inspect_session(self) -> TelegramSessionStatus:
        self.calls.append("inspect")
        return self.status

    async def begin_login(self, phone_number: str) -> None:
        del phone_number
        self.calls.append("begin")

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        del code
        self.calls.append("code")
        return self.login_step

    async def submit_two_factor_password(self, password: str) -> None:
        del password
        self.calls.append("password")

    async def abort_login(self) -> None:
        self.calls.append("abort")

    async def close(self) -> None:
        self.calls.append("close")


@dataclass
class FakeInput:
    code: str = "synthetic-code"
    password: str = field(default_factory=lambda: "synthetic-two-factor-value")
    calls: list[str] = field(default_factory=list)

    async def read_verification_code(self) -> str:
        self.calls.append("code")
        return self.code

    async def read_two_factor_password(self) -> str:
        self.calls.append("password")
        return self.password


def test_authenticates_without_two_factor() -> None:
    gateway = FakeGateway(TelegramSessionStatus.MISSING)
    login_input = FakeInput()

    result = run(
        AuthenticateTelegramSession(gateway).execute(
            phone_number="synthetic-phone",
            login_input=login_input,
        )
    )

    assert result.outcome is AuthenticationOutcome.SESSION_CREATED
    assert gateway.calls == ["inspect", "begin", "code", "abort"]
    assert login_input.calls == ["code"]


def test_authenticates_with_two_factor() -> None:
    gateway = FakeGateway(
        TelegramSessionStatus.MISSING,
        TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED,
    )
    login_input = FakeInput()

    result = run(
        AuthenticateTelegramSession(gateway).execute(
            phone_number="synthetic-phone",
            login_input=login_input,
        )
    )

    assert result.outcome is AuthenticationOutcome.SESSION_CREATED
    assert gateway.calls == ["inspect", "begin", "code", "password", "abort"]
    assert login_input.calls == ["code", "password"]


def test_reuses_authorized_session_without_prompting() -> None:
    gateway = FakeGateway(TelegramSessionStatus.AUTHORIZED)
    login_input = FakeInput()

    result = run(
        AuthenticateTelegramSession(gateway).execute(
            phone_number="synthetic-phone",
            login_input=login_input,
        )
    )

    assert result.outcome is AuthenticationOutcome.SESSION_REUSED
    assert gateway.calls == ["inspect"]
    assert login_input.calls == []


def test_invalid_session_requires_explicit_reauthentication() -> None:
    gateway = FakeGateway(TelegramSessionStatus.INVALID)

    with pytest.raises(TelegramSessionInvalidError) as captured:
        run(
            AuthenticateTelegramSession(gateway).execute(
                phone_number="synthetic-phone",
                login_input=FakeInput(),
            )
        )

    assert "synthetic-phone" not in str(captured.value)
    assert gateway.calls == ["inspect"]


def test_prompt_failure_always_releases_pending_login() -> None:
    gateway = FakeGateway(TelegramSessionStatus.MISSING)
    login_input = FakeInput(code="")

    with pytest.raises(ValueError, match="verification code"):
        run(
            AuthenticateTelegramSession(gateway).execute(
                phone_number="synthetic-phone",
                login_input=login_input,
            )
        )

    assert gateway.calls == ["inspect", "begin", "abort"]
