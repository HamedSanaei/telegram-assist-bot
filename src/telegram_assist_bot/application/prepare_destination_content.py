"""Pure versioned destination text pruning with UTF-16 entity rebasing."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from telegram_assist_bot.application.content.entity_rebaser import TextEdit, apply_edits
from telegram_assist_bot.application.content.models import DestinationPreparedContent
from telegram_assist_bot.application.content.telegram_links import (
    telegram_reference_spans,
)

if TYPE_CHECKING:
    from telegram_assist_bot.domain.posts import TelegramEntity

CONTENT_POLICY_VERSION = 1
_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def prepare_destination_content(
    *,
    text: str,
    entities: tuple[TelegramEntity, ...],
    source_username: str,
    destination_username: str,
) -> DestinationPreparedContent:
    """Replace source, protect destination, and remove other Telegram references."""
    source = source_username.removeprefix("@")
    destination = destination_username.removeprefix("@")
    if not _USERNAME.fullmatch(source) or not _USERNAME.fullmatch(destination):
        raise ValueError("Telegram usernames are invalid.")
    edits: list[TextEdit] = []
    for span in telegram_reference_spans(text):
        folded = span.username.casefold()
        if folded == destination.casefold():
            continue
        replacement = f"@{destination}" if folded == source.casefold() else ""
        edits.append(TextEdit(span.start, span.end, replacement))
    transformed, rebased = apply_edits(text, entities, tuple(edits))
    whitespace_edits: list[TextEdit] = []
    for match in re.finditer(r"[ \t]{2,}|\n{3,}", transformed):
        replacement = " " if "\n" not in match.group() else "\n\n"
        whitespace_edits.append(TextEdit(match.start(), match.end(), replacement))
    transformed, rebased = apply_edits(transformed, rebased, tuple(whitespace_edits))
    return DestinationPreparedContent(transformed, rebased, CONTENT_POLICY_VERSION)
