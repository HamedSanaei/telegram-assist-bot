"""Validate an existing Telegram session, Premium account, and channel access."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from telegram_assist_bot.application.ports import (
    ResolvedTelegramChannel,
    TelegramChannelReference,
    TelegramChannelRole,
    TelegramGatewayError,
    TelegramValidationGateway,
)


class TelegramPremiumRequiredError(Exception):
    """Report that the authorized Telegram account is not Premium."""

    error_category: ClassVar[str] = "authorization"

    def __init__(self) -> None:
        """Initialize a fixed account-detail-free message."""
        super().__init__("The configured Telegram account must be Premium.")


@dataclass(frozen=True, slots=True)
class TelegramChannelValidationIssue:
    """Describe one safe channel validation failure at a configuration path."""

    configuration_path: str
    code: str
    error_category: str


class TelegramChannelValidationError(Exception):
    """Aggregate all configured channel failures without private SDK details."""

    error_category: ClassVar[str] = "configuration"

    def __init__(self, issues: tuple[TelegramChannelValidationIssue, ...]) -> None:
        """Retain a deterministic non-empty tuple of safe validation issues."""
        if not issues:
            raise ValueError("channel validation issues must not be empty")
        self.issues = issues
        super().__init__("One or more configured Telegram channels are invalid.")


@dataclass(frozen=True, slots=True)
class ValidatedTelegramChannel:
    """Bind one configuration name and role to canonical channel metadata."""

    config_name: str
    role: TelegramChannelRole
    channel: ResolvedTelegramChannel


@dataclass(frozen=True, slots=True)
class TelegramValidationReport:
    """Return startup-local account and canonical channel validation facts."""

    account_id: int
    channels: tuple[ValidatedTelegramChannel, ...]


@dataclass(frozen=True, slots=True)
class ValidateTelegramSession:
    """Validate one existing session and aggregate configured channel failures."""

    gateway: TelegramValidationGateway = field(repr=False)

    async def execute(
        self,
        references: tuple[TelegramChannelReference, ...],
    ) -> TelegramValidationReport:
        """Validate Premium status and every required source or destination."""
        account = await self.gateway.validate_account()
        if not account.is_premium:
            raise TelegramPremiumRequiredError

        validated: list[ValidatedTelegramChannel] = []
        issues: list[TelegramChannelValidationIssue] = []
        for reference in references:
            try:
                resolved = await self.gateway.resolve_channel(reference)
            except TelegramGatewayError as error:
                issues.append(
                    TelegramChannelValidationIssue(
                        configuration_path=reference.configuration_path,
                        code="resolve_failed",
                        error_category=error.error_category,
                    )
                )
                continue
            issue = self._validate_resolved(reference, resolved)
            if issue is not None:
                issues.append(issue)
                continue
            validated.append(
                ValidatedTelegramChannel(
                    config_name=reference.config_name,
                    role=reference.role,
                    channel=resolved,
                )
            )

        if issues:
            raise TelegramChannelValidationError(tuple(issues))
        return TelegramValidationReport(
            account_id=account.account_id,
            channels=tuple(validated),
        )

    @staticmethod
    def _validate_resolved(
        reference: TelegramChannelReference,
        resolved: ResolvedTelegramChannel,
    ) -> TelegramChannelValidationIssue | None:
        if (
            reference.configured_channel_id is not None
            and resolved.channel_id != reference.configured_channel_id
        ):
            return TelegramChannelValidationIssue(
                reference.configuration_path,
                "canonical_id_mismatch",
                "configuration",
            )
        if reference.configured_username is not None:
            configured = reference.configured_username.removeprefix("@").casefold()
            actual = None if resolved.username is None else resolved.username.casefold()
            if configured != actual:
                return TelegramChannelValidationIssue(
                    reference.configuration_path,
                    "username_mismatch",
                    "configuration",
                )
        if reference.role is TelegramChannelRole.SOURCE and not resolved.can_read:
            return TelegramChannelValidationIssue(
                reference.configuration_path,
                "source_read_denied",
                "permission",
            )
        if reference.role is TelegramChannelRole.DESTINATION and not (
            resolved.can_publish
        ):
            return TelegramChannelValidationIssue(
                reference.configuration_path,
                "destination_publish_denied",
                "permission",
            )
        return None


__all__ = (
    "TelegramChannelValidationError",
    "TelegramChannelValidationIssue",
    "TelegramPremiumRequiredError",
    "TelegramValidationReport",
    "ValidateTelegramSession",
    "ValidatedTelegramChannel",
)
