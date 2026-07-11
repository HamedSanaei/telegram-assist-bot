from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import AuthenticateTelegramSession
from telegram_assist_bot.application.ports import (
    TelegramAuthenticationGateway,
    TelegramLoginStep,
    TelegramSessionStatus,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonSessionAdapter

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path

pytestmark = pytest.mark.contract


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class RecordedSession:
    authorized: bool = False
    code_requests: int = 0


@dataclass
class RecordedClient:
    path: Path
    session: RecordedSession

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        if self.session.authorized:
            self.path.write_text("synthetic-session-v1", encoding="utf-8")

    async def is_user_authorized(self) -> bool:
        return self.session.authorized

    async def send_code_request(self, phone: str) -> object:
        del phone
        self.session.code_requests += 1
        return object()

    async def sign_in(
        self,
        *,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
    ) -> object:
        del phone, code, password
        self.session.authorized = True
        return object()


@dataclass
class RecordedInput:
    prompts: int = 0

    async def read_verification_code(self) -> str:
        self.prompts += 1
        return "synthetic-code"

    async def read_two_factor_password(self) -> str:
        self.prompts += 1
        return "synthetic-two-factor"


def test_adapter_satisfies_owned_contract_across_two_lifecycles(
    tmp_path: Path,
) -> None:
    session = RecordedSession()

    def factory(
        path: Path,
        api_id: int,
        api_hash: str,
        timeout: float,
    ) -> RecordedClient:
        del api_id, api_hash, timeout
        return RecordedClient(path, session)

    def create_gateway() -> TelethonSessionAdapter:
        return TelethonSessionAdapter(
            session_path=tmp_path / "account.session",
            runtime_root=tmp_path,
            api_id=123456,
            api_hash="synthetic-api-hash",
            timeout_seconds=1.0,
            client_factory=factory,
        )

    first = create_gateway()
    first_input = RecordedInput()
    run(
        AuthenticateTelegramSession(first).execute(
            phone_number="synthetic-phone",
            login_input=first_input,
        )
    )
    run(first.close())

    second = create_gateway()
    second_input = RecordedInput()
    status = run(second.inspect_session())
    run(
        AuthenticateTelegramSession(second).execute(
            phone_number="synthetic-phone",
            login_input=second_input,
        )
    )
    run(second.close())

    assert isinstance(first, TelegramAuthenticationGateway)
    assert status is TelegramSessionStatus.AUTHORIZED
    assert first_input.prompts == 1
    assert second_input.prompts == 0
    assert session.code_requests == 1
    assert TelegramLoginStep.AUTHORIZED.value == "Authorized"
