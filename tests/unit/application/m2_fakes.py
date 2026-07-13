"""In-memory application-port fakes for Milestone 2 unit tests."""

from dataclasses import dataclass, field, replace
from datetime import datetime

from telegram_assist_bot.application.ports import (
    AlbumFinalizationStatus,
    DestinationArtifact,
    MediaGroup,
    MediaGroupMember,
)
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
)
from telegram_assist_bot.domain.duplicates import DuplicateCheckResult
from telegram_assist_bot.domain.media import MediaIdentity, StoredMedia
from telegram_assist_bot.domain.posts import PostId


@dataclass
class FakePreparationRepository:
    """Implement canonical idempotent assignments entirely in memory."""

    media: dict[str, StoredMedia] = field(default_factory=dict)
    groups: dict[str, MediaGroup] = field(default_factory=dict)
    duplicates: dict[str, DuplicateCheckResult] = field(default_factory=dict)
    categories: dict[str, CategorizationResult] = field(default_factory=dict)
    artifacts: dict[tuple[str, str], DestinationArtifact] = field(default_factory=dict)
    cleaned: set[str] = field(default_factory=set)
    ready: set[str] = field(default_factory=set)

    async def get_media(self, identity: MediaIdentity) -> StoredMedia | None:
        return self.media.get(identity.key)

    async def save_media_if_absent(self, media: StoredMedia) -> StoredMedia:
        return self.media.setdefault(media.identity.key, media)

    async def list_media_for_preview(self) -> tuple[StoredMedia, ...]:
        return tuple(self.media[key] for key in sorted(self.media))

    async def list_cleanup_candidates(
        self, *, now: datetime, orphan_before: datetime, limit: int
    ) -> tuple[StoredMedia, ...]:
        del orphan_before
        return tuple(
            item
            for item in self.media.values()
            if item.expires_at <= now and item.identity.key not in self.cleaned
        )[:limit]

    async def is_storage_path_referenced(
        self, storage_path: str, *, now: datetime
    ) -> bool:
        return any(
            item.storage_path == storage_path and item.expires_at > now
            for item in self.media.values()
        )

    async def mark_media_cleaned(
        self, identity: MediaIdentity, *, cleaned_at: datetime
    ) -> bool:
        del cleaned_at
        if identity.key in self.cleaned:
            return False
        self.cleaned.add(identity.key)
        return True

    async def add_group_member(
        self, group: MediaGroup, member: MediaGroupMember
    ) -> MediaGroup:
        current = self.groups.get(group.group_key, group)
        if current.finalized_at is not None or any(
            item.source_message_id == member.source_message_id
            for item in current.members
        ):
            return current
        current = replace(
            group,
            members=tuple(
                sorted(
                    (*current.members, member),
                    key=lambda item: (item.source_date, item.source_message_id),
                )
            ),
        )
        self.groups[group.group_key] = current
        return current

    async def observe_group_member(
        self, group: MediaGroup, *, source_message_id: int
    ) -> MediaGroup:
        current = self.groups.get(group.group_key, group)
        if current.finalized_at is not None:
            return current
        current = replace(
            group,
            members=current.members,
            observed_message_ids=tuple(
                sorted({*current.observed_message_ids, source_message_id})
            ),
        )
        self.groups[group.group_key] = current
        return current

    async def get_group(self, group_key: str) -> MediaGroup | None:
        return self.groups.get(group_key)

    async def finalize_group(self, group_key: str, *, at: datetime) -> bool:
        group = self.groups[group_key]
        if group.finalized_at is not None:
            return False
        self.groups[group_key] = replace(group, finalized_at=at)
        return True

    async def list_due_groups(
        self, *, now: datetime, limit: int
    ) -> tuple[MediaGroup, ...]:
        return tuple(
            sorted(
                (
                    group
                    for group in self.groups.values()
                    if group.finalized_at is None and group.finalize_after <= now
                ),
                key=lambda group: (group.finalize_after, group.group_key),
            )[:limit]
        )

    async def claim_due_group(
        self, *, now: datetime, owner: str, lease_until: datetime
    ) -> MediaGroup | None:
        candidates = sorted(
            (
                group
                for group in self.groups.values()
                if group.finalization_status
                not in {
                    AlbumFinalizationStatus.COMPLETED,
                    AlbumFinalizationStatus.PERMANENT_FAILED,
                }
                and group.finalize_after <= now
                and (group.next_attempt_at is None or group.next_attempt_at <= now)
                and (
                    group.claim_owner is None
                    or (
                        group.claim_expires_at is not None
                        and group.claim_expires_at <= now
                    )
                )
            ),
            key=lambda group: (group.finalize_after, group.group_key),
        )
        if not candidates:
            return None
        group = candidates[0]
        claimed = replace(
            group,
            finalization_status=AlbumFinalizationStatus.PROCESSING,
            attempt_count=group.attempt_count + 1,
            claim_owner=owner,
            claim_expires_at=lease_until,
        )
        self.groups[group.group_key] = claimed
        return claimed

    async def complete_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        at: datetime,
        canonical_source_message_id: int,
    ) -> bool:
        group = self.groups[group_key]
        if group.claim_owner != owner:
            return False
        self.groups[group_key] = replace(
            group,
            finalized_at=at,
            finalization_status=AlbumFinalizationStatus.COMPLETED,
            canonical_source_message_id=canonical_source_message_id,
            claim_owner=None,
            claim_expires_at=None,
            next_attempt_at=None,
            failure_category=None,
        )
        return True

    async def defer_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        failure_category: str,
    ) -> bool:
        group = self.groups[group_key]
        if group.claim_owner != owner:
            return False
        self.groups[group_key] = replace(
            group,
            finalization_status=AlbumFinalizationStatus.PENDING,
            next_attempt_at=next_attempt_at,
            failure_category=failure_category,
            claim_owner=None,
            claim_expires_at=None,
        )
        return True

    async def fail_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        at: datetime,
        failure_category: str,
    ) -> bool:
        del at
        group = self.groups[group_key]
        if group.claim_owner != owner:
            return False
        self.groups[group_key] = replace(
            group,
            finalization_status=AlbumFinalizationStatus.PERMANENT_FAILED,
            failure_category=failure_category,
            claim_owner=None,
            claim_expires_at=None,
            next_attempt_at=None,
        )
        return True

    async def find_duplicate(
        self, *, content_hash: str, post_id: PostId, since: datetime
    ) -> PostId | None:
        matches = [
            (key, result)
            for key, result in self.duplicates.items()
            if key != post_id.value
            and result.content_hash == content_hash
            and result.checked_at >= since
        ]
        return None if not matches else PostId(sorted(matches)[0][0])

    async def save_duplicate_result(
        self, post_id: PostId, result: DuplicateCheckResult
    ) -> DuplicateCheckResult:
        return self.duplicates.setdefault(post_id.value, result)

    async def get_duplicate_result(
        self, post_id: PostId
    ) -> DuplicateCheckResult | None:
        return self.duplicates.get(post_id.value)

    async def save_category_result(
        self, post_id: PostId, result: CategorizationResult
    ) -> CategorizationResult:
        current = self.categories.get(post_id.value)
        if (
            current is not None
            and current.method is CategorizationMethod.MANUAL
            and result.method is not CategorizationMethod.MANUAL
        ):
            return current
        self.categories[post_id.value] = result
        return result

    async def get_category_result(self, post_id: PostId) -> CategorizationResult | None:
        return self.categories.get(post_id.value)

    async def save_destination_artifact(
        self, artifact: DestinationArtifact
    ) -> DestinationArtifact:
        return self.artifacts.setdefault(
            (artifact.post_id.value, artifact.destination_id), artifact
        )

    async def get_destination_artifact(
        self, post_id: PostId, destination_id: str
    ) -> DestinationArtifact | None:
        return self.artifacts.get((post_id.value, destination_id))

    async def mark_preparation_ready(self, post_id: PostId, *, at: datetime) -> bool:
        del at
        if post_id.value in self.ready:
            return False
        self.ready.add(post_id.value)
        return True
