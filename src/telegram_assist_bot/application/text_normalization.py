"""Minimal versioned normalization and deterministic content hashing."""

from __future__ import annotations

import hashlib
import json

NORMALIZATION_VERSION = 1
CONTENT_HASH_VERSION = 1


def normalize_exact_text(value: str | None) -> str | None:
    """Normalize only line endings and trailing horizontal whitespace.

    Persian/Arabic letters, ZWNJ, case, punctuation, combining marks, URLs and
    emoji are deliberately preserved.
    """
    if value is None:
        return None
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip(" \t") for line in lines)


def exact_content_hash(
    *, text: str | None, caption: str | None, media_hashes: tuple[str, ...]
) -> str:
    """Hash runtime-independent UTF-8 JSON with ordered media hashes."""
    payload = {
        "hash_version": CONTENT_HASH_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "text": normalize_exact_text(text),
        "caption": normalize_exact_text(caption),
        "media_hashes": list(media_hashes),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
