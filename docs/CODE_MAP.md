# Code Map

## Project Purpose

A Telegram channel administration and publishing system. It collects posts
from configured sources, discovers proxy configs across joined dialogs,
deduplicates and classifies content with a configurable AI provider chain,
tests vmess/vless configs from an Iran-based worker, routes posts through
an admin approval bot, manages recurring native-schedule campaigns, publishes
approved posts to destination channels, and posts USD price updates twice per day.

## Architecture Overview

Clean Architecture. Dependencies point inward only:

```text
presentation -> application -> domain
workers      -> application -> domain
infrastructure -> application/domain interfaces
```

The `domain` layer uses only the Python standard library. All external
integrations (Telegram, MongoDB, SQLite, AI HTTP APIs, xray) are behind
`Protocol` interfaces defined in `src/domain/interfaces.py` and implemented
in `src/infrastructure/`. `src/composition.py` is the composition root that
wires concrete implementations; entrypoints (`src/main.py`, workers) only
assemble and run.

## Folder Structure

```text
src/
  domain/          Entities, enums, interfaces, pure services (parsers).
  application/     Use cases orchestrating domain interfaces.
  infrastructure/  SQLite/MongoDB repositories, AI providers, Telegram
                   publisher/scheduler adapters, VPN testers, price source.
  presentation/    Approval bot UI (keyboards, callback handlers, notifier).
  workers/         Queue worker, scheduler, collector, Iran VPN worker.
  shared/          Config loading, custom errors, UTF-8-safe logging.
  composition.py   Composition root (dependency wiring).
  main.py          Main process entrypoint (approval bot + queue + scheduler).
  run_all.py       All-in-one entrypoint: supervises main.py and the
                   collector together in a single process.
tests/             Unit tests (pytest, asyncio auto mode) + integration dir.
docs/              This code map and the run guide.
config/            configuration.example.json template.
deploy/            systemd service templates.
scripts/           build_publish.py, PyInstaller entry launchers,
                   install_ubuntu.sh (bundled into the Ubuntu archive).
publish/           GENERATED build outputs (git-ignored): windows/ exes,
                   ubuntu/ tar.gz bundle, BUILD_INFO.txt.
build/             GENERATED PyInstaller work files (git-ignored).
```

## Domain Layer

- `enums.py` — `PostCategory`, `VpnProtocol`, `VpnTestStatus`, `ChannelKind`,
  `QueueItemType`, `QueueStatus`, `MediaKind`.
- `entities.py` — `Post`, `TextEntity`, `MediaItem`, `VpnConfig`, `DestinationChannel`,
  `AdminUser`, `QueueItem`, `PublishRecord`, `DollarPrice`,
  `PostSourceMetrics`, `PostQualityScore`.
- `interfaces.py` — all ports: `AiProvider`, `PostRepository`,
  `QueueRepository`, `ChannelRepository`, `AdminRepository`,
  `PublishLogRepository`, `PriceHistoryRepository`, `VpnConnectivityTester`,
  `MessagePublisher`, `ScheduledMessagePublisher`, `SourceMetadataRefresher`,
  `ApprovalNotifier`, `PriceSource`, plus result value objects
  (`AiClassificationResult`, `DuplicateCheckResult`,
  `QualityScoreResult`, `VpnTestResult`).
- `services/text_normalizer.py` — normalization + SHA-256 content hash for
  cheap exact deduplication (original text is never altered).
- `services/text_fingerprint.py` — local token/shingle similarity used to
  preselect near-duplicate candidates and reduce AI prompt size.
- `services/vpn_parser.py` — pure stdlib parsing/extraction for `vmess`,
  `vless`, `ss`, `ssr`, `trojan`, `hysteria2`/`hy2`, and `tuic` URIs.

## Application Layer

- `ai_service.py` — `AiService`: runs combined classification,
  advertisement pruning, VPN discovery text cleanup, duplicate analysis,
  standalone duplicate checks, and advisory quality scoring through configured providers in priority
  order. Providers that return quota/rate/payment/temporary failures
  (`429`, `402`, `403`, `5xx`, timeout) are put into cooldown so backfill
  bursts immediately move to the next provider.
- `collect_post.py` — `CollectPostUseCase.handle_new_message`: source
  identity repair → exact hash dedup → local fuzzy duplicate filtering →
  combined AI classification/ad pruning with at most five local duplicate
  candidates → VPN config extraction → store with 14-day expiry and source
  metrics → enqueue immediate `approval_request`, collector-owned
  `source_metrics_refresh` at source time +20 minutes, and optional VPN test.
  Source-identity inserts are atomic, so a live/backfill race reuses the
  stored winner without duplicate AI, queue, or approval work.
  Duplicate, advertisement, and
  irrelevant posts are stored with `skipped_reason` so restarts do not keep
  redownloading or reprocessing them.
- `source_metrics_service.py` — collector-side delayed metrics refresh using
  the source-reading Telethon session; it hands refreshed or stored metrics
  to the main quality queue.
- `runtime_lease_service.py` — cross-host runtime ownership for the
  `bot-polling` and `collector` roles. Lease identities are one-way hashes of
  Telegram configuration values; a heartbeat stops the guarded component if
  ownership or Mongo connectivity becomes unsafe.
- `quality_score_service.py` — idempotent background advisory scoring stage.
  It asks AI for a 0-to-100 score and the approval service edits existing
  previews with their current keyboards, without gating or resending them.
- `management_service.py` — application boundary for approval-bot panel
  edits, persisting live channels and recurring campaign configuration.
- `vpn_test_service.py` — `VpnTestService`: tests each config via the
  tester port; post is eligible when at least one config works from Iran.
- `approval_service.py` — `ApprovalService`: approval requests, admin
  validation, direct immediate publish/delete toggles, direct native
  schedule/unschedule toggles, and duplicate-prevention via the publish log.
  It records approval-bot message references and reserves a post/channel
  pair before Telegram actions so multiple admins cannot race the same
  destination. The `approval_requests` table is the approval-preview
  idempotency boundary: startup never resends posts that already entered the
  approval stage, even if old message refs are inactive.
- `price_service.py` — `UsdPriceService` + `format_price_message` (Persian
  message with 🔺/🔻 change vs. previous record).
- `cleanup_service.py` — `CleanupService`: TTL safety net + queue expiry.

## Infrastructure Layer

- `db/sqlite/connection.py` — `Database` wrapper over aiosqlite (WAL mode).
- `db/sqlite/migrations.py` — versioned migrations in `MIGRATIONS`,
  tracked in `schema_migrations`; applied on every startup.
- `db/sqlite/repositories.py` — `SqliteChannelRepository` (destination
  channels plus resolved source-channel labels), `SqliteAdminRepository`,
  `SqliteQueueRepository` (atomic claim via conditional
  `UPDATE ... RETURNING` with per-worker type filtering),
  `SqliteApprovalRequestRepository`,
  `SqliteApprovalMessageRepository`, `SqlitePublishLogRepository`,
  `SqliteRecurringForwardOccurrenceRepository`, `SqlitePriceHistoryRepository`.
- `configuration/atomic_editor.py` — locked UTF-8 JSON updates through a
  temporary sibling and atomic replace, preserving secrets and unknown keys.
- `db/mongo/post_repository.py` — `MongoPostRepository` (Motor); TTL index
  on `expires_at`, lookup index on `content_hash`, and unique source
  identity index on `source_chat_id + source_message_id + grouped_id`.
  `insert_if_absent` converts cross-process identity races into a benign
  stored-winner result.
- `db/mongo/runtime_lease_repository.py` — atomic Mongo-backed runtime lease
  acquisition, owner-scoped heartbeat/release, and TTL cleanup of expired
  leases. It prevents Windows, Ubuntu, and standalone entrypoints from
  polling the same bots or collecting with the same session concurrently.
- `ai/openai_compatible.py` — shared OpenAI-compatible chat-completions
  client with strict JSON prompts for combined analysis (classification,
  duplicate, advertisement), classification, duplicate checks, and
  0-to-100 quality scoring. `src/composition.py` builds this client for each enabled
  `ai.providers` entry (Google AI Studio, Groq, OpenRouter, DeepSeek by
  default; z.ai is present but disabled in the template). HTTP error
  messages include the provider, model name, and API response body.
  OpenRouter entries may set `fallback_models` and `route: "fallback"` so
  OpenRouter handles model fallback inside one provider request.
  `ai/zai_provider.py` and `ai/deepseek_provider.py` remain compatibility
  wrappers for older direct construction.
- `telegram/publisher.py` — `AiogramMessagePublisher` (text, or first
  photo/video/document with caption; long text sent as a follow-up message)
  plus deletion of bot-published channel messages.
- `telegram/telethon_publish.py` — `TelethonDestinationPublisher` publishes
  approved posts immediately and uploads native scheduled messages through
  a Telethon user session, preserving stored custom emoji entities via
  `formatting_entities`. It also deletes immediate/scheduled messages for
  approval toggles. It reconnects the user session before MTProto requests
  and retries once after transient disconnects. `TelethonSourceMetadataRefresher`
  refreshes views/forwards/replies/reactions from the collector process using
  the same reconnect guard. The destination adapter also copies or forwards
  source-post URLs into native Telegram schedules for recurring campaigns.
- `vpn/worker_client.py` — `IranWorkerVpnTester`: HTTP client for the Iran
  worker API (bearer token auth).
- `vpn/xray_tester.py` — `XrayVpnTester`: spawns xray with a temp config,
  probes a test URL through the local SOCKS5 inbound. Used by the Iran
  worker only. Supports tcp/ws/grpc + tls/reality.
- `price/http_price_source.py` — `HttpJsonPriceSource`: configurable URL +
  dotted JSON path.

## Presentation Layer

- `approval_bot/keyboards.py` — inline keyboards with two buttons per
  destination channel: `🚀 فوری` and `⏱ اسکجول`. Active buttons show ✅ and
  stay clickable so a second click deletes/unschedules the real Telegram
  message.
- `approval_bot/handlers.py` — `create_approval_router(approval_service,
  timezone_name)`: direct `apv:pub:<post_id>:<chat_id>` and
  `apv:sch:<post_id>:<chat_id>` callbacks without confirmation. Every
  callback is acknowledged before slow Telegram uploads, validates admin,
  post, channel, and current publish state, then
  refreshes all admins' keyboards best-effort.
- `approval_bot/notifier.py` — `AiogramApprovalNotifier`: sends the Persian
  preview with a readable source-channel label, required 0-to-100 quality-score
  header, the first downloaded photo/video/document when present, and
  keyboard to every configured admin; returns the message id that owns each
  inline keyboard, obeys Telegram retry-after responses, and retries video
  previews as documents when Bot API rejects `send_video`. Score/VPN refresh
  edits always include the current inline keyboard; transient failures retry
  without deactivating the tracked message. Legacy references use
  `preview_kind=unknown`; the notifier infers text/caption, falls back once
  on Telegram body-type mismatch, and persists the corrected type.
- `approval_bot/propagation.py` — best-effort keyboard refresh helpers used
  after publish/schedule callbacks and runtime config reloads. Telegram's
  harmless `message is not modified` response is treated as a successful
  no-op; stale or deleted approval messages are marked inactive instead of
  failing callbacks.
- `approval_bot/panel.py` — admin-only `/panel` callback UI with paged
  source/destination lists and recurring campaign add/edit/toggle/delete
  wizards. Every action revalidates the admin id.
- `main_bot/handlers.py` — admin-only management commands for the main bot:
  reports (`/start`, `/status`, `/sources`, `/destinations`) and channel
  management (`/addsource`, `/delsource`, `/adddest`, `/deldest`,
  `/setdest <chat_id> <field> <value>`, `/setinterval <chat_id> <minutes>`).
  Channel edits are written to SQLite, but runtime config hot reload is
  authoritative for channel/admin lists and can overwrite command edits.

## Workers

- `queue_worker.py` — `QueueWorker`: polls SQLite queue, claims atomically
  only from the types registered by that worker,
  dispatches by `QueueItemType` (`source_metrics_refresh`,
  `quality_score_update`, legacy `quality_score`, `vpn_test`,
  `approval_request`, legacy `scheduled_publish`), retries with linear backoff up to `max_attempts`,
  then marks failed. Safe to restart at any time.
  Native scheduled approval actions upload immediately to Telegram's own
  channel schedule at a slot five minutes after the latest scheduled or
  published post for that destination.
- `scheduler.py` — APScheduler cron jobs: USD price at configured times
  (default 09:00 and 21:00 Asia/Tehran) and daily cleanup.
- `recurring_forward.py` — rolling native Telegram schedule reconciler. It
  fills the lookahead window idempotently and removes future occurrences
  made obsolete by campaign disable, deletion, or editing.
- `config_sync.py` — watches `configuration.json` mtime and hot-reloads
  only runtime-safe lists (`source_channels`, `destination_channels`,
  `admin_user_ids`) into SQLite. Invalid/half-written config files are
  logged and ignored.
- `collector.py` — Telethon-based listener on source channels; stores
  resolved source titles/usernames, downloads photos/videos/documents to
  `storage.media_directory`, and feeds `CollectPostUseCase`. On
  startup it also scans messages from the first post of the current
  Gregorian day in `scheduler.timezone` for each source so restarts and
  missed live events still enter the normal dedup/classify/store pipeline.
  Same-day backfill uses the union of config sources and SQLite-enabled
  sources, tolerates one source history failure without stopping the rest,
  and is processed oldest-first within each source and round-robin across
  sources so a busy first channel does not starve the rest of the
  configured source list. Before downloading media, the collector asks the
  ingestion use case whether the stored source identity already has enough
  media. Already complete source messages are not downloaded again, but
  text-only stored posts can repair missing video/photo attachments on the
  next same-day backfill. Stored posts still pass through the use case so
  missing `source_metrics_refresh`, `quality_score_update`, `vpn_test`, or
  `approval_request` queue stages
  can be repaired.
  Media downloads use a file-size-aware timeout capped at ten minutes and
  retry once; incomplete state is stored so later catch-up can repair it.
  Source identities are locked across live/backfill paths before media or AI.
  It reloads the source channel list from SQLite every
  `telegram.source_refresh_seconds` seconds so sources added or removed via
  the management bot are picked up without a restart; new ones are
  backfilled from today's first message. Albums are
  collected as one post. It also captures Telegram-side metrics (`views`,
  `forwards`, reply count, reaction count, source post date) and custom
  emoji text entities. A collector-only queue worker refreshes engagement
  metrics after 20 minutes with this same source session. Runs as its own process
  (`python -m src.workers.collector`) or inside the all-in-one entrypoint.
  Note: only the first media file of a post is republished currently.
  It also discovers config-bearing posts across all joined channel/group
  dialogs except configured sources/destinations. The discovery path uses
  local URI prefiltering and config fingerprints, AI ad cleanup with
  protected placeholders, immediate VPN-only approval, and no quality score.
- `src/run_all.py` — all-in-one entrypoint (`python -m src.run_all`): runs
  `src.main.run` and the collector concurrently in one event loop, each
  under `supervise()` which restarts a crashed component after a delay and
  permanently stops (only) a component whose configuration is invalid.
  Before either component starts, it atomically acquires both distributed
  runtime leases; a second instance exits without polling or backfill.
- `iran_vpn_worker.py` — FastAPI app on the Iran server. `POST /api/test`
  (bearer token) receives `{"raw": "<vmess|vless URI>"}` and returns
  `{"working", "latency_ms", "error"}`. `GET /api/health` is a liveness probe.

## Databases

- **SQLite** (`data/app.db`): `destination_channels`, `source_channels`
  (including resolved title/username/chat id), `admins`, `queue_items`,
  `approval_requests`, `approval_messages`, `publish_log` (UNIQUE
  post/channel pair plus reservation status),
  `recurring_forward_campaigns`, `recurring_forward_campaign_times`,
  `recurring_forward_campaign_destinations`,
  `recurring_forward_occurrences`, `recurring_forward_messages`,
  `price_history`, `settings`, `error_log`, `schema_migrations`.
- **MongoDB** (`posts` collection): full post documents with text, media
  metadata, source identity, AI results, duplicate/skipped state, source
  metrics, quality-score status, ingestion mode, VPN fingerprints/configs, `collected_at`,
  `expires_at` (TTL index deletes after 14 days).
- **MongoDB** (`runtime_leases` collection): short-lived ownership records
  for bot polling and collector roles. No Telegram token or API hash is
  stored; only hashed lease ids and operational owner metadata are persisted.

## Configuration

`config/configuration.json` (git-ignored; template in
`configuration.example.json`). Loaded by `src/shared/config.py` into frozen
dataclasses. Per-entrypoint validators: `validate_main_app_config`,
`validate_collector_config`, `validate_worker_config`. Path override via the
`TELEGRAM_ADMIN_BOT_CONFIG` environment variable. Collector same-day startup
scan is capped by `telegram.collector_daily_backfill_max_messages`, and
runtime source reloads by `telegram.source_refresh_seconds`. Each runtime
source refresh also performs a lightweight current-day catch-up scan
(maximum 300 recent messages per known source) to recover source-channel
updates missed during Telethon reconnect/difference sync. Destination
`public_id` values are used to replace configured source-channel mentions
before publishing or native scheduling to each selected destination. After
that replacement, remaining Telegram `@username`, `t.me/...`, and
`telegram.me/...` references are removed unless they point to the
destination public id.
`telegram.scheduler_session` is the Telethon destination user session used
for approved immediate publishing, native Telegram channel scheduling, and
custom emoji preservation; the user account must be an admin in destination
channels.

AI settings use a priority-ordered `ai.providers` list. The default template
orders Google AI Studio, Groq, OpenRouter, and DeepSeek, while keeping z.ai
as a disabled entry. Providers with `enabled: false`, missing keys, missing
models, or missing base URLs are skipped. OpenRouter-specific
`fallback_models` are sent as OpenRouter's `models` routing list.

Channel, admin, and recurring campaign lists in the config file are
**authoritative at runtime**:
`sync_config_to_sqlite` upserts configured rows, disables missing channels,
replaces admins, and atomically mirrors normalized campaign definitions.
`ConfigSyncWorker` repeats that sync whenever the file mtime changes, so
adding/removing configured channels or campaigns does not require a restart.
Secrets, AI providers, DB paths, Telegram sessions, scheduler phone, and
price settings remain restart-only.
`post_interval_minutes` is retained for compatibility, but native scheduled
post slots are currently paced every five minutes by product requirement.

## Telegram Bots

- **Main bot** (`telegram.bot_token`) — handles admin management commands
  and simple USD/fallback text publishing.
- **Approval bot** (`telegram.approval_bot_token`) — talks only to admins;
  long polling runs inside `src.main`.
- **Collector user session** (`telegram.api_id`/`api_hash`) — Telethon user
  account that reads the source channels.
- **Scheduler/destination user session** (`telegram.scheduler_session`) —
  Telethon user account that immediately publishes approved posts, uploads
  approved posts into the destination channel's native Telegram schedule,
  preserves premium custom emoji entities, and can refresh source metrics
  when it has source access.
  If this session is not authorized and `telegram.scheduler_phone` is empty,
  `src.main` prompts for the phone number interactively, then Telethon asks
  for the login code and creates the session.

## VPN Testing Worker

Main server enqueues `vpn_test` → `QueueWorker` calls `VpnTestService` →
`IranWorkerVpnTester` POSTs the raw URI to the Iran worker → the worker
parses it and runs `XrayVpnTester` → result is stored per-config in MongoDB.
Eligible posts (≥1 working config) continue to the approval queue; others
retain their test result. Approval is no longer blocked by this background
test; unsupported discovery protocols are marked `unsupported`.

## Scheduled Jobs

Configured in `scheduler` section: `usd_price_publish_times` (twice daily),
`cleanup_time` (daily), `recurring_forward_lookahead_hours`, and
`recurring_forwards`, all in `scheduler.timezone` (default Asia/Tehran).
The USD price source is selected by `usd_price.provider` via
`create_price_source()` in `src/composition.py`: `"nobitex"` (default,
`src/infrastructure/price/nobitex_price_source.py`, public USDT/RLS
market-stats endpoint converted to Toman) or `"http_json"` (generic
`src/infrastructure/price/http_price_source.py`).

## Observability

Every entrypoint logs a non-secret `Effective configuration` summary at
startup (`log_startup_summary()` in `src/shared/config.py`): counts of
channels/admins, provider names, whether each secret is set/EMPTY, database
targets. Each collected message then logs a per-stage chain (received →
duplicate/ad check → classified → saved to MongoDB → quality score →
approval/VPN queue item done) so a stalled pipeline can be bisected from the logs; the exact lines are documented in the
Troubleshooting section of `docs/RUNNING.md`. The approval bot answers
`/start` with the caller's admin status.
Console logs color AI provider/model usage green, AI provider failures
orange, and application errors red. File logs remain plain UTF-8. Every
process start also creates `logs/YYYYMMDD-HHMMSS-<entrypoint>.log` beside
the stable configured log file.

## Tests

`tests/unit/` mirrors the layers; fakes for all ports live in
`tests/unit/application/fakes.py`. Covered: vmess/vless parsing, extraction
from Persian text, UTF-8/Mojibake safety, hash dedup, AI fallback (both
directions), AI chain fallback, required 0-to-100 quality scoring,
advertisement pruning, classification routing, source identity repair,
media-once collection, approval validation and duplicate publish prevention,
native scheduled publishing slots, VPN
eligibility, queue claim/retry/expiry semantics, price change formatting,
14-day expiration, config loading/validation.
`tests/integration/` is reserved for MongoDB/Telegram/AI integration tests.
Run with `pytest`, `pytest tests/unit`, or `pytest tests/integration`.

## Deployment

`deploy/telegram-suite.service` (all-in-one: main app + collector via
`src.run_all`), `deploy/telegram-admin-bot.service` (main process only),
`deploy/telegram-collector.service` (collector only),
`deploy/iran-vpn-worker.service` (Iran server). Enable either the suite
service or the two separate ones, never both. All run from
`/opt/telegram-admin-bot` with `PYTHONIOENCODING=utf-8`.

**Publish outputs** — `python scripts/build_publish.py` must be re-run after
every meaningful change (see the Publish Output Requirement in CLAUDE.md).
It regenerates `publish/windows/` (one-file PyInstaller executables:
`telegram-suite.exe`, `telegram-admin-bot.exe`, `telegram-collector.exe`,
`iran-vpn-worker.exe` plus the config template),
`publish/ubuntu/telegram-admin-bot-<version>.tar.gz`
(source bundle with `install.sh`), and `publish/BUILD_INFO.txt`. Executables
can only be built on Windows; `--skip-exe` builds the Ubuntu bundle only.

## How to Add a New Feature

1. Add/extend domain entities or enums in `src/domain/`.
2. Define any new port as a `Protocol` in `src/domain/interfaces.py`.
3. Add or extend a use case in `src/application/`.
4. Implement the port in `src/infrastructure/`.
5. Wire it in `src/composition.py` and the relevant entrypoint.
6. Add Telegram handlers/worker entrypoints only at the edge
   (`src/presentation/`, `src/workers/`).
7. Update `config/configuration.example.json` if configuration changed.
8. Add unit tests (extend `tests/unit/application/fakes.py` if a new port
   was introduced).
9. Update this file and `docs/RUNNING.md`.
10. Update `deploy/*.service` if execution commands changed.

## Last Updated

2026-07-10 — Added Mongo-backed distributed runtime leases for bot polling
and collector ownership. Startup now refuses a second instance before
polling/backfill, heartbeats leases every 15 seconds, and stops safely after
lease loss. Approval preview repair now runs after application startup in the
background, migrates legacy preview references to `unknown`, detects
text/caption shape, preserves callback keyboards, and never resends an old
approval.

2026-07-10 — Added the approval-bot `/panel`, atomic config persistence,
daily recurring source-post campaigns in Telegram's native schedule,
normalized recurring campaign/time/destination mirrors in SQLite,
immediate approval with 20-minute in-place score updates, shared delivery
history buttons, and all-dialog VPN config discovery for common proxy
protocols with URI-level deduplication and AI advertisement cleanup.

2026-07-10 — Fixed score refresh removing approval keyboards and the caption
`caption_text` failure. Added retry-aware preview repair, early callback
acknowledgement, atomic live/backfill source ingestion, collector-session
metric refresh jobs, and bounded media download retries.

2026-07-08 — Hardened Telethon destination publishing and source metadata
refresh against transient user-session disconnects, and made approval
keyboard refresh treat Telegram's `message is not modified` response as a
harmless no-op in callback handlers.

2026-07-06 — Made approval preview dispatch restart-idempotent:
`approval_requests` now records reserved/sent/failed dispatch state, and
startup no longer resends posts that previously entered the approval bot
stage even when old approval message refs are inactive.

2026-07-05 — Added Telegram id cleanup during publish-time rewrite:
configured source mentions are replaced with the destination id and all
other Telegram handles/links are removed. Added OpenRouter
`fallback_models`/`route` config support so OpenRouter can perform
server-side fallback routing.

2026-07-04 — Added approval-repair delivery guards so posts with any
publish-log history are not resent to admins after restart. Added custom
emoji text-entity storage and Telethon destination publishing for both
immediate and native scheduled approved posts, preserving premium emoji
through source mention rewrites.

2026-07-04 — Fixed video approval preview recovery: stored text-only source
posts now download and attach missing media on later same-day backfills, and
approval previews retry failed `send_video` uploads as documents.

2026-07-04 — Fixed approval-message propagation recovery: Telegram
`message is not modified` no longer deactivates valid approval refs, and
startup now resends recorded approval requests that have no active bot
message refs so posts cannot remain invisible in `waiting_approval`.

2026-07-03 (latest) — Replaced approval confirmation/mode-toggle flow with
direct two-column publish/schedule toggles. Active buttons now delete the
real published channel message or native Telegram scheduled message on
second click. Added timestamped per-run log files, local fuzzy duplicate
pre-filtering to cut AI prompt tokens, fail-fast AI provider fallback for
quota/temporary failures, and italic HTML approval headers with source
publish time.

2026-07-03 (latest) — Reintroduced quality scoring as a required
collection-to-approval gate with 0-to-100 scores, 15-minute freshness
delay, and Telegram metadata refresh before scoring. Added AI
advertisement pruning, colored console logs for AI usage/errors and
application errors, and native Telegram scheduled publishing through a
separate Telethon scheduler user session with five-minute per-channel
slots.

2026-07-03 (latest) — Added a per-media Telegram download timeout so a
stalled video/photo download cannot block same-day backfill; timed-out
media is skipped with a warning while the post text continues to storage
and approval.

2026-07-03 (later) — Made startup backfill more fault-tolerant: config
sources are merged with SQLite sources, one source history failure no
longer aborts the whole same-day backfill, and HTTP 429 from an AI provider
now fails fast and places that provider in a short cooldown so the chain
falls through instead of hammering Gemini/free quota during bursts. Combined
AI analysis now handles duplicate check and classification in one request
per post to reduce requests during backfill.

2026-07-03 — Refactored collection/storage/approval into idempotent stages.
Earlier eligible posts enqueued `approval_request` or `vpn_test` immediately;
that behavior is now superseded by the required `quality_score` gate. MongoDB stores
source identity (`source_chat_id`, `source_message_id`, `grouped_id`),
duplicate/skipped state, and uses a unique source-message index so restarts
do not redownload media. SQLite now has `approval_requests` to prevent
duplicate approval previews, and same-day backfill repairs missing
`vpn_test`/`approval_request` stages without hiding stored posts.

2026-07-02 — Replaced the old two-provider AI setup with a
configuration-driven provider chain (Google AI Studio → Groq → OpenRouter
→ DeepSeek, with z.ai disabled in the template), added optional AI quality
scoring with source engagement metrics and approval preview headers. Native
scheduled publishing now uses a Telethon user session and five-minute slots;
the older 24-hour internal queue behavior is superseded. MongoDB
datetime fields are normalized to UTC-aware values at the repository
boundary. Collector
same-day backfill now processes sources round-robin after scanning them,
so the first configured source cannot delay every later source. Stored
same-day posts that never reached approval are requeued during exact-hash
dedup repair.

2026-07-03 — Added runtime collector catch-up scans for known sources so
Telethon reconnect/difference sync does not leave source-channel posts
unprocessed when no live event reaches the handler. Approval requests now
raise and retry when every admin send fails, preventing false
`waiting_approval` state for messages the approval bot never delivered.

2026-07-03 — Added runtime config hot reload for source/destination/admin
lists, approval-message tracking for multi-admin keyboard propagation, and
publish-log reservations so concurrent admin clicks cannot double-publish
the same post to the same destination.

2026-07-02 (later) — Added scheduled publishing: delivery-mode toggle on
approval messages (scheduled queue by default, immediate on demand),
per-channel pacing via `post_interval_minutes` and the `scheduled_publish`
queue type, and full channel management in the main bot (`/addsource`,
`/delsource`, `/adddest`, `/deldest`, `/setdest`, `/setinterval`). Channel
config became seed-only; SQLite is the runtime source of truth and the
collector reloads sources from SQLite. Fixed AI 400 errors: model
overrides are per provider (`ai.zai_model`/`ai.deepseek_model`, shared
keys deprecated) and HTTP errors now log the API's response body. Mention
rewriting also covers collector-resolved source usernames, warns when a
destination lacks `public_id`, and seeding backfills an empty `public_id`
from config.

2026-07-02 — Added video/document media collection, approval previews, and
publishing, plus readable source-channel labels in approval previews.
Changed collector startup and newly added source backfill to scan from the
current Gregorian day's first message, capped by
`telegram.collector_daily_backfill_max_messages`. Added main bot management
commands, destination `public_id` replacement for source-channel mentions,
runtime source-channel refresh, album-aware collection, Telegram retry-after
handling, and conservative storage when AI classification is unavailable.
Earlier the same day: pipeline observability (startup config summary,
per-stage collection logs, approval-bot `/start` handler, error-chain
logging) and the Nobitex USD price source (`usd_price.provider`,
`create_price_source()` factory). 2026-07-01: all-in-one entrypoint
`src/run_all.py` (`telegram-suite` executable and systemd unit, `suite`
install role), publish build system, and the initial full implementation.
