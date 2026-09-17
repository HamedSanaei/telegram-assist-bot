# Release Notes

## v1.1.4 — Fair media cleanup and expired-approval media release

This patch release fixes two production media-cleanup defects observed in a
`1.1.3` deployment that used `media.retention_days = 1`: a large blocked backlog
that cleanup could never reach, and media pinned by approvals whose own approval
lifecycle had already expired.

### Fixed

- **Cleanup candidate starvation / head-of-line blocking.** Candidate selection
  is now an explicit two-class fairness policy. Expired records that never
  received a cleanup attempt (no `cleanup_next_check_at`) are selected first in
  `_id` order, and already deferred records whose retry instant has arrived are
  selected afterwards in due order. A bounded set of perpetually referenced
  records can therefore no longer consume every worker cycle and leave later
  expired, unreferenced media permanently unreachable, while `_id` keeps ordering
  deterministic inside each class. The class filters also keep both candidate
  queries bounded by the expired population instead of the (much larger) fresh
  media population; on a production-shaped dataset of 106,200 documents the
  retry class is an index-only seek (`keysExamined = 100`, no blocking sort) and
  the never-attempted class reads only its own 5,200 expired records.
- **Expired approvals pinning media for the Post retention window.** A completed
  approval delivery now protects media only while `approval_expired` is not
  `True`. The approval cleanup lifecycle writes `approval_expired = True` when
  the administrator action window ends, and such an approval no longer keeps the
  file alive merely because its Post is still inside the separate 14-day Post
  retention window. `approval_expired = False` and legacy documents without the
  field keep protecting media, nonterminal deliveries (`pending`, `claimed`,
  `retry`) keep protecting media, and fresh media, Album/preparation, active
  Publication, Schedule, Native Schedule and advertisement references are
  unchanged.
- **Cleanup observability.** Batch metrics now also report `reference_deferred`,
  the subset of `deferred` items that were deferred because a durable reference
  still protects the shared path, so operators can separate reference blocking
  from ordinary cleanup progress.

### Internal / safety

- Additive `ix_media_cleanup_fairness_v4` MongoDB index
  (`cleaned_at`, `cleanup_next_check_at`, `_id`), created idempotently; it serves
  the due-retry candidate class directly, without a blocking sort. The
  never-attempted class keeps using the existing versioned indexes, so only one
  new index is added; no existing index is dropped and no data migration is
  required.
- The cleanup worker stays bounded (`media.cleanup_batch_size`,
  `media.cleanup_max_batches_per_cycle`) and shutdown-safe; the fairness policy
  lives in the candidate queue, not in an unbounded loop.
- Post retention (14 days), approval retention, media retention, shared-path
  safety and all other durable reference protections are unchanged.
- No manual deletion, bulk `updateMany` or manual `cleaned_at` mutation is
  required; the existing cleanup path performs every deletion.

### Compatibility

- Existing `v1.1.3` installations can upgrade normally.
- MongoDB, media and session volumes are preserved and no
  `docker compose down --volumes` is required.
- Old configuration files remain compatible; no configuration key changed.

### Operator note: expected backlog behaviour after upgrade

Records that were already deferred become eligible again after
`media.cleanup_defer_seconds` (3600 by default). Records blocked only by an
already expired approval are released on their next attempt; the periodic worker
drains up to `media.cleanup_batch_size × media.cleanup_max_batches_per_cycle`
items per interval, and each `media-cleanup` one-shot command processes one
bounded batch. `reference_deferred` distinguishes blocked items in the batch
log, but it does not settle every reference that may appear between cycles.

In a `1.1.3` deployment that accumulated a `1.1.3`-era blocked backlog, rule 2
above means the freed records are only attempted again once
`media.cleanup_defer_seconds` has elapsed. A host `tabctl` that predates this
release must be updated too, otherwise use the runtime container command
`/app/.venv/bin/python -m telegram_assist_bot media-cleanup --config
/app/config/configuration.json` for a manual bounded run.

## v1.1.3 — Prevent unbounded media accumulation

This patch release hardens media storage cleanup so a running bot cannot
gradually accumulate unbounded media files and eventually fill the Docker
host disk.

### Fixed

- **Cleanup candidate starvation / head-of-line blocking.** Referenced
  candidates are now deferred (`cleanup_next_check_at`) instead of blocking the
  first page forever, so a busy leading page can no longer starve later expired,
  unreferenced media.
- **Media cleanup throughput ceiling.** The cleanup worker now drains up to
  `media.cleanup_max_batches_per_cycle` bounded batches per wake-up instead of
  exactly one, removing the 100 items/hour ceiling on active backlogs.
- **Canonical filesystem orphan cleanup.** The worker now scans the owned media
  root for canonical files whose metadata disappeared, bounded by
  `media.orphan_grace_seconds`, and deletes only validated, unreferenced
  canonical paths.
- **Preview storage escaping the managed media volume.** Previews now live
  inside the media volume (`<media.root>/.preview`) instead of the container
  writable layer, so they cannot silently accumulate outside managed storage.
- **Preview lifecycle cleanup.** When canonical media is cleaned, its
  preview artifacts are cleaned too (requires `media.preview_enabled`).
- **Cleanup observability.** Structured batch metrics now report `scanned`,
  `deleted`, `deferred`, `orphan_deleted`, `temporary_deleted` and `failed`, so
  operators can distinguish healthy deletion from all-deferred or empty runs.
- **Cleanup worker preview configuration wiring.** The cleanup composition now
  passes the real `media.preview_enabled` value into local storage so preview
  lifecycle cleanup actually runs in production.

### Internal / safety

- Additive `cleanup_next_check_at` field (legacy records remain immediately
  eligible).
- Additive `ix_media_cleanup_deferral_v3` MongoDB index, created idempotently;
  no existing indexes are dropped.
- Bounded multi-batch cleanup with a cooperative yield between batches, plus
  stop-event checks (no unbounded tight loop).
- Bounded canonical orphan scanning with path-shape and symlink/traversal
  protections and a final reference recheck before deletion.
- Docker logging limits are unchanged.
- No destructive migration; all changes are additive.

### Compatibility

- Existing `v1.1.2` installations can upgrade normally.
- MongoDB, media and session volumes are preserved.
- No `docker compose down --volumes` is required.
- Old configuration files remain compatible; the new cleanup options
  (`cleanup_max_batches_per_cycle`, `cleanup_defer_seconds`) receive safe
  defaults automatically.

### Operator note: legacy previews

If a `v1.1.2` installation had `"preview_enabled": true`, legacy preview files
may still exist under the old writable-layer location `data/media-preview`
(container path `/app/data/media-preview`). This release does **not**
automatically delete arbitrary legacy paths. Operators upgrading such an
installation may manually remove any generated previews from that legacy
location after confirming the preview volume moved to `<media.root>/.preview`.