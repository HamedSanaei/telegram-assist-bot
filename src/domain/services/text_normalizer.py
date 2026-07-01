"""Pure text normalization helpers used for cheap exact deduplication.

Normalization is only used to build a stable content hash. The original
post text is always stored unchanged, so Persian characters (including
zero-width non-joiners) are never lost in the stored content.
"""

from __future__ import annotations

import hashlib
import re

_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\u2060\ufeff"
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_hash(text: str) -> str:
    """
    Normalize text for hashing purposes only.

    Lowercases the text, removes zero-width characters, and collapses
    all whitespace runs into single spaces. Persian letters are kept
    exactly as-is.

    Args:
        text: Raw post text.

    Returns:
        The normalized text.

    Example:
        normalize_for_hash("سلام   دنیا") == "سلام دنیا"
    """
    cleaned = text.translate({ord(c): None for c in _ZERO_WIDTH_CHARS})
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    return cleaned.strip().lower()


def content_hash(text: str) -> str:
    """
    Compute a stable SHA-256 hash of the normalized text.

    Args:
        text: Raw post text.

    Returns:
        Hex digest of the normalized text, encoded as UTF-8.

    Example:
        content_hash("سلام  دنیا") == content_hash("سلام دنیا")
    """
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
