"""Distributed runtime lease coordination for long-running components."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
import hashlib
import os
import socket
import uuid

from src.domain.interfaces import RuntimeLeaseRepository
from src.shared.errors import (
    ApplicationAlreadyRunningError,
    RuntimeLeaseLostError,
)
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class RuntimeLeaseService:
    """
    Own and renew one distributed runtime role lease.

    The service hashes role identity values before persistence so Telegram
    tokens, API hashes, and session credentials never enter logs or MongoDB.
    """

    def __init__(
        self,
        repository: RuntimeLeaseRepository,
        role: str,
        identity_values: Iterable[str],
        *,
        lease_seconds: float = 60.0,
        heartbeat_seconds: float = 15.0,
        max_heartbeat_failures: int = 3,
        owner_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Initialize one runtime lease coordinator.

        Args:
            repository: Distributed lease persistence adapter.
            role: Human-readable component role such as ``bot-polling``.
            identity_values: Secret or non-secret values uniquely identifying
                the component configuration.
            lease_seconds: Lease lifetime after each successful renewal.
            heartbeat_seconds: Delay between renewal attempts.
            max_heartbeat_failures: Consecutive repository errors tolerated
                before the guarded component is stopped.
            owner_id: Optional deterministic owner id used by tests.
            clock: Optional UTC clock used by tests.
        """
        digest_input = "\x1f".join(str(value) for value in identity_values)
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
        self.lease_id = f"{role}:{digest}"
        self.role = role
        self.owner_id = owner_id or self._new_owner_id()
        self._repository = repository
        self._lease_seconds = max(5.0, lease_seconds)
        self._heartbeat_seconds = max(0.01, heartbeat_seconds)
        self._max_heartbeat_failures = max(1, max_heartbeat_failures)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._acquired = False

    @property
    def is_acquired(self) -> bool:
        """Return whether this service currently owns its lease."""
        return self._acquired

    async def acquire(self) -> None:
        """
        Acquire the role lease or raise when another instance owns it.

        Raises:
            ApplicationAlreadyRunningError: Another unexpired owner holds the
                same role and configuration identity.
            RepositoryError: MongoDB cannot be queried.
        """
        await self._repository.ensure_indexes()
        now = self._utc_now()
        acquired = await self._repository.try_acquire(
            self.lease_id,
            self.owner_id,
            now,
            now + timedelta(seconds=self._lease_seconds),
            {
                "role": self.role,
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
            },
        )
        if not acquired:
            raise ApplicationAlreadyRunningError(
                f"Another instance already owns runtime role '{self.role}'"
            )
        self._acquired = True
        logger.info("Runtime lease acquired role=%s lease=%s", self.role, self.lease_id)

    async def release(self) -> None:
        """Best-effort release of the currently owned lease."""
        if not self._acquired:
            return
        try:
            await self._repository.release(self.lease_id, self.owner_id)
            logger.info("Runtime lease released role=%s lease=%s", self.role, self.lease_id)
        except Exception as exc:
            logger.warning(
                "Runtime lease release failed role=%s lease=%s error=%s",
                self.role,
                self.lease_id,
                exc,
            )
        finally:
            self._acquired = False

    async def run_with_heartbeat(self, guarded: Awaitable[None]) -> None:
        """
        Run a component while renewing this lease in a sibling task.

        Args:
            guarded: Long-running component coroutine.

        Raises:
            RuntimeLeaseLostError: Lease ownership or Mongo connectivity is
                lost long enough to make continued execution unsafe.
            Exception: Any exception raised by the guarded component.
        """
        if not self._acquired:
            if hasattr(guarded, "close"):
                guarded.close()  # type: ignore[attr-defined]
            raise RuntimeLeaseLostError(
                f"Runtime lease is not acquired for role '{self.role}'"
            )
        guarded_task = asyncio.create_task(guarded)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            done, _ = await asyncio.wait(
                {guarded_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = (
                    None if heartbeat_task.cancelled() else heartbeat_task.exception()
                )
                if heartbeat_error is not None:
                    raise heartbeat_error
                raise RuntimeLeaseLostError(
                    f"Runtime lease heartbeat stopped for role '{self.role}'"
                )
            await guarded_task
        finally:
            for task in (guarded_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                guarded_task,
                heartbeat_task,
                return_exceptions=True,
            )

    async def _heartbeat_loop(self) -> None:
        """Renew the lease until cancelled or ownership becomes unsafe."""
        consecutive_failures = 0
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                expires_at = self._utc_now() + timedelta(
                    seconds=self._lease_seconds
                )
                renewed = await self._repository.renew(
                    self.lease_id,
                    self.owner_id,
                    expires_at,
                )
                if not renewed:
                    self._acquired = False
                    raise RuntimeLeaseLostError(
                        f"Runtime lease ownership lost for role '{self.role}'"
                    )
                consecutive_failures = 0
            except asyncio.CancelledError:
                raise
            except RuntimeLeaseLostError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                logger.warning(
                    "Runtime lease heartbeat failed role=%s attempt=%d/%d error=%s",
                    self.role,
                    consecutive_failures,
                    self._max_heartbeat_failures,
                    exc,
                )
                if consecutive_failures >= self._max_heartbeat_failures:
                    self._acquired = False
                    raise RuntimeLeaseLostError(
                        f"Runtime lease heartbeat failed repeatedly for role '{self.role}'"
                    ) from exc

    def _utc_now(self) -> datetime:
        """Return an aware UTC timestamp from the configured clock."""
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _new_owner_id() -> str:
        """Build a process-unique owner id without embedding credentials."""
        return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
