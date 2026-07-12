from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from telethon.errors import (  # type: ignore[import-untyped]
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from telegram_assist_bot.application.ports import (
    TelegramInvalidCodeError,
    TelegramInvalidPasswordError,
    TelegramLoginStep,
    TelegramOperationTimeoutError,
    TelegramRateLimitError,
    TelegramSessionInvalidError,
    TelegramSessionMutationConflictError,
    TelegramSessionStatus,
    TelegramTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user.session_adapter import (
    TelethonSessionAdapter,
    create_telethon_client,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class SessionState:
    authorized: bool = False
    require_password: bool = False
    connect_error: Exception | None = None
    send_error: Exception | None = None
    code_error: Exception | None = None
    password_error: Exception | None = None


@dataclass
class FakeClient:
    path: Path
    state: SessionState
    calls: list[str] = field(default_factory=list)

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.state.connect_error is not None:
            raise self.state.connect_error

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        if self.state.authorized:
            self.path.write_text("synthetic-authorized-session", encoding="utf-8")

    async def is_user_authorized(self) -> bool:
        self.calls.append("authorized")
        return self.state.authorized

    async def send_code_request(self, phone: str) -> object:
        del phone
        self.calls.append("send_code")
        if self.state.send_error is not None:
            raise self.state.send_error
        return object()

    async def sign_in(
        self,
        *,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
    ) -> object:
        del phone, code
        selected_error = (
            self.state.password_error if password is not None else self.state.code_error
        )
        if selected_error is not None:
            raise selected_error
        if password is None and self.state.require_password:
            self.calls.append("code_requires_password")
            raise SessionPasswordNeededError(None)
        self.calls.append("password" if password is not None else "code")
        self.state.authorized = True
        return object()


@dataclass
class FakeFactory:
    state: SessionState
    clients: list[FakeClient] = field(default_factory=list)

    def __call__(
        self,
        path: Path,
        api_id: int,
        api_hash: str,
        timeout: float,
    ) -> FakeClient:
        del api_id, api_hash, timeout
        client = FakeClient(path, self.state)
        self.clients.append(client)
        return client


def adapter(
    tmp_path: Path,
    factory: FakeFactory,
    **overrides: object,
) -> TelethonSessionAdapter:
    values: dict[str, object] = {
        "session_path": tmp_path / "source.session",
        "runtime_root": tmp_path,
        "api_id": 123456,
        "api_hash": "synthetic-api-hash",
        "timeout_seconds": 1.0,
        "client_factory": factory,
    }
    values.update(overrides)
    return TelethonSessionAdapter(**values)  # type: ignore[arg-type]


def test_missing_session_does_not_create_client(tmp_path: Path) -> None:
    factory = FakeFactory(SessionState())

    result = run(adapter(tmp_path, factory).inspect_session())

    assert result is TelegramSessionStatus.MISSING
    assert factory.clients == []


def test_concrete_client_factory_is_inert_and_uses_bounded_settings(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        client = create_telethon_client(
            tmp_path / "factory.session",
            123456,
            "synthetic-api-hash",
            1.0,
        )
        await client.disconnect()

    run(scenario())


def test_existing_unauthorized_session_reports_invalid_status(tmp_path: Path) -> None:
    (tmp_path / "source.session").write_text("synthetic", encoding="utf-8")

    result = run(adapter(tmp_path, FakeFactory(SessionState())).inspect_session())

    assert result is TelegramSessionStatus.INVALID


def test_unauthorized_partial_session_is_discarded_before_explicit_login(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "source.session"
    session_path.write_text("synthetic-partial-session", encoding="utf-8")
    factory = FakeFactory(SessionState())
    gateway = adapter(tmp_path, factory)

    assert run(gateway.inspect_session()) is TelegramSessionStatus.INVALID
    run(gateway.begin_login("synthetic-phone"))

    assert not session_path.exists()
    assert factory.clients[-1].calls == ["connect", "send_code"]
    run(gateway.abort_login())


def test_transient_first_login_failure_can_be_retried_with_partial_session(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "source.session"
    state = SessionState(send_error=ConnectionError("offline"))
    first_gateway = adapter(tmp_path, FakeFactory(state))

    with pytest.raises(TelegramTransientError):
        run(first_gateway.begin_login("synthetic-phone"))

    session_path.write_text("synthetic-partial-session", encoding="utf-8")
    state.send_error = None
    retry_factory = FakeFactory(state)
    retry_gateway = adapter(tmp_path, retry_factory)

    assert run(retry_gateway.inspect_session()) is TelegramSessionStatus.INVALID
    run(retry_gateway.begin_login("synthetic-phone"))

    assert retry_factory.clients[-1].calls == ["connect", "send_code"]
    run(retry_gateway.abort_login())


def test_create_then_reuse_synthetic_session(tmp_path: Path) -> None:
    state = SessionState()
    factory = FakeFactory(state)
    gateway = adapter(tmp_path, factory)

    run(gateway.begin_login("synthetic-phone"))
    result = run(gateway.submit_login_code("synthetic-code"))

    assert result is TelegramLoginStep.AUTHORIZED
    assert gateway.session_path.read_text(encoding="utf-8") == (
        "synthetic-authorized-session"
    )
    assert run(gateway.inspect_session()) is TelegramSessionStatus.AUTHORIZED
    assert factory.clients[-1].calls == ["connect", "authorized", "disconnect"]


def test_two_factor_flow_retains_lock_until_password(tmp_path: Path) -> None:
    state = SessionState(require_password=True)
    gateway = adapter(tmp_path, FakeFactory(state))

    run(gateway.begin_login("synthetic-phone"))
    next_step = run(gateway.submit_login_code("synthetic-code"))
    run(gateway.submit_two_factor_password("synthetic-password"))

    assert next_step is TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED
    assert gateway.session_path.exists()


def test_network_failure_preserves_existing_session_bytes(tmp_path: Path) -> None:
    session_path = tmp_path / "source.session"
    session_path.write_text("existing-synthetic-session", encoding="utf-8")
    gateway = adapter(
        tmp_path,
        FakeFactory(SessionState(connect_error=ConnectionError("offline"))),
    )

    with pytest.raises(TelegramTransientError):
        run(gateway.inspect_session())

    assert session_path.read_text(encoding="utf-8") == "existing-synthetic-session"


def test_open_authorized_client_holds_lock_until_idempotent_close(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "source.session"
    session_path.write_text("synthetic", encoding="utf-8")
    state = SessionState(authorized=True)
    gateway = adapter(tmp_path, FakeFactory(state))

    client = run(gateway.open_authorized_client())

    assert gateway.connected_client is client
    run(gateway.close())
    run(gateway.close())
    with pytest.raises(TelegramSessionInvalidError):
        _ = gateway.connected_client


def test_open_unauthorized_client_releases_lock_and_reports_invalid(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.session").write_text("synthetic", encoding="utf-8")
    gateway = adapter(tmp_path, FakeFactory(SessionState()))

    with pytest.raises(TelegramSessionInvalidError):
        run(gateway.open_authorized_client())

    run(gateway.begin_login("synthetic-phone"))
    run(gateway.abort_login())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError(), TelegramOperationTimeoutError),
        (FloodWaitError(None, capture=5), TelegramRateLimitError),
    ],
)
def test_begin_login_maps_bounded_external_failures_and_releases_lock(
    tmp_path: Path,
    failure: Exception,
    expected: type[Exception],
) -> None:
    state = SessionState(
        connect_error=failure if isinstance(failure, TimeoutError) else None,
        send_error=failure if not isinstance(failure, TimeoutError) else None,
    )
    gateway = adapter(tmp_path, FakeFactory(state))

    with pytest.raises(expected):
        run(gateway.begin_login("synthetic-phone"))

    run(gateway.abort_login())


def test_invalid_code_and_password_are_mapped_without_secret_values(
    tmp_path: Path,
) -> None:
    code_gateway = adapter(
        tmp_path,
        FakeFactory(SessionState(code_error=PhoneCodeInvalidError(None))),
    )
    run(code_gateway.begin_login("synthetic-phone"))
    with pytest.raises(TelegramInvalidCodeError):
        run(code_gateway.submit_login_code("synthetic-code"))
    run(code_gateway.abort_login())

    password_gateway = adapter(
        tmp_path,
        FakeFactory(
            SessionState(
                require_password=True,
                password_error=PasswordHashInvalidError(None),
            )
        ),
    )
    run(password_gateway.begin_login("synthetic-phone"))
    assert run(password_gateway.submit_login_code("synthetic-code")) is (
        TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED
    )
    with pytest.raises(TelegramInvalidPasswordError):
        run(password_gateway.submit_two_factor_password("synthetic-two-factor"))
    run(password_gateway.abort_login())


def test_concurrent_session_mutation_returns_bounded_conflict(tmp_path: Path) -> None:
    first = adapter(tmp_path, FakeFactory(SessionState()))
    ticks = iter((0.0, 0.0, 1.0))

    async def no_sleep(_seconds: float) -> None:
        return None

    second = adapter(
        tmp_path,
        FakeFactory(SessionState()),
        lock_timeout_seconds=0.1,
        monotonic_clock=lambda: next(ticks),
        sleeper=no_sleep,
    )

    run(first.begin_login("synthetic-phone"))
    try:
        with pytest.raises(TelegramSessionMutationConflictError):
            run(second.begin_login("synthetic-phone"))
    finally:
        run(first.abort_login())


def test_rejects_session_path_outside_approved_runtime_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="approved runtime root"):
        TelethonSessionAdapter(
            session_path=tmp_path.parent / "outside.session",
            runtime_root=tmp_path,
            api_id=123456,
            api_hash="synthetic-api-hash",
            timeout_seconds=1.0,
            client_factory=FakeFactory(SessionState()),
        )
