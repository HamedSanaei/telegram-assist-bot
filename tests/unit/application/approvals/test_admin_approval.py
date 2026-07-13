from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from telegram_assist_bot.application.approvals import (
    AuthorizationStatus,
    AuthorizeAdminAction,
    BuildDestinationKeyboard,
    CallbackStatus,
    CallbackTokenService,
    DeliverApproval,
    DestinationOption,
    RenderApprovalHeader,
    SynchronizeApprovalMessages,
    ToggleDestinationSelection,
    ToggleStatus,
)
from telegram_assist_bot.application.ports import (
    ApprovalContent,
    BotEditOutcome,
    BotUpdate,
    InlineKeyboard,
)
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    ApprovalReference,
    CallbackAction,
    CallbackClaims,
    DestinationSelection,
    SelectionMode,
)

NOW = datetime(2026, 7, 12, tzinfo=UTC)


class MemoryRepository:
    def __init__(self) -> None:
        self.callbacks: dict[str, CallbackClaims] = {}
        self.references: dict[str, ApprovalReference] = {}
        self.selections: dict[tuple[str, int], DestinationSelection] = {}
        self.force_conflict = False

    async def insert_callback(self, claims: CallbackClaims) -> None:
        self.callbacks[claims.token_digest] = claims

    async def get_callback(self, digest: str) -> CallbackClaims | None:
        return self.callbacks.get(digest)

    async def consume_callback(self, digest: str) -> bool:
        claims = self.callbacks.get(digest)
        if claims is None or claims.revoked:
            return False
        self.callbacks[digest] = replace(claims, revoked=True)
        return True

    async def revoke_post_callbacks(self, post_id: str) -> int:
        count = 0
        for key, claims in tuple(self.callbacks.items()):
            if claims.post_id == post_id and not claims.revoked:
                self.callbacks[key] = replace(claims, revoked=True)
                count += 1
        return count

    async def save_reference(self, reference: ApprovalReference) -> ApprovalReference:
        return self.references.setdefault(reference.reference_id, reference)

    async def get_reference(self, reference_id: str) -> ApprovalReference | None:
        return self.references.get(reference_id)

    async def save_delivery_progress(
        self, reference: ApprovalReference
    ) -> ApprovalReference:
        return await self.save_reference(reference)

    async def complete_reference(
        self, reference_id: str, content_message_ids: tuple[int, ...]
    ) -> ApprovalReference:
        completed = replace(
            self.references[reference_id],
            content_message_ids=content_message_ids,
            active=True,
        )
        self.references[reference_id] = completed
        return completed

    async def list_active_references(
        self, post_id: str
    ) -> tuple[ApprovalReference, ...]:
        return tuple(
            item
            for item in self.references.values()
            if item.post_id == post_id and item.active
        )

    async def get_selection(
        self, post_id: str, destination_id: int
    ) -> DestinationSelection:
        return self.selections.get(
            (post_id, destination_id), DestinationSelection(post_id, destination_id)
        )

    async def compare_and_set_selection(
        self, current: DestinationSelection, updated: DestinationSelection
    ) -> bool:
        if self.force_conflict:
            self.force_conflict = False
            self.selections[(current.post_id, current.destination_id)] = replace(
                current, version=current.version + 1
            )
            return False
        self.selections[(current.post_id, current.destination_id)] = updated
        return True

    async def mark_sync_success(self, reference_id: str, version: int) -> bool:
        item = self.references[reference_id]
        if item.rendered_version > version:
            return False
        self.references[reference_id] = replace(item, rendered_version=version)
        return True

    async def mark_sync_failure(
        self,
        reference_id: str,
        version: int,
        *,
        category: str,
        next_retry_at: datetime | None,
        inactive: bool,
    ) -> bool:
        item = self.references[reference_id]
        self.references[reference_id] = replace(
            item,
            active=not inactive,
            attempt_count=item.attempt_count + 1,
            next_retry_at=next_retry_at,
            last_error_category=category,
        )
        return True

    async def claim_retry(
        self, reference_id: str, *, now: datetime, lease_until: datetime
    ) -> bool:
        del reference_id, now, lease_until
        return True


class FakeGateway:
    def __init__(self) -> None:
        self.edits: list[int] = []
        self.outcomes: dict[int, BotEditOutcome | Exception] = {}
        self.closed = 0
        self.header_sends = 0
        self.fail_content = False

    async def send_header(
        self, chat_id: int, text: str, keyboard: InlineKeyboard | None = None
    ) -> int:
        del chat_id, text, keyboard
        self.header_sends += 1
        return 10

    async def send_content(
        self, chat_id: int, content: ApprovalContent
    ) -> tuple[int, ...]:
        del chat_id
        if self.fail_content:
            self.fail_content = False
            raise TimeoutError
        assert content.text == "سلام‌دنیا\n😀"
        return (11, 12)

    async def edit_header(
        self, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard
    ) -> BotEditOutcome:
        del chat_id, text, keyboard
        self.edits.append(message_id)
        outcome = self.outcomes.get(message_id, BotEditOutcome.UPDATED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        del query_id, text, alert

    async def close(self) -> None:
        self.closed += 1


def admin(
    *, active: bool = True, permissions: frozenset[AdminPermission] | None = None
) -> Administrator:
    return Administrator(
        1001,
        active,
        "admin",
        permissions or frozenset(AdminPermission),
        frozenset({-2001, -2002}),
    )


def test_authorization_is_private_default_deny_and_destination_scoped() -> None:
    authorize = AuthorizeAdminAction((admin(),))
    assert (
        authorize.execute(BotUpdate(1001, 1001, "private"), AdminPermission.VIEW).status
        is AuthorizationStatus.ALLOWED
    )
    for update in (
        BotUpdate(999, 999, "private"),
        BotUpdate(1001, -1, "group"),
        BotUpdate(1001, 999, "private"),
    ):
        assert (
            authorize.execute(update, AdminPermission.VIEW).status
            is not AuthorizationStatus.ALLOWED
        )
    assert (
        authorize.execute(
            BotUpdate(1001, 1001, "private"),
            AdminPermission.TOGGLE,
            destination_id=-999,
        ).status
        is AuthorizationStatus.DENIED
    )
    no_toggle = admin(permissions=frozenset({AdminPermission.VIEW}))
    assert (
        AuthorizeAdminAction((no_toggle,))
        .execute(BotUpdate(1001, 1001, "private"), AdminPermission.TOGGLE)
        .status
        is AuthorizationStatus.DENIED
    )


def test_selection_complete_transition_table_and_aware_time() -> None:
    selection = DestinationSelection("post", -2)
    for requested, expected in (
        (SelectionMode.IMMEDIATE, SelectionMode.IMMEDIATE),
        (SelectionMode.IMMEDIATE, SelectionMode.NONE),
        (SelectionMode.SCHEDULED, SelectionMode.SCHEDULED),
        (SelectionMode.IMMEDIATE, SelectionMode.IMMEDIATE),
    ):
        selection = selection.toggle(
            requested, actor_id=1, occurred_at=NOW, correlation_id="corr"
        )
        assert selection.mode is expected
    assert selection.version == 4
    with pytest.raises(ValueError, match="actionable"):
        selection.toggle(
            SelectionMode.NONE, actor_id=1, occurred_at=NOW, correlation_id="corr"
        )


@pytest.mark.parametrize(
    "mutation", ["changed", "unknown", "expired", "revoked", "actor"]
)
def test_callback_security_and_reuse(mutation: str) -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        service = CallbackTokenService(repository, lambda size: bytes(range(size)))
        token = await service.issue(
            actor_id=1001,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post",
            destination_id=-2001,
            now=NOW,
        )
        assert token.startswith("c1_")
        assert len(token) < 64
        assert "post" not in token
        actor_id, now, candidate = 1001, NOW, token
        if mutation == "changed":
            candidate += "x"
        if mutation == "unknown":
            candidate = "c1_AAAAAAAAAAAAAAAAAAAAAA"
        if mutation == "expired":
            now = NOW.replace(year=2027)
        if mutation == "actor":
            actor_id = 1002
        if mutation == "revoked":
            await repository.revoke_post_callbacks("post")
        result = await service.resolve(candidate, actor_id=actor_id, now=now)
        expected = {
            "changed": CallbackStatus.INVALID,
            "unknown": CallbackStatus.INVALID,
            "expired": CallbackStatus.EXPIRED,
            "revoked": CallbackStatus.REVOKED,
            "actor": CallbackStatus.ACTOR_MISMATCH,
        }[mutation]
        assert result.status is expected
        if mutation == "changed":
            assert (
                await service.resolve(token, actor_id=1001, now=NOW)
            ).status is CallbackStatus.VALID
            assert (
                await service.resolve(token, actor_id=1001, now=NOW)
            ).status is CallbackStatus.VALID

    asyncio.run(scenario())


def test_callback_revalidates_randomness_state_and_current_permission() -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        invalid_random = CallbackTokenService(repository, lambda _size: b"short")
        with pytest.raises(ValueError, match="128 bits"):
            await invalid_random.issue(
                actor_id=1001,
                action=CallbackAction.TOGGLE_IMMEDIATE,
                post_id="post",
                destination_id=-2001,
                now=NOW,
            )
        service = CallbackTokenService(repository, lambda size: b"r" * size)
        token = await service.issue(
            actor_id=1001,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post",
            destination_id=-2001,
            now=NOW,
        )
        update = BotUpdate(1001, 1001, "private")
        authorize = AuthorizeAdminAction((admin(),))
        invalid_state = await service.resolve_authorized(
            token,
            update=update,
            now=NOW,
            authorize=authorize,
            post_actionable=False,
        )
        assert invalid_state.status is CallbackStatus.INVALID_STATE
        denied = await service.resolve_authorized(
            token,
            update=update,
            now=NOW,
            authorize=AuthorizeAdminAction((replace(admin(), active=False),)),
            post_actionable=True,
        )
        assert denied.status is CallbackStatus.DESTINATION_DENIED
        malformed = await service.resolve("bad", actor_id=1001, now=NOW)
        assert malformed.status is CallbackStatus.INVALID

    asyncio.run(scenario())


def test_keyboard_labels_order_filter_overflow_and_header() -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        builder = BuildDestinationKeyboard(
            CallbackTokenService(repository, lambda size: b"x" * size)
        )
        keyboard = await builder.execute(
            actor=admin(),
            post_id="post",
            destinations=(
                DestinationOption(-2002),
                DestinationOption(-999),
                DestinationOption(-2001),
            ),
            selections=(DestinationSelection("post", -2001, SelectionMode.IMMEDIATE),),
            now=NOW,
        )
        assert [row[0].label for row in keyboard.rows] == ["🕒 زمان‌بندی", "🕒 زمان‌بندی"]
        assert keyboard.rows[1][1].label == "✅ فوری"
        with pytest.raises(ValueError, match="at most 20"):
            await builder.execute(
                actor=replace(admin(), allowed_destination_ids=frozenset(range(21))),
                post_id="post",
                destinations=tuple(DestinationOption(item) for item in range(21)),
                selections=(),
                now=NOW,
            )
        header = RenderApprovalHeader().execute(
            source_name="منبع",
            source_username=None,
            source_channel_id=-1,
            post_id="post",
            status="آماده تأیید",
            category=None,
            duplicate=None,
            score=None,
        )
        assert "ناموجود" in header
        assert "در انتظار بررسی" in header
        assert "سلام" not in header

    asyncio.run(scenario())


def test_delivery_toggle_conflict_and_best_effort_sync() -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        gateway = FakeGateway()
        reference = await DeliverApproval(gateway, repository).execute(
            reference_id="r1",
            actor_id=1001,
            post_id="post",
            header="هدر",
            content=ApprovalContent("سلام‌دنیا\n😀", None),
        )
        assert reference.header_message_id == 10
        assert reference.content_message_ids == (11, 12)
        toggle = ToggleDestinationSelection(
            repository, AuthorizeAdminAction((admin(),))
        )
        update = BotUpdate(1001, 1001, "private")
        result = await toggle.execute(
            update,
            post_id="post",
            destination_id=-2001,
            requested=SelectionMode.IMMEDIATE,
            expected_version=0,
            post_actionable=True,
            now=NOW,
            correlation_id="corr",
        )
        assert result.status is ToggleStatus.UPDATED
        assert result.selection is not None
        repository.force_conflict = True
        conflict = await toggle.execute(
            update,
            post_id="post",
            destination_id=-2001,
            requested=SelectionMode.SCHEDULED,
            expected_version=1,
            post_actionable=True,
            now=NOW,
            correlation_id="corr",
        )
        assert conflict.status is ToggleStatus.CONFLICT
        assert conflict.selection is not None
        repository.references["r2"] = ApprovalReference(
            "r2", 1002, 1002, "post", 20, ()
        )
        gateway.outcomes[10] = TimeoutError()
        gateway.outcomes[20] = BotEditOutcome.NOT_MODIFIED

        async def render(item: ApprovalReference) -> tuple[str, InlineKeyboard]:
            return f"نسخه {item.reference_id}", InlineKeyboard(())

        await SynchronizeApprovalMessages(gateway, repository).execute(
            post_id="post", version=3, render=render, now=NOW
        )
        assert gateway.edits == [10, 20]
        assert repository.references["r1"].last_error_category == "timeout"
        assert repository.references["r2"].rendered_version == 3

    asyncio.run(scenario())


def test_partial_delivery_reuses_header_after_restart() -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        gateway = FakeGateway()
        gateway.fail_content = True
        delivery = DeliverApproval(gateway, repository)
        content = ApprovalContent("سلام‌دنیا\n😀", None)
        with pytest.raises(TimeoutError):
            await delivery.execute(
                reference_id="stable",
                actor_id=1001,
                post_id="post",
                header="هدر",
                content=content,
            )
        progress = repository.references["stable"]
        assert not progress.active
        assert progress.content_message_ids == ()
        completed = await DeliverApproval(gateway, repository).execute(
            reference_id="stable",
            actor_id=1001,
            post_id="post",
            header="هدر",
            content=content,
        )
        assert completed.active
        assert completed.content_message_ids == (11, 12)
        assert gateway.header_sends == 1

    asyncio.run(scenario())


def test_toggle_rejections_deleted_sync_and_stale_reference() -> None:
    async def scenario() -> None:
        repository = MemoryRepository()
        authorize = AuthorizeAdminAction((admin(),))
        toggle = ToggleDestinationSelection(repository, authorize)
        denied = await toggle.execute(
            BotUpdate(999, 999, "private"),
            post_id="post",
            destination_id=-2001,
            requested=SelectionMode.IMMEDIATE,
            expected_version=0,
            post_actionable=True,
            now=NOW,
            correlation_id="c",
        )
        assert denied.status is ToggleStatus.DENIED
        invalid = await toggle.execute(
            BotUpdate(1001, 1001, "private"),
            post_id="post",
            destination_id=-2001,
            requested=SelectionMode.IMMEDIATE,
            expected_version=0,
            post_actionable=False,
            now=NOW,
            correlation_id="c",
        )
        assert invalid.status is ToggleStatus.INVALID
        repository.selections[("post", -2001)] = DestinationSelection(
            "post", -2001, version=2
        )
        stale = await toggle.execute(
            BotUpdate(1001, 1001, "private"),
            post_id="post",
            destination_id=-2001,
            requested=SelectionMode.SCHEDULED,
            expected_version=1,
            post_actionable=True,
            now=NOW,
            correlation_id="c",
        )
        assert stale.status is ToggleStatus.CONFLICT

        gateway = FakeGateway()
        repository.references["old"] = ApprovalReference(
            "old", 1001, 1001, "post", 1, (), rendered_version=5
        )
        repository.references["gone"] = ApprovalReference(
            "gone", 1002, 1002, "post", 2, ()
        )
        gateway.outcomes[2] = BotEditOutcome.DELETED

        async def render(_item: ApprovalReference) -> tuple[str, InlineKeyboard]:
            return "هدر", InlineKeyboard(())

        await SynchronizeApprovalMessages(gateway, repository).execute(
            post_id="post", version=3, render=render, now=NOW
        )
        assert gateway.edits == [2]
        assert not repository.references["gone"].active

    asyncio.run(scenario())
