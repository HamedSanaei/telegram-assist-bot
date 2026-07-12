from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import telegram_assist_bot.bootstrap.telegram_login as login_module
from telegram_assist_bot.application.ports import (
    TelegramLoginStep,
    TelegramSessionStatus,
)
from telegram_assist_bot.bootstrap.runtime import FoundationExitCode
from telegram_assist_bot.bootstrap.telegram_login import (
    TerminalTelegramLoginInput,
    run_telegram_login,
)
from telegram_assist_bot.shared.config import ConfigurationError
from tests.unit.test_text_ingestion_bootstrap import loaded_configuration

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.shared.observability import RedactedValue


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class Sink:
    events: list[Mapping[str, RedactedValue]] = field(default_factory=list)

    def __call__(self, event: Mapping[str, RedactedValue]) -> None:
        self.events.append(event)


@dataclass
class Gateway:
    status: TelegramSessionStatus
    step: TelegramLoginStep = TelegramLoginStep.AUTHORIZED
    calls: list[str] = field(default_factory=list)
    close_calls: int = 0

    async def inspect_session(self) -> TelegramSessionStatus:
        self.calls.append("inspect")
        return self.status

    async def begin_login(self, phone_number: str) -> None:
        del phone_number
        self.calls.append("begin")

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        del code
        self.calls.append("code")
        return self.step

    async def submit_two_factor_password(self, password: str) -> None:
        del password
        self.calls.append("password")

    async def abort_login(self) -> None:
        self.calls.append("abort")

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class Input:
    code: str = "synthetic-code"
    password: str = field(default_factory=lambda: "synthetic-two-factor")

    async def read_verification_code(self) -> str:
        return self.code

    async def read_two_factor_password(self) -> str:
        return self.password


def patch_composition(
    monkeypatch: pytest.MonkeyPatch,
    gateway: Gateway,
) -> None:
    monkeypatch.setattr(
        login_module,
        "load_configuration",
        lambda *_args, **_kwargs: loaded_configuration(),
    )
    monkeypatch.setattr(
        login_module,
        "TelethonSessionAdapter",
        lambda **_kwargs: gateway,
    )


def test_explicit_login_command_reuses_session_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(TelegramSessionStatus.AUTHORIZED)
    patch_composition(monkeypatch, gateway)
    sink = Sink()

    result = run(
        run_telegram_login(
            Path("synthetic.json"),
            environ={},
            sink=sink,
            login_input=Input(code="", password=""),
        )
    )

    assert result is FoundationExitCode.SUCCESS
    assert gateway.calls == ["inspect"]
    assert gateway.close_calls == 1
    assert sink.events[-1]["event_name"] == "telegram_login_succeeded"


def test_login_uses_telegram_timeout_not_mongodb_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = loaded_configuration()
    telegram = loaded.settings.telegram.model_copy(
        update={
            "ingestion": loaded.settings.telegram.ingestion.model_copy(
                update={"operation_timeout_seconds": 30}
            )
        }
    )
    settings = loaded.settings.model_copy(
        update={
            "mongodb": loaded.settings.mongodb.model_copy(
                update={"connect_timeout_seconds": 5}
            ),
            "telegram": telegram,
        }
    )
    gateway = Gateway(TelegramSessionStatus.AUTHORIZED)
    captured: dict[str, object] = {}

    def factory(**kwargs: object) -> Gateway:
        captured.update(kwargs)
        return gateway

    monkeypatch.setattr(
        login_module,
        "load_configuration",
        lambda *_args, **_kwargs: replace(loaded, settings=settings),
    )
    monkeypatch.setattr(login_module, "TelethonSessionAdapter", factory)

    assert (
        run(run_telegram_login(Path("synthetic.json"), environ={}, sink=Sink()))
        is FoundationExitCode.SUCCESS
    )
    assert captured["timeout_seconds"] == 30.0


def test_explicit_login_command_runs_code_and_two_factor_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(
        TelegramSessionStatus.MISSING,
        TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED,
    )
    patch_composition(monkeypatch, gateway)

    result = run(
        run_telegram_login(
            Path("synthetic.json"),
            environ={},
            sink=Sink(),
            login_input=Input(),
        )
    )

    assert result is FoundationExitCode.SUCCESS
    assert gateway.calls == ["inspect", "begin", "code", "password", "abort"]


def test_unauthorized_session_runs_explicit_reauthentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(TelegramSessionStatus.INVALID)
    patch_composition(monkeypatch, gateway)
    sink = Sink()

    result = run(
        run_telegram_login(
            Path("synthetic.json"),
            environ={},
            sink=sink,
            login_input=Input(),
        )
    )

    assert result is FoundationExitCode.SUCCESS
    assert gateway.close_calls == 1
    assert gateway.calls == ["inspect", "begin", "code", "abort"]
    assert sink.events[-1]["event_name"] == "telegram_login_succeeded"


def test_configuration_failure_creates_no_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise ConfigurationError

    monkeypatch.setattr(login_module, "load_configuration", fail)

    result = run(
        run_telegram_login(
            Path("missing.json"),
            environ={},
            sink=Sink(),
        )
    )

    assert result is FoundationExitCode.CONFIGURATION_ERROR


def test_login_cancellation_closes_gateway_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(TelegramSessionStatus.MISSING)
    patch_composition(monkeypatch, gateway)

    class CancelledAuthentication:
        def __init__(self, _gateway: object) -> None:
            pass

        async def execute(self, **_kwargs: object) -> None:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        login_module,
        "AuthenticateTelegramSession",
        CancelledAuthentication,
    )

    with pytest.raises(asyncio.CancelledError):
        run(
            run_telegram_login(
                Path("synthetic.json"),
                environ={},
                sink=Sink(),
                login_input=Input(),
            )
        )

    assert gateway.close_calls == 1


def test_terminal_input_uses_injected_non_echoing_reader() -> None:
    prompts: list[str] = []
    values = iter(("code-value", "two-factor-value"))

    def reader(prompt: str) -> str:
        prompts.append(prompt)
        return next(values)

    login_input = TerminalTelegramLoginInput(reader)

    assert run(login_input.read_verification_code()) == "code-value"
    assert run(login_input.read_two_factor_password()) == "two-factor-value"
    assert prompts == ["Telegram verification code: ", "Telegram 2FA password: "]
