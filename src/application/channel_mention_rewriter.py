"""Utilities for rewriting source-channel mentions before publishing."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from src.domain.entities import TextEntity

_TELEGRAM_LINK_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,})(?:/\d+)?",
    flags=re.IGNORECASE,
)
_TELEGRAM_HANDLE_RE = re.compile(
    r"(?<![\w@./:-])@([A-Za-z0-9_]{5,})(?![\w])",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class RewrittenText:
    """
    Text rewritten for a destination channel plus adjusted formatting entities.

    Attributes:
        text: Rewritten message text.
        entities: Entities whose offsets were adjusted to the rewritten text.
    """

    text: str
    entities: list[TextEntity]


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
    Replace source mentions and remove other Telegram handles/links.

    Args:
        text: Original post text.
        source_identifiers: Source channel identifiers from configuration.
        destination_public_id: Public destination id/link, for example
            ``"@my_channel"`` or ``"https://t.me/my_channel"``.

    Returns:
        Text with source ``@channel`` and ``t.me/channel`` references
        replaced by ``destination_public_id``. Other Telegram handles/links
        are removed so support or advertising ids are not republished. When
        no destination public id is configured, the text is returned
        unchanged.

    Example:
        rewrite_source_channel_mentions("از @source", ["@source"], "@dest")
    """
    return rewrite_source_channel_mentions_with_entities(
        text,
        [],
        source_identifiers,
        destination_public_id,
    ).text


def rewrite_source_channel_mentions_with_entities(
    text: str,
    text_entities: list[TextEntity],
    source_identifiers: list[str | int],
    destination_public_id: str,
) -> RewrittenText:
    """
    Replace source mentions, remove other Telegram ids, and align entities.

    Args:
        text: Original post text.
        text_entities: Formatting entities attached to ``text``.
        source_identifiers: Source channel identifiers from configuration.
        destination_public_id: Destination public id/link.

    Returns:
        Rewritten text and adjusted entities.
    """
    replacement_text = destination_public_id.strip()
    if not text or not replacement_text:
        return RewrittenText(text=text, entities=list(text_entities))

    rewritten = text
    entities = list(text_entities)
    handles = {
        handle.lower(): handle
        for handle in (_handle_from_identifier(identifier) for identifier in source_identifiers)
        if handle
    }
    for handle in handles.values():
        escaped = re.escape(handle)
        rewritten, entities = _replace_with_entity_shift(
            rewritten,
            entities,
            re.compile(rf"(?<![\w@])@{escaped}(?![\w])", flags=re.IGNORECASE),
            replacement_text,
        )
        rewritten, entities = _replace_with_entity_shift(
            rewritten,
            entities,
            re.compile(
                rf"(?:https?://)?(?:t\.me|telegram\.me)/{escaped}(?:/\d+)?",
                flags=re.IGNORECASE,
            ),
            replacement_text,
        )
    destination_handle = _handle_from_identifier(replacement_text)
    rewritten, entities = _remove_unwanted_telegram_mentions(
        rewritten,
        entities,
        protected_handle=destination_handle.lower() if destination_handle else None,
    )
    rewritten, entities = _normalize_cleanup_spacing(rewritten, entities)
    return RewrittenText(text=rewritten, entities=entities)


def _replace_with_entity_shift(
    text: str,
    entities: list[TextEntity],
    pattern: re.Pattern[str],
    replacement_text: str,
) -> tuple[str, list[TextEntity]]:
    """Apply repeated regex replacements while shifting entity offsets."""
    rewritten = text
    shifted = list(entities)
    search_from = 0
    while True:
        match = pattern.search(rewritten, search_from)
        if match is None:
            return rewritten, shifted
        start, end = match.span()
        old_length = end - start
        rewritten = f"{rewritten[:start]}{replacement_text}{rewritten[end:]}"
        shifted = _shift_entities_after_replacement(
            shifted,
            start=start,
            old_length=old_length,
            new_length=len(replacement_text),
        )
        search_from = start + len(replacement_text)


def _shift_entities_after_replacement(
    entities: list[TextEntity],
    start: int,
    old_length: int,
    new_length: int,
) -> list[TextEntity]:
    """
    Shift entity offsets after replacing a span of text.

    Entities that overlap the replaced span are dropped because their target
    text no longer exists at the same semantic location.
    """
    end = start + old_length
    delta = new_length - old_length
    shifted: list[TextEntity] = []
    for entity in entities:
        entity_end = entity.offset + entity.length
        if entity_end <= start:
            shifted.append(entity)
        elif entity.offset >= end:
            shifted.append(replace(entity, offset=entity.offset + delta))
    return shifted


def _remove_unwanted_telegram_mentions(
    text: str,
    entities: list[TextEntity],
    protected_handle: str | None,
) -> tuple[str, list[TextEntity]]:
    """
    Remove Telegram handles/links except the destination public handle.

    Args:
        text: Text after known source mentions were rewritten.
        entities: Text entities aligned with ``text``.
        protected_handle: Destination handle without ``@`` to preserve.

    Returns:
        Cleaned text and shifted entities.
    """
    cleaned, shifted = _remove_matches_except_handle(
        text,
        entities,
        _TELEGRAM_LINK_RE,
        protected_handle,
    )
    return _remove_matches_except_handle(
        cleaned,
        shifted,
        _TELEGRAM_HANDLE_RE,
        protected_handle,
    )


def _remove_matches_except_handle(
    text: str,
    entities: list[TextEntity],
    pattern: re.Pattern[str],
    protected_handle: str | None,
) -> tuple[str, list[TextEntity]]:
    """Remove repeated regex matches unless they target the protected handle."""
    rewritten = text
    shifted = list(entities)
    search_from = 0
    while True:
        match = pattern.search(rewritten, search_from)
        if match is None:
            return rewritten, shifted
        handle = match.group(1).lower()
        if protected_handle and handle == protected_handle:
            search_from = match.end()
            continue
        start, end = match.span()
        old_length = end - start
        rewritten = f"{rewritten[:start]}{rewritten[end:]}"
        shifted = _shift_entities_after_replacement(
            shifted,
            start=start,
            old_length=old_length,
            new_length=0,
        )
        search_from = start


def _normalize_cleanup_spacing(
    text: str, entities: list[TextEntity]
) -> tuple[str, list[TextEntity]]:
    """Collapse repeated spaces left behind after mention/link removal."""
    collapsed, shifted = _replace_with_entity_shift(
        text,
        entities,
        re.compile(r"[ \t]{2,}"),
        " ",
    )
    return _trim_outer_whitespace(collapsed, shifted)


def _trim_outer_whitespace(
    text: str, entities: list[TextEntity]
) -> tuple[str, list[TextEntity]]:
    """Trim leading/trailing whitespace while keeping entity offsets valid."""
    leading = len(text) - len(text.lstrip())
    if leading:
        text = text[leading:]
        entities = _shift_entities_after_replacement(
            entities, start=0, old_length=leading, new_length=0
        )
    trailing = len(text) - len(text.rstrip())
    if trailing:
        start = len(text) - trailing
        text = text[:start]
        entities = _shift_entities_after_replacement(
            entities, start=start, old_length=trailing, new_length=0
        )
    return text, entities
