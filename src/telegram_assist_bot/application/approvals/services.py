"""Focused, SDK-independent approval workflow services."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

from telegram_assist_bot.application.ports.admin import (
    AdminMessagingGateway,
    ApprovalContent,
    ApprovalRepository,
    BotEditOutcome,
    BotUpdate,
    InlineButton,
    InlineKeyboard,
)
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    AdminRole,
    ApprovalReference,
    CallbackAction,
    CallbackClaims,
    DestinationSelection,
    SelectionMode,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

UNAUTHORIZED_TEXT = "شما مجاز به انجام این عملیات نیستید."
EXPIRED_TEXT = "این دکمه منقضی شده است. پیام تأیید را تازه‌سازی کنید."
INVALID_ACTION_TEXT = "این عملیات دیگر معتبر نیست."
TEMPORARY_FAILURE_TEXT = "در حال حاضر انجام عملیات ممکن نیست. دوباره تلاش کنید."
CALLBACK_LIFETIME = timedelta(days=14)
MAX_DESTINATIONS = 20


class _CallbackConsumer(Protocol):
    async def consume_callback(self, digest: str) -> bool: ...


class AuthorizationStatus(StrEnum):
    """Stable authorization outcomes without disclosure to the caller."""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNSUPPORTED_CHAT = "unsupported_chat"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Return the trusted administrator only after all checks pass."""

    status: AuthorizationStatus
    administrator: Administrator | None = None


class AuthorizeAdminAction:
    """Enforce private-chat, role, permission, and destination authorization."""

    def __init__(self, administrators: tuple[Administrator, ...]) -> None:
        """Index immutable administrator configuration by trusted actor ID."""
        self._administrators = {item.telegram_user_id: item for item in administrators}

    def execute(
        self,
        update: BotUpdate,
        permission: AdminPermission,
        *,
        destination_id: int | None = None,
    ) -> AuthorizationDecision:
        """Authorize using only the actor extracted from the trusted update."""
        if update.chat_type != "private" or update.actor_id != update.chat_id:
            return AuthorizationDecision(AuthorizationStatus.UNSUPPORTED_CHAT)
        admin = self._administrators.get(update.actor_id)
        if (
            admin is None
            or not admin.active
            or admin.role != AdminRole.ADMIN.value
            or permission not in admin.permissions
            or (
                destination_id is not None
                and destination_id not in admin.allowed_destination_ids
            )
        ):
            return AuthorizationDecision(AuthorizationStatus.DENIED)
        return AuthorizationDecision(AuthorizationStatus.ALLOWED, admin)


class CallbackStatus(StrEnum):
    """Typed callback validation results."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ACTOR_MISMATCH = "actor_mismatch"
    DESTINATION_DENIED = "destination_denied"
    INVALID_STATE = "invalid_state"


@dataclass(frozen=True, slots=True)
class CallbackResolution:
    """Return callback claims only for a valid opaque token."""

    status: CallbackStatus
    claims: CallbackClaims | None = None


class CallbackTokenService:
    """Issue and explicitly validate reusable server-side opaque tokens."""

    def __init__(
        self,
        repository: ApprovalRepository,
        random_bytes: Callable[[int], bytes],
    ) -> None:
        """Store injected persistence and deterministic CSPRNG boundary."""
        self._repository = repository
        self._random_bytes = random_bytes

    async def issue(
        self,
        *,
        actor_id: int,
        action: CallbackAction,
        post_id: str,
        destination_id: int | None,
        now: datetime,
        correlation_id: str | None = None,
    ) -> str:
        """Generate 128 random bits and persist only a digest as identity."""
        raw = self._random_bytes(16)
        if len(raw) != 16:
            raise ValueError("Callback randomness must contain exactly 128 bits.")
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        callback_data = f"c1_{encoded}"
        digest = hashlib.sha256(callback_data.encode("ascii")).hexdigest()
        issued = now.astimezone(UTC)
        await self._repository.insert_callback(
            CallbackClaims(
                digest,
                actor_id,
                action,
                post_id,
                destination_id,
                issued,
                issued + CALLBACK_LIFETIME,
                correlation_id=correlation_id,
            )
        )
        return callback_data

    async def resolve(
        self, callback_data: str, *, actor_id: int, now: datetime
    ) -> CallbackResolution:
        """Reject malformed, unknown, expired, revoked, or cross-actor tokens."""
        if not callback_data.startswith("c1_") or len(callback_data) >= 64:
            return CallbackResolution(CallbackStatus.INVALID)
        digest = hashlib.sha256(callback_data.encode("utf-8")).hexdigest()
        claims = await self._repository.get_callback(digest)
        if claims is None:
            return CallbackResolution(CallbackStatus.INVALID)
        if claims.revoked:
            return CallbackResolution(CallbackStatus.REVOKED)
        if now.astimezone(UTC) >= claims.expires_at:
            return CallbackResolution(CallbackStatus.EXPIRED)
        if claims.actor_id != actor_id:
            return CallbackResolution(CallbackStatus.ACTOR_MISMATCH)
        return CallbackResolution(CallbackStatus.VALID, claims)

    async def resolve_authorized(
        self,
        callback_data: str,
        *,
        update: BotUpdate,
        now: datetime,
        authorize: AuthorizeAdminAction,
        post_actionable: bool,
    ) -> CallbackResolution:
        """Revalidate current actor, destination permission, and Post state."""
        resolution = await self.resolve(
            callback_data, actor_id=update.actor_id, now=now
        )
        claims = resolution.claims
        if resolution.status is not CallbackStatus.VALID or claims is None:
            return resolution
        if not post_actionable:
            return CallbackResolution(CallbackStatus.INVALID_STATE)
        decision = authorize.execute(
            update,
            AdminPermission.TOGGLE,
            destination_id=claims.destination_id,
        )
        if decision.status is not AuthorizationStatus.ALLOWED:
            return CallbackResolution(CallbackStatus.DESTINATION_DENIED)
        return resolution

    async def consume(self, callback_data: str) -> bool:
        """Atomically consume one valid opaque token after authorization."""
        digest = hashlib.sha256(callback_data.encode("utf-8")).hexdigest()
        repository = cast("_CallbackConsumer", self._repository)
        return await repository.consume_callback(digest)


@dataclass(frozen=True, slots=True)
class DestinationOption:
    """Describe one destination in validated configuration order."""

    destination_id: int
    active: bool = True
    source_allowed: bool = True


class BuildDestinationKeyboard:
    """Build deterministic two-button rows without mutating selection state."""

    def __init__(self, token_service: CallbackTokenService) -> None:
        """Store the opaque token issuer."""
        self._tokens = token_service

    async def execute(
        self,
        *,
        actor: Administrator,
        post_id: str,
        destinations: tuple[DestinationOption, ...],
        selections: tuple[DestinationSelection, ...],
        now: datetime,
    ) -> InlineKeyboard:
        """Filter authorized destinations and issue one token per button."""
        allowed = tuple(
            item
            for item in destinations
            if item.active
            and item.source_allowed
            and item.destination_id in actor.allowed_destination_ids
        )
        if len(allowed) > MAX_DESTINATIONS:
            raise ValueError("An approval keyboard supports at most 20 destinations.")
        by_destination = {item.destination_id: item.mode for item in selections}
        rows: list[tuple[InlineButton, InlineButton]] = []
        for destination in allowed:
            mode = by_destination.get(destination.destination_id, SelectionMode.NONE)
            scheduled = await self._tokens.issue(
                actor_id=actor.telegram_user_id,
                action=CallbackAction.TOGGLE_SCHEDULED,
                post_id=post_id,
                destination_id=destination.destination_id,
                now=now,
            )
            immediate = await self._tokens.issue(
                actor_id=actor.telegram_user_id,
                action=CallbackAction.TOGGLE_IMMEDIATE,
                post_id=post_id,
                destination_id=destination.destination_id,
                now=now,
            )
            rows.append(
                (
                    InlineButton(
                        "✅ زمان‌بندی"
                        if mode is SelectionMode.SCHEDULED
                        else "🕒 زمان‌بندی",
                        scheduled,
                    ),
                    InlineButton(
                        "✅ فوری" if mode is SelectionMode.IMMEDIATE else "⚡ فوری",
                        immediate,
                    ),
                )
            )
        return InlineKeyboard(tuple(rows))


class RenderApprovalHeader:
    """Render deterministic Persian managerial metadata separately from content."""

    def execute(
        self,
        *,
        source_name: str,
        source_username: str | None,
        source_channel_id: int,
        post_id: str,
        status: str,
        category: str | None,
        duplicate: str | None,
        score: str | None,
    ) -> str:
        """Render explicit unavailable/pending values without source content."""

        def value(item: str | None, fallback: str) -> str:
            return item if item is not None else fallback

        username = f"@{source_username}" if source_username else "ناموجود"
        return "\n".join(
            (
                f"منبع: {source_name}",
                f"شناسه: {username} ({source_channel_id})",
                f"دسته‌بندی: {value(category, 'نامشخص')}",
                f"تکراری: {value(duplicate, 'در انتظار بررسی')}",
                f"امتیاز هوش مصنوعی: {value(score, 'در انتظار بررسی')}",
                f"شناسه داخلی: {post_id}",
                f"وضعیت: {status}",
            )
        )


class DeliverApproval:
    """Deliver header then prepared content and persist only identifiable success."""

    def __init__(
        self, gateway: AdminMessagingGateway, repository: ApprovalRepository
    ) -> None:
        """Store delivery boundaries."""
        self._gateway = gateway
        self._repository = repository

    async def execute(
        self,
        *,
        reference_id: str,
        actor_id: int,
        post_id: str,
        header: str,
        content: ApprovalContent,
        keyboard: InlineKeyboard | None = None,
    ) -> ApprovalReference:
        """Create a reference only after header and content identifiers exist."""
        existing = await self._repository.get_reference(reference_id)
        if existing is not None and existing.active:
            return existing
        if existing is None:
            header_id = await self._gateway.send_header(actor_id, header, keyboard)
            existing = await self._repository.save_delivery_progress(
                ApprovalReference(
                    reference_id,
                    actor_id,
                    actor_id,
                    post_id,
                    header_id,
                    (),
                    active=False,
                )
            )
        content_ids = await self._gateway.send_content(actor_id, content)
        return await self._repository.complete_reference(
            existing.reference_id, content_ids
        )


class ToggleStatus(StrEnum):
    """Stable toggle results."""

    UPDATED = "updated"
    CONFLICT = "conflict"
    DENIED = "denied"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ToggleResult:
    """Return the current selection for success and conflict."""

    status: ToggleStatus
    selection: DestinationSelection | None = None


class ToggleDestinationSelection:
    """Authorize and atomically compare-and-set one destination selection."""

    def __init__(
        self,
        repository: ApprovalRepository,
        authorize: AuthorizeAdminAction,
        *,
        after_commit: Callable[[DestinationSelection], Awaitable[None]] | None = None,
    ) -> None:
        """Store authorization and atomic persistence boundaries."""
        self._repository = repository
        self._authorize = authorize
        self._after_commit = after_commit

    async def execute(
        self,
        update: BotUpdate,
        *,
        post_id: str,
        destination_id: int,
        requested: SelectionMode,
        expected_version: int,
        post_actionable: bool,
        now: datetime,
        correlation_id: str,
    ) -> ToggleResult:
        """Return conflict after reloading state rather than retrying stale input."""
        decision = self._authorize.execute(
            update, AdminPermission.TOGGLE, destination_id=destination_id
        )
        if decision.status is not AuthorizationStatus.ALLOWED:
            return ToggleResult(ToggleStatus.DENIED)
        if not post_actionable:
            return ToggleResult(ToggleStatus.INVALID)
        current = await self._repository.get_selection(post_id, destination_id)
        if current.version != expected_version:
            return ToggleResult(ToggleStatus.CONFLICT, current)
        updated = current.toggle(
            requested,
            actor_id=update.actor_id,
            occurred_at=now,
            correlation_id=correlation_id,
        )
        if not await self._repository.compare_and_set_selection(current, updated):
            return ToggleResult(
                ToggleStatus.CONFLICT,
                await self._repository.get_selection(post_id, destination_id),
            )
        if self._after_commit is not None:
            await self._after_commit(updated)
        return ToggleResult(ToggleStatus.UPDATED, updated)


class SynchronizeApprovalMessages:
    """Fan out the latest state independently with stale-render protection."""

    def __init__(
        self, gateway: AdminMessagingGateway, repository: ApprovalRepository
    ) -> None:
        """Store edit and persistence boundaries."""
        self._gateway = gateway
        self._repository = repository

    async def execute(
        self,
        *,
        post_id: str,
        version: int,
        render: Callable[[ApprovalReference], Awaitable[tuple[str, InlineKeyboard]]],
        now: datetime,
    ) -> None:
        """Continue after per-reference failure and persist only safe categories."""
        for reference in await self._repository.list_active_references(post_id):
            if reference.rendered_version > version:
                continue
            header, keyboard = await render(reference)
            try:
                outcome = await self._gateway.edit_header(
                    reference.chat_id,
                    reference.header_message_id,
                    header,
                    keyboard,
                )
            except TimeoutError:
                await self._repository.mark_sync_failure(
                    reference.reference_id,
                    version,
                    category="timeout",
                    next_retry_at=now.astimezone(UTC) + timedelta(seconds=1),
                    inactive=False,
                )
                continue
            if outcome is BotEditOutcome.DELETED:
                await self._repository.mark_sync_failure(
                    reference.reference_id,
                    version,
                    category="permanent",
                    next_retry_at=None,
                    inactive=True,
                )
            else:
                await self._repository.mark_sync_success(
                    reference.reference_id, version
                )
