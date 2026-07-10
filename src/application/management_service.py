"""Application service for approval-bot management panel operations."""

from __future__ import annotations

from typing import Protocol

from src.domain.entities import DestinationChannel
from src.domain.interfaces import ChannelRepository
from src.shared.config import RecurringForwardConfig


class ConfigurationEditor(Protocol):
    """Port for persistent runtime-safe configuration edits."""

    async def add_source(self, identifier: str) -> None: ...
    async def remove_source(self, identifier: str) -> None: ...
    async def upsert_destination(self, channel: DestinationChannel) -> None: ...
    async def remove_destination(self, chat_id: int) -> None: ...
    async def list_campaigns(self) -> list[RecurringForwardConfig]: ...
    async def upsert_campaign(self, campaign: RecurringForwardConfig) -> None: ...
    async def delete_campaign(self, campaign_id: str) -> None: ...
    async def set_campaign_enabled(self, campaign_id: str, enabled: bool) -> None: ...


class RecurringCampaignStore(Protocol):
    """Operational SQLite mirror for recurring campaign definitions."""

    async def upsert(self, campaign: RecurringForwardConfig) -> None: ...
    async def delete(self, campaign_id: str) -> None: ...
    async def set_enabled(self, campaign_id: str, enabled: bool) -> None: ...


class ManagementService:
    """Persist panel changes to both config JSON and live SQLite state."""

    def __init__(
        self,
        channels: ChannelRepository,
        editor: ConfigurationEditor,
        campaigns: RecurringCampaignStore,
    ) -> None:
        """Initialize the service with config and operational repositories."""
        self._channels = channels
        self._editor = editor
        self._campaigns = campaigns

    async def add_source(self, identifier: str) -> None:
        """Persist and activate one source channel."""
        await self._editor.add_source(identifier)
        await self._channels.upsert_source(identifier)

    async def remove_source(self, identifier: str) -> bool:
        """Persist removal and disable one source channel."""
        await self._editor.remove_source(identifier)
        return await self._channels.disable_source(identifier)

    async def list_sources(self) -> list[str]:
        """Return enabled source identifiers."""
        return await self._channels.list_sources()

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Persist and activate one destination channel."""
        await self._editor.upsert_destination(channel)
        await self._channels.upsert_destination(channel)

    async def remove_destination(self, chat_id: int) -> None:
        """Persist removal and disable one destination channel."""
        await self._editor.remove_destination(chat_id)
        await self._channels.disable_destinations_except(
            {
                item.chat_id
                for item in await self._channels.list_destinations()
                if item.chat_id != chat_id
            }
        )

    async def list_destinations(self) -> list[DestinationChannel]:
        """Return enabled destination channels."""
        return await self._channels.list_destinations()

    async def list_campaigns(self) -> list[RecurringForwardConfig]:
        """Return recurring campaigns from authoritative config."""
        return await self._editor.list_campaigns()

    async def upsert_campaign(self, campaign: RecurringForwardConfig) -> None:
        """Persist one recurring campaign."""
        await self._editor.upsert_campaign(campaign)
        await self._campaigns.upsert(campaign)

    async def delete_campaign(self, campaign_id: str) -> None:
        """Delete one recurring campaign."""
        await self._editor.delete_campaign(campaign_id)
        await self._campaigns.delete(campaign_id)

    async def set_campaign_enabled(self, campaign_id: str, enabled: bool) -> None:
        """Set recurring campaign enabled state."""
        await self._editor.set_campaign_enabled(campaign_id, enabled)
        await self._campaigns.set_enabled(campaign_id, enabled)
