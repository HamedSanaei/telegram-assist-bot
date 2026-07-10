"""Atomic UTF-8 JSON editor for runtime-safe management settings."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable

from src.domain.entities import DestinationChannel
from src.shared.config import (
    CONFIG_PATH_ENV_VAR,
    DEFAULT_CONFIG_PATH,
    RecurringForwardConfig,
    load_configuration,
)
from src.shared.errors import ConfigurationError


class AtomicConfigurationEditor:
    """
    Persist channel and recurring-campaign edits without exposing secrets.

    The complete JSON object is preserved, changed under one process-wide
    lock, written to a sibling temporary file, and atomically replaced.
    """

    _lock = asyncio.Lock()

    def __init__(self, path: str | Path | None = None) -> None:
        """Args: path: Optional configuration path override."""
        self._path = Path(
            path or os.environ.get(CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH)
        )

    async def add_source(self, identifier: str) -> None:
        """Add a source identifier if absent."""
        identifier = identifier.strip()

        def mutate(data: dict[str, object]) -> None:
            telegram = self._object_section(data, "telegram")
            sources = list(telegram.get("source_channels", []))
            if identifier not in {str(value) for value in sources}:
                sources.append(identifier)
            telegram["source_channels"] = sources

        await self._update(mutate)

    async def remove_source(self, identifier: str) -> None:
        """Remove a source identifier from authoritative configuration."""
        normalized = identifier.strip().lower().lstrip("@")

        def mutate(data: dict[str, object]) -> None:
            telegram = self._object_section(data, "telegram")
            telegram["source_channels"] = [
                value
                for value in list(telegram.get("source_channels", []))
                if str(value).strip().lower().lstrip("@") != normalized
            ]

        await self._update(mutate)

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Insert or replace one destination channel object."""
        encoded = {
            "chat_id": channel.chat_id,
            "title": channel.title,
            "public_id": channel.public_id,
            "kind": channel.kind.value,
            "publish_usd_price": channel.publish_usd_price,
            "post_interval_minutes": channel.post_interval_minutes,
        }

        def mutate(data: dict[str, object]) -> None:
            telegram = self._object_section(data, "telegram")
            destinations = list(telegram.get("destination_channels", []))
            replaced = False
            for index, value in enumerate(destinations):
                if isinstance(value, dict) and int(value.get("chat_id", 0)) == channel.chat_id:
                    destinations[index] = encoded
                    replaced = True
                    break
            if not replaced:
                destinations.append(encoded)
            telegram["destination_channels"] = destinations

        await self._update(mutate)

    async def remove_destination(self, chat_id: int) -> None:
        """Remove one destination from authoritative configuration."""

        def mutate(data: dict[str, object]) -> None:
            telegram = self._object_section(data, "telegram")
            telegram["destination_channels"] = [
                value
                for value in list(telegram.get("destination_channels", []))
                if not isinstance(value, dict) or int(value.get("chat_id", 0)) != chat_id
            ]

        await self._update(mutate)

    async def list_campaigns(self) -> list[RecurringForwardConfig]:
        """Return currently configured recurring campaigns."""
        return list(load_configuration(self._path).scheduler.recurring_forwards)

    async def upsert_campaign(self, campaign: RecurringForwardConfig) -> None:
        """Insert or replace one recurring-forward campaign."""
        encoded = {
            "id": campaign.id,
            "enabled": campaign.enabled,
            "source_post_url": campaign.source_post_url,
            "destination_chat_ids": campaign.destination_chat_ids,
            "show_forward_header": campaign.show_forward_header,
            "times": campaign.times,
        }

        def mutate(data: dict[str, object]) -> None:
            scheduler = self._object_section(data, "scheduler")
            campaigns = list(scheduler.get("recurring_forwards", []))
            replaced = False
            for index, value in enumerate(campaigns):
                if isinstance(value, dict) and str(value.get("id", "")) == campaign.id:
                    campaigns[index] = encoded
                    replaced = True
                    break
            if not replaced:
                campaigns.append(encoded)
            scheduler["recurring_forwards"] = campaigns
            scheduler.setdefault("recurring_forward_lookahead_hours", 24)

        await self._update(mutate)

    async def delete_campaign(self, campaign_id: str) -> None:
        """Delete one recurring campaign from configuration."""

        def mutate(data: dict[str, object]) -> None:
            scheduler = self._object_section(data, "scheduler")
            scheduler["recurring_forwards"] = [
                value
                for value in list(scheduler.get("recurring_forwards", []))
                if not isinstance(value, dict) or str(value.get("id", "")) != campaign_id
            ]

        await self._update(mutate)

    async def set_campaign_enabled(self, campaign_id: str, enabled: bool) -> None:
        """Enable or disable one recurring campaign."""

        def mutate(data: dict[str, object]) -> None:
            scheduler = self._object_section(data, "scheduler")
            for value in list(scheduler.get("recurring_forwards", [])):
                if isinstance(value, dict) and str(value.get("id", "")) == campaign_id:
                    value["enabled"] = enabled
                    return
            raise ConfigurationError(f"Recurring campaign not found: {campaign_id}")

        await self._update(mutate)

    async def _update(
        self, mutator: Callable[[dict[str, object]], None]
    ) -> None:
        """Apply a mutator and atomically replace the UTF-8 JSON file."""
        async with self._lock:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfigurationError(f"Cannot edit configuration: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigurationError("Configuration root must be an object")
            mutator(data)
            temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                load_configuration(temp_path)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
            os.replace(temp_path, self._path)

    @staticmethod
    def _object_section(data: dict[str, object], name: str) -> dict[str, object]:
        """Return one mutable JSON object section."""
        section = data.get(name)
        if not isinstance(section, dict):
            raise ConfigurationError(f"Missing configuration section: {name}")
        return section
