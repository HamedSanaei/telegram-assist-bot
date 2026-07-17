"""Unit tests for sanitized AI audit and side-effect contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.infrastructure.mongodb.ai_audit_repository import (
    ai_audit_event_to_document,
)
from telegram_assist_bot.shared.config import AiAuditConfig, AiCachePolicyConfig

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def test_audit_serialization_contains_only_allowlisted_safe_metadata() -> None:
    event = AIAuditEvent(
        event_id="event",
        event_type=AIAuditEventType.PROVIDER_ATTEMPT,
        job_id="job",
        post_id="post",
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        prompt_version="1",
        schema_version="1",
        occurred_at=NOW,
        provider_name="provider",
        model_name="model",
        success=False,
        failure_category="timeout",
        http_status=504,
    )
    serialized = json.dumps(
        ai_audit_event_to_document(event),
        ensure_ascii=False,
        default=str,
    )
    forbidden = (
        "secret-api-key",
        "Bearer token",
        "Authorization",
        "raw provider response",
        "user@example.com",
        "متن خصوصی پرامپت",
    )

    assert all(value not in serialized for value in forbidden)
    assert "raw_response" not in serialized
    assert "prompt_text" not in serialized


def test_raw_storage_is_structurally_disabled_and_retention_is_explicit() -> None:
    assert AiAuditConfig().raw_storage_enabled is False
    with pytest.raises(ValidationError):
        AiAuditConfig(enabled=True)
    with pytest.raises(ValidationError):
        AiAuditConfig(raw_storage_enabled=True)  # type: ignore[arg-type]


def test_enabled_cache_requires_explicit_positive_bounded_ttl() -> None:
    with pytest.raises(ValidationError):
        AiCachePolicyConfig(
            task=AITaskType.SCORING,
            enabled=True,
        )
    disabled = AiCachePolicyConfig(task=AITaskType.SCORING, enabled=False)
    assert disabled.ttl_seconds is None
