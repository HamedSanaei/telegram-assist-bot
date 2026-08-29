# Release Notes

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