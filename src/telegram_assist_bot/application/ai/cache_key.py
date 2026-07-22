"""Deterministic versioned cache identities for normalized AI inputs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from telegram_assist_bot.application.text_normalization import normalize_exact_text

if TYPE_CHECKING:
    from pydantic import BaseModel

    from telegram_assist_bot.application.ai.contracts import AITaskType

CACHE_KEY_CONTRACT_VERSION: Final[int] = 1
_LANGUAGE_PATTERN = re.compile(r"[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*")


@dataclass(frozen=True, slots=True)
class AICacheIdentity:
    """Complete stable identity and its one-way input fingerprint."""

    cache_key: str
    task_type: AITaskType
    input_hash: str
    prompt_version: str
    schema_version: str
    language: str
    key_version: int


def normalize_language_identifier(value: str) -> str:
    """Canonicalize an ASCII language identifier without touching content text."""
    if not _LANGUAGE_PATTERN.fullmatch(value):
        raise ValueError("language identifier is invalid")
    return value.replace("_", "-").lower()


def _normalize_value(value: object) -> object:
    if isinstance(value, str):
        return normalize_exact_text(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build_ai_cache_identity(
    *,
    task_type: AITaskType,
    request_context: BaseModel,
    prompt_version: str,
    schema_version: str,
    language: str,
    key_version: int = CACHE_KEY_CONTRACT_VERSION,
) -> AICacheIdentity:
    """Hash canonical UTF-8 input and all version dimensions deterministically."""
    if key_version < 1:
        raise ValueError("cache key version must be positive")
    normalized_language = normalize_language_identifier(language)
    normalized_input = _normalize_value(request_context.model_dump(mode="json"))
    input_hash = hashlib.sha256(_canonical_json(normalized_input)).hexdigest()
    key_payload = {
        "input_hash": input_hash,
        "key_version": key_version,
        "language": normalized_language,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "task_type": task_type.value,
    }
    cache_key = hashlib.sha256(_canonical_json(key_payload)).hexdigest()
    return AICacheIdentity(
        cache_key=cache_key,
        task_type=task_type,
        input_hash=input_hash,
        prompt_version=prompt_version,
        schema_version=schema_version,
        language=normalized_language,
        key_version=key_version,
    )


__all__ = (
    "CACHE_KEY_CONTRACT_VERSION",
    "AICacheIdentity",
    "build_ai_cache_identity",
    "normalize_language_identifier",
)
