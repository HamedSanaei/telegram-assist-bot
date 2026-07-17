"""Validate the storage-independent post repository contract."""

from __future__ import annotations

import ast
import inspect
import sys
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, datetime, timedelta
from importlib.util import resolve_name
from pathlib import Path
from typing import cast

import pytest

from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InsertPostResult,
    InvalidPostRepositoryRequestError,
    PostConcurrencyConflictError,
    PostNotFoundError,
    PostRepository,
    PostRepositoryDataError,
    PostRepositoryError,
    PostRepositoryUnavailableError,
    PostTransitionRequest,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_PORTS_ROOT = _REPOSITORY_ROOT / "src" / "telegram_assist_bot" / "application" / "ports"
_PORTS_PACKAGE = "telegram_assist_bot.application.ports"


def _discovered_post() -> Post:
    received_at = datetime(2026, 7, 11, 8, 30, tzinfo=UTC)
    return Post(
        post_id=PostId("post-004"),
        source_identity=SourceMessageIdentity(-1001234567890, 412),
        source_channel_username="sample_channel",
        source_channel_display_name="کانال نمونه",
        original_content=OriginalPostContent(
            text="سلام\nخبر تازه 🧑‍💻",
            caption=None,
        ),
        source_published_at=received_at - timedelta(minutes=2),
        received_at=received_at,
    )


def _stored_post() -> Post:
    post = _discovered_post()
    return post.transition_to(
        PostStatus.STORED,
        expected_version=post.version,
        occurred_at=post.received_at + timedelta(seconds=1),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
        correlation_id="corr-t004",
    )


def test_insert_result_is_frozen_and_uses_exact_public_outcomes() -> None:
    assert tuple(InsertPostOutcome) == (
        InsertPostOutcome.CREATED,
        InsertPostOutcome.ALREADY_EXISTS,
        InsertPostOutcome.CONFLICT,
    )
    assert InsertPostOutcome.CREATED.value == "Created"
    assert InsertPostOutcome.ALREADY_EXISTS.value == "AlreadyExists"

    result = InsertPostResult(InsertPostOutcome.CREATED, PostId("canonical-post"))
    assert is_dataclass(result)
    assert result.outcome is InsertPostOutcome.CREATED
    with pytest.raises(FrozenInstanceError):
        result.outcome = InsertPostOutcome.ALREADY_EXISTS  # type: ignore[misc]


def test_insert_result_rejects_coerced_or_foreign_outcomes() -> None:
    with pytest.raises(InvalidPostRepositoryRequestError):
        InsertPostResult(
            cast("InsertPostOutcome", "Created"),
            PostId("canonical-post"),
        )


def test_transition_request_accepts_one_domain_validated_step() -> None:
    post = _stored_post()

    request = PostTransitionRequest(
        post=post,
        expected_version=0,
        expected_status=PostStatus.DISCOVERED,
    )

    assert request.post is post
    assert request.expected_version == 0
    assert request.expected_status is PostStatus.DISCOVERED
    with pytest.raises(FrozenInstanceError):
        request.expected_version = 1  # type: ignore[misc]


@pytest.mark.parametrize("expected_version", [-1, True, 1])
def test_transition_request_rejects_invalid_or_mismatched_versions(
    expected_version: int,
) -> None:
    with pytest.raises(InvalidPostRepositoryRequestError):
        PostTransitionRequest(
            post=_stored_post(),
            expected_version=expected_version,
            expected_status=PostStatus.DISCOVERED,
        )


def test_transition_request_rejects_invalid_post_or_expected_status() -> None:
    with pytest.raises(InvalidPostRepositoryRequestError):
        PostTransitionRequest(
            post=cast("Post", object()),
            expected_version=0,
            expected_status=PostStatus.DISCOVERED,
        )
    with pytest.raises(InvalidPostRepositoryRequestError):
        PostTransitionRequest(
            post=_stored_post(),
            expected_version=0,
            expected_status=cast("PostStatus", "Discovered"),
        )


def test_transition_request_rejects_initial_snapshot_without_history() -> None:
    malformed_post = _discovered_post()
    object.__setattr__(malformed_post, "version", 1)
    with pytest.raises(InvalidPostRepositoryRequestError):
        PostTransitionRequest(
            post=malformed_post,
            expected_version=0,
            expected_status=PostStatus.DISCOVERED,
        )


def test_transition_request_rejects_tail_that_does_not_match_expected_status() -> None:
    with pytest.raises(InvalidPostRepositoryRequestError):
        PostTransitionRequest(
            post=_stored_post(),
            expected_version=0,
            expected_status=PostStatus.STORED,
        )


@pytest.mark.parametrize(
    ("error_type", "expected_message"),
    [
        (
            PostRepositoryUnavailableError,
            "Post persistence is temporarily unavailable.",
        ),
        (
            PostRepositoryDataError,
            "Persisted post data is invalid or unsupported.",
        ),
        (PostNotFoundError, "The requested post does not exist."),
        (
            PostConcurrencyConflictError,
            "The post changed before the transition was persisted.",
        ),
        (
            InvalidPostRepositoryRequestError,
            "The post repository request is invalid.",
        ),
    ],
)
def test_repository_errors_are_safe_and_driver_independent(
    error_type: type[PostRepositoryError],
    expected_message: str,
) -> None:
    error = error_type()

    assert isinstance(error, PostRepositoryError)
    assert str(error) == expected_message
    assert error.args == (expected_message,)
    assert error.__cause__ is None
    assert vars(error) == {}


def test_repository_protocol_declares_only_task_owned_operations() -> None:
    operations = {
        name
        for name, value in vars(PostRepository).items()
        if inspect.isfunction(value) and not name.startswith("_")
    }
    assert operations == {
        "get_by_id",
        "get_by_source_identity",
        "insert_idempotently",
        "list_unexpired",
        "transition",
        "claim_for_next_stage",
    }
    assert getattr(PostRepository, "_is_runtime_protocol", False)


def _absolute_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        assert node.module is not None
        return node.module
    relative_path = path.relative_to(_PORTS_ROOT).with_suffix("")
    module_parts = (
        relative_path.parts[:-1] if path.name == "__init__.py" else relative_path.parts
    )
    package = ".".join((_PORTS_PACKAGE, *module_parts))
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    return resolve_name("." * node.level + (node.module or ""), package)


def test_application_port_sources_do_not_import_infrastructure_or_drivers() -> None:
    violations: list[str] = []
    for path in sorted(_PORTS_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = (_absolute_import(path, node),)
            for module in modules:
                root = module.partition(".")[0]
                allowed = (
                    root in sys.stdlib_module_names
                    or root == "__future__"
                    or root == "pydantic"
                    or (
                        module in {"telegram_assist_bot.domain", _PORTS_PACKAGE}
                        or module.startswith(
                            (
                                "telegram_assist_bot.domain.",
                                f"{_PORTS_PACKAGE}.",
                                "telegram_assist_bot.application.ai",
                            )
                        )
                    )
                )
                if not allowed:
                    line_number = cast("int", getattr(node, "lineno", 0))
                    violations.append(f"{path.name}:{line_number}: {module}")

    assert not violations, "\n".join(violations)


def test_ports_package_public_api_is_complete_and_documented() -> None:
    from telegram_assist_bot.application import ports

    expected_exports = {
        "AIProvider",
        "AlbumFinalizationStatus",
        "AdminMessagingGateway",
        "ApprovalContent",
        "ApprovalAdministratorDeliveryState",
        "ApprovalDeliveryError",
        "ApprovalDeliveryRateLimitError",
        "ApprovalDeliveryRejectedError",
        "ApprovalDeliveryTransientError",
        "ApprovalDeliveryUnavailableError",
        "ApprovalMedia",
        "ApprovalMediaNetworkError",
        "ApprovalMediaPathError",
        "ApprovalMediaRejectionReason",
        "ApprovalMediaRejectedError",
        "ApprovalMediaUploadTimeoutError",
        "ApprovalDeliveryClaim",
        "ApprovalPost",
        "ApprovalPostLoader",
        "ApprovalRepository",
        "ApprovalSyncClaim",
        "BotEditOutcome",
        "BotUpdate",
        "Clock",
        "ContentPreparationRepository",
        "DestinationArtifact",
        "DestinationPublicationState",
        "InsertPostOutcome",
        "InsertPostResult",
        "InlineButton",
        "InlineKeyboard",
        "InvalidMediaGroupRecordError",
        "InvalidPostRepositoryRequestError",
        "MediaDownloadSpec",
        "MediaGroup",
        "MediaGroupMember",
        "MediaOperationError",
        "MediaPermanentError",
        "MediaRateLimitError",
        "MediaSource",
        "MediaStorage",
        "MediaTooLargeError",
        "MediaTransientError",
        "NativeScheduleCommand",
        "NativeScheduleReceipt",
        "NativeScheduleRepository",
        "NativeScheduleStatus",
        "NativeScheduledMessage",
        "PostConcurrencyConflictError",
        "PostClaimOutcome",
        "PostClaimRequest",
        "PostClaimResult",
        "PostNotFoundError",
        "PostRepository",
        "PostRepositoryDataError",
        "PostRepositoryError",
        "PostRepositoryUnavailableError",
        "PostTransitionRequest",
        "OperationalApprovalRepository",
        "PublicationClaimOutcome",
        "PublicationClaimResult",
        "PublicationMedia",
        "PublicationPayload",
        "PublicationPayloadLoader",
        "PublicationRepository",
        "PublisherError",
        "ResolvedTelegramChannel",
        "ScheduleRepository",
        "ScheduleReservation",
        "TelegramAccount",
        "TelegramAuthenticationGateway",
        "TelegramChannelNotFoundError",
        "TelegramChannelPermissionError",
        "TelegramChannelReference",
        "TelegramChannelRole",
        "TelegramGatewayError",
        "TelegramHistoryGateway",
        "TelegramHistoryPage",
        "TelegramHistoryQuery",
        "TelegramInvalidCodeError",
        "TelegramInvalidPasswordError",
        "TelegramLiveGateway",
        "TelegramLiveSubscription",
        "TelegramLoginStep",
        "TelegramMessageMappingError",
        "TelegramMediaReference",
        "TelegramNativeSchedulerGateway",
        "TelegramOperationTimeoutError",
        "TelegramPublisherGateway",
        "TelegramRateLimitError",
        "TelegramSessionInvalidError",
        "TelegramSessionMutationConflictError",
        "TelegramSessionStatus",
        "TelegramSourceGateway",
        "TelegramTextMessage",
        "TelegramTransientError",
        "TelegramValidationGateway",
    }

    assert inspect.getdoc(ports)
    assert isinstance(ports.__all__, tuple)
    assert set(ports.__all__) == expected_exports
    assert len(ports.__all__) == len(set(ports.__all__))
    for name in ports.__all__:
        exported = getattr(ports, name)
        assert inspect.getdoc(exported), name
