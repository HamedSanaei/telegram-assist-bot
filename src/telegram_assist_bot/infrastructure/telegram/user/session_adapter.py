"""Telethon adapter for bounded, exclusive user-session authentication."""

from __future__ import annotations

import asyncio
import importlib
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Protocol, TextIO, cast

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon import utils as telethon_utils
from telethon.errors import (  # type: ignore[import-untyped]
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    RPCError,
    SessionPasswordNeededError,
    UnauthorizedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserNotParticipantError,
)

from telegram_assist_bot.application.ports import (
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelNotFoundError,
    TelegramChannelPermissionError,
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramGatewayError,
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

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType


class TelethonClientProtocol(Protocol):
    """Describe only the Telethon client surface required by authentication."""

    async def connect(self) -> None:
        """Connect without automatically prompting for authentication."""
        ...

    async def disconnect(self) -> None:
        """Disconnect and flush the SDK-owned session."""
        ...

    async def is_user_authorized(self) -> bool:
        """Return whether the current session is authorized."""
        ...

    async def send_code_request(self, phone: str) -> object:
        """Request a verification code for a private phone value."""
        ...

    async def sign_in(
        self,
        *,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
    ) -> object:
        """Complete one code or password authentication step."""
        ...


class TelethonValidationClientProtocol(TelethonClientProtocol, Protocol):
    """Describe the additional non-interactive validation client surface."""

    async def get_me(self) -> object | None:
        """Return the authorized account entity or no account."""
        ...

    async def get_entity(self, entity: str | int) -> object:
        """Resolve one configured channel identifier."""
        ...

    async def get_permissions(self, entity: object, user: str) -> object:
        """Read account permissions without sending a test message."""
        ...


type TelethonClientFactory = Callable[[Path, int, str, float], TelethonClientProtocol]
type AsyncSleep = Callable[[float], Awaitable[None]]
type MonotonicClock = Callable[[], float]
type PeerIdResolver = Callable[[object], int]


def create_telethon_client(
    session_path: Path,
    api_id: int,
    api_hash: str,
    timeout_seconds: float,
) -> TelethonClientProtocol:
    """Create an inert Telethon client with bounded retries and no prompting."""
    client = TelegramClient(
        str(session_path),
        api_id,
        api_hash,
        timeout=timeout_seconds,
        request_retries=1,
        connection_retries=1,
        retry_delay=1,
        auto_reconnect=False,
        flood_sleep_threshold=0,
        receive_updates=False,
    )
    return cast("TelethonClientProtocol", client)


class _SessionFileLock:
    """Hold an operating-system lock for one ignored session lock file."""

    __slots__ = ("_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: TextIO | None = None

    @property
    def path(self) -> Path:
        """Return the non-secret lock-file path for permission hardening."""
        return self._path

    def try_acquire(self) -> bool:
        """Attempt one non-blocking cross-process exclusive lock."""
        if self._handle is not None:
            return True
        handle = self._path.open("a+", encoding="utf-8", newline="")
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write("0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                module = importlib.import_module("fcntl")
                flock = cast("Callable[[int, int], None]", module.flock)
                flock(handle.fileno(), int(module.LOCK_EX) | int(module.LOCK_NB))
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Release exactly one held operating-system lock."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                module = importlib.import_module("fcntl")
                flock = cast("Callable[[int, int], None]", module.flock)
                flock(handle.fileno(), int(module.LOCK_UN))
        finally:
            handle.close()

    def __enter__(self) -> _SessionFileLock:
        """Return an already-acquired lock for cleanup helpers."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lock regardless of the protected outcome."""
        del exc_type, exc_value, traceback
        self.release()


@dataclass(slots=True)
class TelethonSessionAdapter:
    """Authenticate one SQLite-backed Telethon session safely and explicitly."""

    session_path: Path
    api_id: int = field(repr=False)
    api_hash: str = field(repr=False)
    timeout_seconds: float
    lock_timeout_seconds: float = 2.0
    runtime_root: Path = Path("var/sessions")
    client_factory: TelethonClientFactory = field(
        default=create_telethon_client,
        repr=False,
    )
    sleeper: AsyncSleep = field(default=asyncio.sleep, repr=False)
    monotonic_clock: MonotonicClock = field(default=monotonic, repr=False)
    peer_id_resolver: PeerIdResolver = field(
        default=telethon_utils.get_peer_id,
        repr=False,
    )
    _client: TelethonClientProtocol | None = field(default=None, init=False, repr=False)
    _lock: _SessionFileLock | None = field(default=None, init=False, repr=False)
    _phone_number: str | None = field(default=None, init=False, repr=False)
    _recover_unauthorized_session: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate bounded settings and the approved private runtime path."""
        if type(self.api_id) is not int or self.api_id <= 0:
            raise ValueError("api_id must be a positive integer")
        if type(self.api_hash) is not str or not self.api_hash:
            raise ValueError("api_hash must be a non-empty string")
        for value in (self.timeout_seconds, self.lock_timeout_seconds):
            if isinstance(value, bool) or value <= 0:
                raise ValueError("timeouts must be positive numbers")

        root = self.runtime_root.resolve()
        resolved = self.session_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(
                "session_path must be inside the approved runtime root"
            ) from None
        if resolved.suffix != ".session":
            raise ValueError("session_path must use the .session extension")
        object.__setattr__(self, "session_path", resolved)
        object.__setattr__(self, "runtime_root", root)

    @property
    def connected_client(self) -> TelethonClientProtocol:
        """Return the explicitly opened client while this adapter owns its lock."""
        if self._client is None or self._lock is None:
            raise TelegramSessionInvalidError
        return self._client

    async def open_authorized_client(self) -> TelethonClientProtocol:
        """Open an existing session non-interactively and hold its lock."""
        if self._client is not None or self._lock is not None:
            raise TelegramSessionMutationConflictError
        if not self.session_path.exists():
            raise TelegramSessionInvalidError
        await self._acquire_lock()
        client = self.client_factory(
            self.session_path,
            self.api_id,
            self.api_hash,
            float(self.timeout_seconds),
        )
        self._client = client
        try:
            await self._bounded(client.connect())
            if not await self._bounded(client.is_user_authorized()):
                raise TelegramSessionInvalidError
        except TelegramSessionInvalidError:
            await self.abort_login()
            raise
        except Exception as error:
            await self.abort_login()
            raise self._map_error(error) from error
        return client

    async def inspect_session(self) -> TelegramSessionStatus:
        """Inspect authorization under a short lock without prompting."""
        if not self.session_path.exists():
            return TelegramSessionStatus.MISSING
        await self._acquire_lock()
        client = self.client_factory(
            self.session_path,
            self.api_id,
            self.api_hash,
            float(self.timeout_seconds),
        )
        try:
            await self._bounded(client.connect())
            authorized = await self._bounded(client.is_user_authorized())
            if authorized:
                self._recover_unauthorized_session = False
                return TelegramSessionStatus.AUTHORIZED
            self._recover_unauthorized_session = True
            return TelegramSessionStatus.INVALID
        except (AuthKeyUnregisteredError, UnauthorizedError) as error:
            del error
            self._recover_unauthorized_session = True
            return TelegramSessionStatus.INVALID
        except Exception as error:
            raise self._map_error(error) from error
        finally:
            await self._disconnect_best_effort(client)
            self._release_lock()

    async def validate_account(self) -> TelegramAccount:
        """Validate authorization and return only account ID and Premium status."""
        if not self.session_path.exists():
            raise TelegramSessionInvalidError
        await self._acquire_lock()
        client = cast(
            "TelethonValidationClientProtocol",
            self.client_factory(
                self.session_path,
                self.api_id,
                self.api_hash,
                float(self.timeout_seconds),
            ),
        )
        try:
            await self._bounded(client.connect())
            if not await self._bounded(client.is_user_authorized()):
                raise TelegramSessionInvalidError
            account = await self._bounded(client.get_me())
            if account is None:
                raise TelegramSessionInvalidError
            account_id = getattr(account, "id", None)
            premium = getattr(account, "premium", False)
            if (
                type(account_id) is not int
                or account_id <= 0
                or type(premium) is not bool
            ):
                raise TelegramSessionInvalidError
            return TelegramAccount(account_id=account_id, is_premium=premium)
        except TelegramSessionInvalidError:
            raise
        except Exception as error:
            raise self._map_error(error) from error
        finally:
            await self._disconnect_best_effort(client)
            self._release_lock()

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        """Resolve canonical identity and permissions without history or send."""
        await self._acquire_lock()
        client = cast(
            "TelethonValidationClientProtocol",
            self.client_factory(
                self.session_path,
                self.api_id,
                self.api_hash,
                float(self.timeout_seconds),
            ),
        )
        try:
            await self._bounded(client.connect())
            if not await self._bounded(client.is_user_authorized()):
                raise TelegramSessionInvalidError
            identifier: str | int | None = (
                reference.configured_username.removeprefix("@")
                if reference.configured_username is not None
                else reference.configured_channel_id
            )
            if identifier is None:
                raise AssertionError("validated channel reference has no identifier")
            entity = await self._bounded(client.get_entity(identifier))
            channel_id = self.peer_id_resolver(entity)
            if type(channel_id) is not int or channel_id == 0:
                raise TelegramChannelNotFoundError
            username = getattr(entity, "username", None)
            title = getattr(entity, "title", None)
            if username is not None and type(username) is not str:
                username = None
            if type(title) is not str or not title or title.isspace():
                title = reference.config_name
            can_read = type(entity).__name__ != "ChannelForbidden"
            if reference.role is TelegramChannelRole.SOURCE:
                can_publish = False
            else:
                try:
                    permissions = await self._bounded(
                        client.get_permissions(entity, "me")
                    )
                except UserNotParticipantError:
                    can_publish = False
                else:
                    can_publish = bool(
                        getattr(permissions, "is_creator", False)
                        or getattr(permissions, "post_messages", False)
                    )
            return ResolvedTelegramChannel(
                channel_id=channel_id,
                username=username,
                display_name=title,
                can_read=can_read,
                can_publish=can_publish,
            )
        except (UsernameInvalidError, UsernameNotOccupiedError, ValueError) as error:
            raise TelegramChannelNotFoundError(cause=error) from error
        except ChannelPrivateError as error:
            raise TelegramChannelPermissionError(cause=error) from error
        except TelegramGatewayError:
            raise
        except Exception as error:
            raise self._map_error(error) from error
        finally:
            await self._disconnect_best_effort(client)
            self._release_lock()

    async def begin_login(self, phone_number: str) -> None:
        """Acquire mutation ownership and request one verification code."""
        if self._client is not None or self._lock is not None:
            raise TelegramSessionMutationConflictError
        if type(phone_number) is not str or not phone_number or phone_number.isspace():
            raise ValueError("phone_number must be a non-blank string")
        await self._acquire_lock()
        try:
            if self._recover_unauthorized_session:
                self._discard_unauthorized_session()
                self._recover_unauthorized_session = False
            client = self.client_factory(
                self.session_path,
                self.api_id,
                self.api_hash,
                float(self.timeout_seconds),
            )
            self._client = client
            self._phone_number = phone_number
            await self._bounded(client.connect())
            await self._bounded(client.send_code_request(phone_number))
        except Exception as error:
            await self.abort_login()
            raise self._map_error(error) from error

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        """Submit a code, retaining the lock only while 2FA remains pending."""
        client, phone_number = self._require_pending_login()
        try:
            await self._bounded(client.sign_in(phone=phone_number, code=code))
        except SessionPasswordNeededError:
            return TelegramLoginStep.TWO_FACTOR_PASSWORD_REQUIRED
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as error:
            raise TelegramInvalidCodeError(cause=error) from error
        except Exception as error:
            raise self._map_error(error) from error
        await self._finish_login()
        return TelegramLoginStep.AUTHORIZED

    async def submit_two_factor_password(self, password: str) -> None:
        """Complete a pending two-factor step and secure the session file."""
        client, _ = self._require_pending_login()
        try:
            await self._bounded(client.sign_in(password=password))
        except PasswordHashInvalidError as error:
            raise TelegramInvalidPasswordError(cause=error) from error
        except Exception as error:
            raise self._map_error(error) from error
        await self._finish_login()

    async def abort_login(self) -> None:
        """Disconnect and release mutation ownership after any partial flow."""
        client = self._client
        self._client = None
        self._phone_number = None
        if client is not None:
            await self._disconnect_best_effort(client)
        self._release_lock()

    async def close(self) -> None:
        """Close any pending client and release the session lock idempotently."""
        await self.abort_login()

    async def _finish_login(self) -> None:
        client = self._client
        if client is None:
            raise TelegramSessionMutationConflictError
        await self._disconnect_best_effort(client)
        self._secure_runtime_files()
        self._client = None
        self._phone_number = None
        self._release_lock()

    def _require_pending_login(self) -> tuple[TelethonClientProtocol, str]:
        if self._client is None or self._phone_number is None or self._lock is None:
            raise TelegramSessionMutationConflictError
        return self._client, self._phone_number

    async def _bounded[T](self, operation: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(float(self.timeout_seconds)):
                return await operation
        except TimeoutError as error:
            raise TelegramOperationTimeoutError(cause=error) from error

    async def _acquire_lock(self) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self._set_private_permissions(self.session_path.parent, 0o700)
        lock = _SessionFileLock(self.session_path.with_suffix(".session.lock"))
        deadline = self.monotonic_clock() + float(self.lock_timeout_seconds)
        while not lock.try_acquire():
            remaining = deadline - self.monotonic_clock()
            if remaining <= 0:
                raise TelegramSessionMutationConflictError
            await self.sleeper(min(0.05, remaining))
        self._lock = lock
        self._set_private_permissions(lock.path, 0o600)

    def _release_lock(self) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            lock.release()

    async def _disconnect_best_effort(self, client: TelethonClientProtocol) -> None:
        with suppress(Exception):
            await self._bounded(client.disconnect())

    def _secure_runtime_files(self) -> None:
        self._set_private_permissions(self.session_path.parent, 0o700)
        if self.session_path.exists():
            self._set_private_permissions(self.session_path, 0o600)

    def _discard_unauthorized_session(self) -> None:
        """Remove only session files proven unauthorized while holding the lock."""
        for suffix in ("", "-journal", "-shm", "-wal"):
            path = Path(f"{self.session_path}{suffix}")
            with suppress(FileNotFoundError):
                path.unlink()

    @staticmethod
    def _set_private_permissions(path: Path, mode: int) -> None:
        with suppress(OSError):
            path.chmod(mode)

    @staticmethod
    def _map_error(error: Exception) -> Exception:
        if isinstance(
            error, (TelegramOperationTimeoutError, TelegramSessionInvalidError)
        ):
            return error
        if isinstance(error, FloodWaitError):
            return TelegramRateLimitError(max(0, int(error.seconds)), cause=error)
        if isinstance(error, (AuthKeyUnregisteredError, UnauthorizedError)):
            return TelegramSessionInvalidError(cause=error)
        if isinstance(error, (ConnectionError, OSError)):
            return TelegramTransientError(cause=error)
        if isinstance(error, RPCError):
            return TelegramTransientError(cause=error)
        return TelegramTransientError(cause=error)


__all__ = (
    "TelethonClientFactory",
    "TelethonClientProtocol",
    "TelethonSessionAdapter",
    "create_telethon_client",
)
