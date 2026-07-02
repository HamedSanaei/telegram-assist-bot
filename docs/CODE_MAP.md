# Code Map

## Project Purpose

A Telegram channel administration and publishing system. It collects posts
from source channels, deduplicates and classifies them with AI (z.ai primary,
DeepSeek fallback), tests vmess/vless VPN configs from an Iran-based worker,
routes posts through an admin approval bot, publishes approved posts to
destination channels, and posts USD price updates twice per day.

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
                   publisher, VPN testers, price source.
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
- `entities.py` — `Post`, `MediaItem`, `VpnConfig`, `DestinationChannel`,
  `AdminUser`, `QueueItem`, `PublishRecord`, `DollarPrice`.
- `interfaces.py` — all ports: `AiProvider`, `PostRepository`,
  `QueueRepository`, `ChannelRepository`, `AdminRepository`,
  `PublishLogRepository`, `PriceHistoryRepository`, `VpnConnectivityTester`,
  `MessagePublisher`, `ApprovalNotifier`, `PriceSource`, plus result value
  objects (`AiClassificationResult`, `DuplicateCheckResult`, `VpnTestResult`).
- `services/text_normalizer.py` — normalization + SHA-256 content hash for
  cheap exact deduplication (original text is never altered).
- `services/vpn_parser.py` — `parse_vmess`, `parse_vless`,
  `extract_vpn_configs` (pure stdlib parsing of config URIs).

## Application Layer

- `ai_service.py` — `AiService`: primary provider with automatic fallback;
  raises `PostClassificationError` / `DuplicateDetectionError` when all fail.
- `collect_post.py` — `CollectPostUseCase.handle_new_message`: hash dedup →
  AI dedup/classification when available → VPN config extraction → store
  with 14-day expiry → enqueue `vpn_test` or `approval_request`. If all AI
  providers are temporarily unavailable, the post is still stored for manual
  approval with a conservative default category instead of being dropped.
- `vpn_test_service.py` — `VpnTestService`: tests each config via the
  tester port; post is eligible when at least one config works from Iran.
- `approval_service.py` — `ApprovalService`: approval requests, admin
  validation, publish with duplicate-prevention via the publish log.
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
  `UPDATE ... RETURNING`), `SqlitePublishLogRepository`,
  `SqlitePriceHistoryRepository`.
- `db/mongo/post_repository.py` — `MongoPostRepository` (Motor); TTL index
  on `expires_at`, lookup index on `content_hash`.
- `ai/openai_compatible.py` — shared chat-completions client with strict
  JSON prompts; HTTP error messages include the model name and the API's
  error body. `ai/zai_provider.py` (default model glm-4.6, override
  `ai.zai_model`) and `ai/deepseek_provider.py` (default model
  deepseek-chat, override `ai.deepseek_model`). Model overrides are per
  provider; the legacy shared model keys are ignored with a warning.
- `telegram/publisher.py` — `AiogramMessagePublisher` (text, or first
  photo/video/document with caption; long text sent as a follow-up message).
- `vpn/worker_client.py` — `IranWorkerVpnTester`: HTTP client for the Iran
  worker API (bearer token auth).
- `vpn/xray_tester.py` — `XrayVpnTester`: spawns xray with a temp config,
  probes a test URL through the local SOCKS5 inbound. Used by the Iran
  worker only. Supports tcp/ws/grpc + tls/reality.
- `price/http_price_source.py` — `HttpJsonPriceSource`: configurable URL +
  dotted JSON path.

## Presentation Layer

- `approval_bot/keyboards.py` — inline keyboards. The first row toggles the
  delivery mode (`s` = scheduled queue, default; `i` = immediate). Callback
  data: `apv:mode:<post_id>:<m>`, `apv:send:<post_id>:<chat_id>:<m>`,
  `apv:cfm:<post_id>:<chat_id>:<m>`, `apv:cxl:<post_id>:<m>`,
  `apv:nop:pub` / `apv:nop:sch` (published channels render as `✅ <title>`,
  queued ones as `⏱ <title>`).
- `approval_bot/handlers.py` — `create_approval_router(approval_service,
  timezone_name)`: toggle mode → select channel → confirm keyboard →
  publish immediately or `schedule_publish` into the channel queue →
  keyboard refreshed with ✅/⏱. Every callback validates admin, post,
  channel, duplicate, and queued state.
- `approval_bot/notifier.py` — `AiogramApprovalNotifier`: sends the Persian
  preview with a readable source-channel label, the first downloaded
  photo/video/document when present, and keyboard to every configured admin;
  obeys Telegram retry-after responses.
- `main_bot/handlers.py` — admin-only management commands for the main bot:
  reports (`/start`, `/status`, `/sources`, `/destinations`) and channel
  management (`/addsource`, `/delsource`, `/adddest`, `/deldest`,
  `/setdest <chat_id> <field> <value>`, `/setinterval <chat_id> <minutes>`).
  Channel edits are written to SQLite, which is the runtime source of truth.

## Workers

- `queue_worker.py` — `QueueWorker`: polls SQLite queue, claims atomically,
  dispatches by `QueueItemType` (`vpn_test`, `approval_request`,
  `scheduled_publish`), retries with linear backoff up to `max_attempts`,
  then marks failed. Safe to restart at any time. `scheduled_publish` items
  become due at the per-channel paced slot computed by
  `ApprovalService.schedule_publish` (at least `post_interval_minutes`
  between posts of one channel).
- `scheduler.py` — APScheduler cron jobs: USD price at configured times
  (default 09:00 and 21:00 Asia/Tehran) and daily cleanup.
- `collector.py` — Telethon-based listener on source channels; stores
  resolved source titles/usernames, downloads photos/videos/documents to
  `storage.media_directory`, and feeds `CollectPostUseCase`. On
  startup it also scans messages from the first post of the current
  Gregorian day in `scheduler.timezone` for each source so restarts and
  missed live events still enter the normal dedup/classify/store pipeline.
  It reloads the source channel list from SQLite every
  `telegram.source_refresh_seconds` seconds so sources added or removed via
  the management bot are picked up without a restart; new ones are
  backfilled from today's first message. Albums are
  collected as one post. Runs as its own process
  (`python -m src.workers.collector`) or inside the all-in-one entrypoint.
  Note: only the first media file of a post is republished currently.
- `src/run_all.py` — all-in-one entrypoint (`python -m src.run_all`): runs
  `src.main.run` and the collector concurrently in one event loop, each
  under `supervise()` which restarts a crashed component after a delay and
  permanently stops (only) a component whose configuration is invalid.
- `iran_vpn_worker.py` — FastAPI app on the Iran server. `POST /api/test`
  (bearer token) receives `{"raw": "<vmess|vless URI>"}` and returns
  `{"working", "latency_ms", "error"}`. `GET /api/health` is a liveness probe.

## Databases

- **SQLite** (`data/app.db`): `destination_channels`, `source_channels`
  (including resolved title/username/chat id), `admins`, `queue_items`,
  `publish_log` (UNIQUE post/channel pair), `price_history`, `settings`,
  `error_log`, `schema_migrations`.
- **MongoDB** (`posts` collection): full post documents with text, media
  metadata, AI results, extracted configs, `collected_at`, `expires_at`
  (TTL index deletes after 14 days).

## Configuration

`config/configuration.json` (git-ignored; template in
`configuration.example.json`). Loaded by `src/shared/config.py` into frozen
dataclasses. Per-entrypoint validators: `validate_main_app_config`,
`validate_collector_config`, `validate_worker_config`. Path override via the
`TELEGRAM_ADMIN_BOT_CONFIG` environment variable. Collector same-day startup
scan is capped by `telegram.collector_daily_backfill_max_messages`, and
runtime source reloads by `telegram.source_refresh_seconds`. Destination
`public_id` values are used to replace configured source-channel mentions
before publishing to each selected destination.

Channel lists in the config file are **seed-only**: `sync_config_to_sqlite`
inserts missing rows but never overwrites existing ones, so channels and
per-channel settings (`post_interval_minutes`, `public_id`, ...) edited via
the management bot survive restarts. Admin ids stay config-authoritative.

## Telegram Bots

- **Main bot** (`telegram.bot_token`) — publishes to destination channels
  and handles admin management commands; must be admin in all destination
  channels.
- **Approval bot** (`telegram.approval_bot_token`) — talks only to admins;
  long polling runs inside `src.main`.
- **Collector user session** (`telegram.api_id`/`api_hash`) — Telethon user
  account that reads the source channels.

## VPN Testing Worker

Main server enqueues `vpn_test` → `QueueWorker` calls `VpnTestService` →
`IranWorkerVpnTester` POSTs the raw URI to the Iran worker → the worker
parses it and runs `XrayVpnTester` → result is stored per-config in MongoDB.
Eligible posts (≥1 working config) continue to the approval queue; others
are marked `skipped`.

## Scheduled Jobs

Configured in `scheduler` section: `usd_price_publish_times` (twice daily),
`cleanup_time` (daily), all in `scheduler.timezone` (default Asia/Tehran).
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
duplicate check → classified → saved to MongoDB → enqueued → queue item
done) so a stalled pipeline can be bisected from the logs; the exact lines
are documented in the Troubleshooting section of `docs/RUNNING.md`. The
approval bot answers `/start` with the caller's admin status.

## Tests

`tests/unit/` mirrors the layers; fakes for all ports live in
`tests/unit/application/fakes.py`. Covered: vmess/vless parsing, extraction
from Persian text, UTF-8/Mojibake safety, hash dedup, AI fallback (both
directions), classification routing, approval validation and duplicate
publish prevention, VPN eligibility, queue claim/retry/expiry semantics,
price change formatting, 14-day expiration, config loading/validation.
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
