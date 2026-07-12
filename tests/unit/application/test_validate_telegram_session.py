from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import (
    TelegramChannelValidationError,
    TelegramPremiumRequiredError,
    ValidateTelegramSession,
)
from telegram_assist_bot.application.ports import (
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramSessionInvalidError,
    TelegramTransientError,
)
from telegram_assist_bot.bootstrap.telegram_validation import (
    required_channel_references,
)
from telegram_assist_bot.shared.config import ApplicationConfig

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class FakeValidationGateway:
    account: TelegramAccount | Exception
    channels: dict[str, ResolvedTelegramChannel | Exception]

    async def validate_account(self) -> TelegramAccount:
        if isinstance(self.account, Exception):
            raise self.account
        return self.account

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        result = self.channels[reference.config_name]
        if isinstance(result, Exception):
            raise result
        return result


def reference(
    name: str,
    channel_id: int | None,
    role: TelegramChannelRole,
    *,
    username: str | None = None,
) -> TelegramChannelReference:
    return TelegramChannelReference(
        config_name=name,
        configured_channel_id=channel_id,
        configured_username=username,
        role=role,
        configuration_path=f"channels.{name}",
    )


def channel(
    channel_id: int,
    *,
    username: str | None = None,
    can_read: bool = True,
    can_publish: bool = False,
    usernames: tuple[str, ...] = (),
) -> ResolvedTelegramChannel:
    return ResolvedTelegramChannel(
        channel_id=channel_id,
        username=username,
        display_name="Synthetic channel",
        can_read=can_read,
        can_publish=can_publish,
        usernames=usernames,
    )


def test_validates_premium_account_and_all_channel_roles() -> None:
    source = reference("source", -1001, TelegramChannelRole.SOURCE, username="src")
    destination = reference(
        "destination",
        -1002,
        TelegramChannelRole.DESTINATION,
        username="@dest",
    )
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {
            "source": channel(-1001, username="src"),
            "destination": channel(-1002, username="dest", can_publish=True),
        },
    )

    report = run(ValidateTelegramSession(gateway).execute((source, destination)))

    assert report.account_id == 42
    assert [item.channel.channel_id for item in report.channels] == [-1001, -1002]


def test_username_only_source_accepts_the_resolved_canonical_identifier() -> None:
    source = reference("source", None, TelegramChannelRole.SOURCE, username="source")
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {"source": channel(-1001, username="source")},
    )

    report = run(ValidateTelegramSession(gateway).execute((source,)))

    assert report.channels[0].channel.channel_id == -1001


@pytest.mark.parametrize(
    ("configured", "active_usernames"),
    [
        ("alonews", ("alonews", "lastnews")),
        ("@LASTNEWS", ("alonews", "lastnews")),
        ("  Alonews ", ("alonews",)),
    ],
)
def test_accepts_any_normalized_active_channel_username(
    configured: str,
    active_usernames: tuple[str, ...],
) -> None:
    source = reference("source", -1001, TelegramChannelRole.SOURCE, username=configured)
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {"source": channel(-1001, username=None, usernames=active_usernames)},
    )

    report = run(ValidateTelegramSession(gateway).execute((source,)))

    assert report.channels[0].channel.usernames == active_usernames


def test_absent_active_usernames_still_report_username_mismatch() -> None:
    source = reference("source", -1001, TelegramChannelRole.SOURCE, username="alonews")
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {"source": channel(-1001, username=None)},
    )

    with pytest.raises(TelegramChannelValidationError) as captured:
        run(ValidateTelegramSession(gateway).execute((source,)))

    assert captured.value.issues[0].code == "username_mismatch"


def test_rejects_non_premium_account_before_channel_resolution() -> None:
    gateway = FakeValidationGateway(TelegramAccount(42, False), {})

    with pytest.raises(TelegramPremiumRequiredError):
        run(ValidateTelegramSession(gateway).execute(()))


def test_invalid_session_remains_distinct() -> None:
    gateway = FakeValidationGateway(TelegramSessionInvalidError(), {})

    with pytest.raises(TelegramSessionInvalidError):
        run(ValidateTelegramSession(gateway).execute(()))


def test_aggregates_mismatch_and_permission_failures() -> None:
    source = reference("source", -1001, TelegramChannelRole.SOURCE)
    destination = reference("destination", -1002, TelegramChannelRole.DESTINATION)
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {
            "source": channel(-9999),
            "destination": channel(-1002, can_publish=False),
        },
    )

    with pytest.raises(TelegramChannelValidationError) as captured:
        run(ValidateTelegramSession(gateway).execute((source, destination)))

    assert [(item.configuration_path, item.code) for item in captured.value.issues] == [
        ("channels.source", "canonical_id_mismatch"),
        ("channels.destination", "destination_publish_denied"),
    ]


def test_transient_resolution_failure_is_not_reclassified_as_permanent() -> None:
    source = reference("source", -1001, TelegramChannelRole.SOURCE)
    gateway = FakeValidationGateway(
        TelegramAccount(42, True),
        {"source": TelegramTransientError()},
    )

    with pytest.raises(TelegramChannelValidationError) as captured:
        run(ValidateTelegramSession(gateway).execute((source,)))

    assert captured.value.issues[0].error_category == "transient"


def test_disabled_source_is_not_resolved_and_its_destination_is_not_required() -> None:
    root = Path(__file__).resolve().parents[3]
    settings = ApplicationConfig.model_validate_json(
        (root / "config" / "configuration.example.json").read_text(encoding="utf-8")
    )
    disabled = settings.source_channels[0].model_copy(update={"enabled": False})
    settings = settings.model_copy(update={"source_channels": (disabled,)})

    assert required_channel_references(settings) == ()
