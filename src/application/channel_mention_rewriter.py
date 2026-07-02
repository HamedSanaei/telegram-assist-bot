"""Utilities for rewriting source-channel mentions before publishing."""

from __future__ import annotations

import re


def _handle_from_identifier(identifier: str | int) -> str | None:
    """
    Extract a Telegram public username from a source identifier.

    Args:
        identifier: Source channel identifier from configuration. Supported
            forms include ``"@channel"``, ``"t.me/channel"``, and
            ``"https://t.me/channel"``. Numeric ids have no public handle.

    Returns:
        The username without ``@``, or ``None`` when no public username can
        be extracted.
    """
    value = str(identifier).strip()
    if not value:
        return None
    if value.startswith("@"):
        return value[1:] or None
    match = re.search(
        r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,})",
        value,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return None


def rewrite_source_channel_mentions(
    text: str,
    source_identifiers: list[str | int],
    destination_public_id: str,
) -> str:
    """
    Replace configured source-channel mentions with the destination id.

    Args:
        text: Original post text.
        source_identifiers: Source channel identifiers from configuration.
        destination_public_id: Public destination id/link, for example
            ``"@my_channel"`` or ``"https://t.me/my_channel"``.

    Returns:
        Text with source ``@channel`` and ``t.me/channel`` references
        replaced by ``destination_public_id``. When no destination public id
        is configured, the text is returned unchanged.

    Example:
        rewrite_source_channel_mentions("از @source", ["@source"], "@dest")
    """
    replacement = destination_public_id.strip()
    if not text or not replacement:
        return text

    rewritten = text
    handles = {
        handle.lower(): handle
        for handle in (_handle_from_identifier(identifier) for identifier in source_identifiers)
        if handle
    }
    for handle in handles.values():
        escaped = re.escape(handle)
        rewritten = re.sub(
            rf"(?<![\w@])@{escaped}(?![\w])",
            replacement,
            rewritten,
            flags=re.IGNORECASE,
        )
        rewritten = re.sub(
            rf"(?:https?://)?(?:t\.me|telegram\.me)/{escaped}(?:/\d+)?",
            replacement,
            rewritten,
            flags=re.IGNORECASE,
        )
    return rewritten
