# Running the Telegram Admin Bot

## Requirements

- Python 3.12 or newer
- MongoDB 5 or newer (local or remote)
- SQLite (bundled with Python)
- Telegram credentials:
  - Main bot token (management bot and USD/fallback publishing)
  - Approval bot token (talks to admins)
  - API ID and API hash from https://my.telegram.org (for the collector
    user session that reads source channels and the scheduler user session)
  - A separate premium Telethon destination/scheduler user session that is
    an admin in every destination channel. Approved post publishing and
    native Telegram scheduling both use this user session so premium custom
    emoji entities can be preserved.
- At least one enabled AI provider key in `ai.providers`. The default chain
  is Google AI Studio, Groq, OpenRouter, then DeepSeek. z.ai remains in the
  template but is disabled by default.
- For VPN testing: an Ubuntu server inside Iran with
  [xray-core](https://github.com/XTLS/Xray-core) installed

## Installation

```bash
git clone <repository-url> telegram-admin-bot
cd telegram-admin-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # add requirements-dev.txt for tests
```

## Configuration

```bash
cp config/configuration.example.json config/configuration.json
```

Edit `config/configuration.json` (UTF-8, never committed):

| Key | Meaning |
| --- | --- |
| `telegram.bot_token` | Main management bot token, plus USD/fallback text publishing |
| `telegram.approval_bot_token` | Approval assistant bot token |
| `telegram.api_id` / `api_hash` | Telegram API credentials for the collector |
| `telegram.collector_session` | Telethon session file path (default `data/collector`) |
| `telegram.scheduler_session` | Telethon user session file path used to publish approved posts and upload posts into the destination channel's native Telegram schedule (default `data/scheduler`) |
| `telegram.scheduler_phone` | Optional phone number for the first scheduler user login. If it is empty and the session is not logged in yet, `src.main` asks for it interactively. Leave empty after the session file has been created. |
| `telegram.collector_daily_backfill_max_messages` | Maximum messages scanned from each source for the current Gregorian day when the collector starts (default `5000`; set `0` to disable) |
| `telegram.source_refresh_seconds` | How often the running collector reloads the source channel list from SQLite (default `60`; set `0` to disable collector live refresh) |
| `telegram.source_channels` | Usernames (`"@channel"`) or numeric ids to collect from. This list is hot-reloaded and authoritative at runtime. |
| `telegram.destination_channels` | Objects: `chat_id`, `title`, `public_id`, `kind` (`news`/`breaking`/`technology`/`vpn`), `publish_usd_price`, `post_interval_minutes` (kept for compatibility; native scheduled posts are currently paced every 5 minutes). `chat_id` is the numeric Telegram id of the channel (negative, usually starting with `-100`); forward a channel post to `@userinfobot` or open the channel in Telegram Web and prefix the number in the URL with `-100` to find it. `public_id` is the public destination handle/link (for example `@my_channel`) used to replace source-channel mentions before publishing. The scheduler/destination user must be an admin for approved immediate publishing and native scheduled publishing. |
| `telegram.admin_user_ids` | Telegram user ids allowed to approve posts |
| `ai.providers` | Priority-ordered AI provider chain. Each entry has `name`, `enabled`, `api_key`, `base_url`, `model`, and `timeout_seconds`. Disabled providers, or providers without a key/model/base URL, are skipped. The default order is `google_ai_studio`, `groq`, `openrouter`, `deepseek`, with `zai` kept as `enabled: false`. For OpenRouter only, `fallback_models` plus `route: "fallback"` enables OpenRouter's own model fallback routing inside that provider. |
| `ai.request_timeout_seconds` / `recent_posts_compare_limit` | Shared timeout fallback for provider entries and the number of recent posts used for AI duplicate checks. |
| `database.sqlite_path` | SQLite file (default `data/app.db`) |
| `database.mongodb_connection_string` | e.g. `mongodb://localhost:27017` |
| `storage.media_directory` | Downloaded media location |
| `storage.retention_days` | Post retention (default 14) |
| `storage.media_download_timeout_seconds` | Maximum seconds to wait for each Telegram media download before continuing with the text-only post (default `60`) |
| `vpn_testing.worker_api_url` | Iran worker base URL, e.g. `http://1.2.3.4:8088` |
| `vpn_testing.worker_api_token` | Shared secret for the worker API |
| `vpn_testing.xray_binary_path` | xray path on the Iran server |
| `scheduler.usd_price_publish_times` | e.g. `["09:00", "21:00"]` (in `scheduler.timezone`) |
| `scheduler.recurring_forward_lookahead_hours` | Rolling native Telegram schedule horizon for recurring campaigns (default `24`) |
| `scheduler.recurring_forwards` | Daily campaign objects with `id`, `enabled`, `source_post_url`, `destination_chat_ids`, `show_forward_header`, and Tehran `times` in `HH:MM` format |
| `usd_price.provider` | `"nobitex"` (default; free-market USDT rate from the public Nobitex API, no key needed, published in Toman) or `"http_json"` for a custom endpoint |
| `usd_price.source_url` / `price_json_path` | Only for `provider: "http_json"`: JSON endpoint and dotted path of the USD price value |
| `logging.color_console` | Colorize console logs: AI provider/model usage in green, AI provider failures in orange, and application errors in red. File logs stay plain UTF-8. Each process start also creates `logs/YYYYMMDD-HHMMSS-<entrypoint>.log` beside the stable configured log file. |

A custom config path can be set with the `TELEGRAM_ADMIN_BOT_CONFIG`
environment variable.

## Database Setup

### MongoDB

Install and start MongoDB, then set `database.mongodb_connection_string`.
The application creates the `posts` collection, the TTL index on
`expires_at`, and lookup indexes automatically on startup. No manual steps.

### SQLite

Nothing to install. The database file and all tables are created by the
versioned migrations automatically on startup.

### Running Migrations

Migrations run automatically whenever any entrypoint starts. To run them
manually:

```bash
python -c "
import asyncio
from src.shared.config import load_configuration
from src.composition import create_sqlite
asyncio.run(create_sqlite(load_configuration()))
"
```

## Running the Application

### Running Everything with One Command (recommended)

```bash
python -m src.run_all
```

This starts the main application (approval bot + queue worker + scheduler)
and the collector together in a single process. Each component runs under a
supervisor: if one crashes it is restarted after a short delay without
taking the other down. A component with broken configuration is stopped
permanently (and logged) while the rest keep running.

Do the first collector and scheduler logins separately
(`python -m src.workers.collector` and `python -m src.main`) before using
this entrypoint as a background service, because the interactive Telethon
prompts wait for phone/login codes on stdin.

The Iran VPN worker is not part of this command; it runs on the Iran server
(see below).

### Running the Main Bot (approval bot + queue worker + scheduler)

```bash
python -m src.main
```

This single process runs the approval bot (long polling), the SQLite queue
worker (background quality updates, VPN test dispatch, and approval dispatch), the
Telethon destination/scheduler user session for approved channel publishing
and native channel scheduling, the recurring-forward reconciler, and the
scheduler (USD price publishing and daily cleanup). It also polls the main management bot
(`telegram.bot_token`) for admin commands.

### Managing Channels with the Management Bot

The main bot (`telegram.bot_token`) doubles as the management bot for
admins listed in `telegram.admin_user_ids`:

| Command | Effect |
| --- | --- |
| `/start`, `/help` | Show the command list |
| `/status` | System summary (sources, destinations, admins, AI, backfill) |
| `/sources` | List source channels |
| `/addsource <@user or id>` | Add a source channel; the collector starts watching it within `source_refresh_seconds` |
| `/delsource <@user or id>` | Stop collecting from a source channel |
| `/destinations` | List destination channels with kind, interval, and USD flag |
| `/adddest <chat_id> <title>` | Add a destination channel with defaults (kind `news`, interval 30 min) |
| `/deldest <chat_id>` | Disable a destination channel |
| `/setdest <chat_id> <field> <value>` | Change `title`, `public_id`, `kind`, `usd` (on/off), `enabled` (on/off), or `interval` (minutes) |
| `/setinterval <chat_id> <minutes>` | Shortcut for the scheduling interval |

`configuration.json` is hot-reloaded while the app is running for
`source_channels`, `destination_channels`, `admin_user_ids`, and
`scheduler.recurring_forwards`. Those lists are authoritative: adding an
item to the file activates it without a restart, and removing one disables
or removes it from runtime SQLite state. Secrets, AI providers, database
paths, Telegram sessions, scheduler phone, and price settings still require
a restart. Runtime edits made with legacy bot commands are overwritten by
the next config reload unless the same change is also written to
`configuration.json`.

The approval bot also exposes `/panel`. Its callback menus manage source
channels, destination channels, and recurring campaigns. Panel changes are
written atomically to the UTF-8 JSON file and immediately mirrored to
SQLite, so they survive hot reload and do not require a restart. Use
`/cancel` to leave an unfinished panel wizard.

Recurring definitions are normalized into
`recurring_forward_campaigns`, `recurring_forward_campaign_times`, and
`recurring_forward_campaign_destinations`; actual Telegram schedule state
is tracked separately in the occurrence/message tables.

### Running the Approval Bot

The approval bot runs inside `src.main`; it has no separate entrypoint.

Every approval message shows two direct buttons per destination channel:
`🚀 فوری` and `⏱ اسکجول`. There is no separate confirmation step. Tapping
`🚀 فوری` publishes immediately through the Telethon destination user
session configured by `telegram.scheduler_session`. Tapping `⏱ اسکجول`
uploads the post into Telegram's own scheduled-message list for that
destination channel through the same session. The slot is 5 minutes after
the latest scheduled/published post for that destination, or 5 minutes from
now when there is no queued channel post.

Active buttons show ✅ and remain clickable. A second tap on `✅ فوری`
deletes the real published message from the destination channel. A second
tap on `✅ اسکجول` removes the native Telegram scheduled message. If
Telegram refuses deletion because of permissions or a missing message id,
the state stays active and the bot shows an error alert.

Posts are delivered to the approval bot immediately after duplicate,
advertisement, and relevance analysis. The initial italic header shows
`⭐ امتیاز: در انتظار آمار ۲۰ دقیقه‌ای`. At 20 minutes after the source
publish time, a background queue item refreshes views, forwards, replies,
and reactions, calculates the 0-to-100 score, and edits the same approval
message for every admin. It never sends a second approval preview. Older
same-day backfill posts are approved first and scored immediately afterward.

When a post has any publish/schedule history, a prominent single button is
rendered above destination actions. Tapping it shows the recorded channel
states (`published`, `scheduled`, or `removed`). This state is shared across
admins and refreshes after every toggle.

When multiple admins receive the same approval post, the bot stores every
approval message id. If one admin publishes or schedules a post, keyboards
for all other admins are refreshed best-effort. If one admin deletes or
unschedules it, all keyboards are refreshed back to the available state. If
an old approval message was deleted or the bot can no longer edit it, that
message is marked inactive and the callback continues normally. Telegram's
`message is not modified` response during refresh is a harmless no-op and
does not deactivate the stored approval message.

At startup, `src.main` never resends posts that already have an
`approval_requests` row. That row is the idempotency boundary for approval
previews: if an approval message was previously delivered, deleted, or later
marked inactive because Telegram could not edit it, restart still does not
create a duplicate preview. Missing approval-message refs are logged for
manual inspection instead of being resent automatically.

Important: Bot API cannot create native scheduled channel messages and is
not reliable for preserving premium custom emoji in destination posts. The
scheduler/destination session is a normal Telegram user session, should be
premium when source posts contain premium emoji, must be logged in once
interactively, and must be added as an admin to the destination channels. If
the scheduler session is not logged in and `telegram.scheduler_phone` is
empty, `src.main` asks for the phone number in the terminal and then
Telethon asks for the login code.

### Running the Collector

```bash
python -m src.workers.collector
```

On first run Telethon asks for a phone number and login code and stores the
session at `telegram.collector_session`. Run the first login interactively
before enabling the systemd service.

At startup the collector scans from the first message of the current
Gregorian day in `scheduler.timezone` (Asia/Tehran by default) for every
source channel, then waits for live updates. Startup uses the union of
sources listed in `configuration.json` and enabled sources stored in
SQLite, so sources you add to the config during development are included
in the next same-day backfill even if SQLite has older state. Messages are processed
oldest-first within each source and round-robin across sources, so one
busy channel cannot delay all other channels' same-day posts. The normal
exact-hash and AI deduplication prevents restarts, cross-channel reposts,
and already processed posts from being stored twice. If a same-day post was
already stored in MongoDB but never reached an active/successful
`quality_score_update`, `vpn_test`, or `approval_request` queue item, the backfill
requeues the missing next stage instead of hiding it as a duplicate. Stored source messages are
checked before media download, so restarts do not download the same photo
or video again when MongoDB already has the media. If an older bug or
timeout stored the post without its video/photo, the collector downloads
the missing media on the next same-day backfill and updates the existing
MongoDB post.
`telegram.collector_daily_backfill_max_messages` is only a safety cap per
source; increase it for very high-volume channels or set it to `0` only
when you want a strict live-only listener.
If one source fails while reading history, the collector logs that source
failure and continues backfilling the remaining sources.

In parallel, the collector enumerates every joined Telegram channel and
group, including archived/muted dialogs, except configured source and
destination chats. It prefilters today's messages locally for `vmess`,
`vless`, `ss`, `ssr`, `trojan`, `hysteria2`/`hy2`, and `tuic` URIs before
media download or AI usage. Each source message and each exact config URI
is persisted idempotently, so the same config forwarded through another
chat is not proposed again. AI removes promotional/referral text while
protected placeholders guarantee the original config URIs are restored
unchanged; if AI fails, only the config URIs are kept. These discovery posts
go immediately to approval without quality scoring and show only VPN
destination buttons. Publishing is allowed before Iran testing; currently
only `vmess`/`vless` are testable and other protocols are marked unsupported.

While running, the collector reloads the source channel list from SQLite
every `telegram.source_refresh_seconds` seconds. Sources added with the
management bot's `/addsource` or added to `configuration.json` are resolved
and backfilled from today's first message without restarting the process;
sources removed with `/delsource` or removed from `configuration.json` stop
being collected on the next refresh. On each refresh the collector also runs a
lightweight current-day catch-up scan for already known sources, capped at
300 recent messages per source. This covers Telethon reconnect/difference
sync cases where account state advances but no live event is delivered to
the handler. Already stored source messages are skipped before media
download or AI calls, so the catch-up is idempotent.

When the collector resolves a source channel, it stores the channel title
and username in SQLite. Approval previews then show that readable source
label instead of the raw `-100...` chat id. The collector downloads photos,
videos, and documents into `storage.media_directory`; approval previews and
destination publishing send the first available media file with the post
caption, falling back to a text-only message when no media file exists.
The collector also stores Telegram custom emoji text entities next to the
raw text. Destination publishing sends those entities back through Telethon
`formatting_entities`, so premium emoji survive source mention rewriting
and republishing when the destination user session has access to them.
Approval previews try to send videos as Telegram videos first and fall back
to sending the same local file as a document if Bot API rejects the video
upload, so video posts do not disappear from the approval bot silently.
If Telegram stalls while downloading a media file from another data center,
the collector waits up to `storage.media_download_timeout_seconds`, logs a
warning, and still stores/sends the text portion instead of blocking the
rest of the backfill.

### Running the Scheduler

The scheduler runs inside `src.main`. Times are configured in the
`scheduler` section (`Asia/Tehran` by default). The USD price job uses
the source selected by `usd_price.provider` — with the default
`"nobitex"` no further price configuration is required.

The recurring-forward worker also runs inside `src.main`. It loads campaign
changes without restart and fills the next configured lookahead window in
Telegram's native channel schedule. `show_forward_header: true` performs a
real forward; `false` copies text/media/albums without `Forwarded from`.
Occurrence identity is stored in SQLite, preventing duplicate schedules
after restart. Disabling, deleting, or changing a campaign removes obsolete
future native scheduled messages. Missed past times are intentionally not
sent as catch-up advertisements.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest                    # full suite
pytest tests/unit         # unit tests
pytest tests/integration  # integration tests
```

## Running the Iran VPN Testing Worker

On the Iran server:

```bash
# 1. Install xray-core
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
# binary lands at /usr/local/bin/xray

# 2. Install the project (only the worker runs here)
cd /opt && git clone <repository-url> telegram-admin-bot
cd telegram-admin-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. Configure: configuration.json needs only vpn_testing.* filled in
#    (worker_api_token, xray_binary_path, worker_listen_host/port, test_url)

# 4. Run
.venv/bin/python -m src.workers.iran_vpn_worker
```

The worker listens on `vpn_testing.worker_listen_host:worker_listen_port`
and requires `Authorization: Bearer <worker_api_token>` on every request.
Set the same URL and token in the main server's configuration
(`vpn_testing.worker_api_url` / `worker_api_token`).

Health check:

```bash
curl http://localhost:8088/api/health
```

## Testing VLESS Configurations

```bash
curl -X POST http://localhost:8088/api/test \
  -H "Authorization: Bearer <worker_api_token>" \
  -H "Content-Type: application/json" \
  -d '{"raw": "vless://uuid@host:443?type=ws&security=tls&path=/ws#remark"}'
```

Response: `{"working": true, "latency_ms": 850, "error": null}`

## Testing VMESS Configurations

```bash
curl -X POST http://localhost:8088/api/test \
  -H "Authorization: Bearer <worker_api_token>" \
  -H "Content-Type: application/json" \
  -d '{"raw": "vmess://eyJhZGQiOiAiaG9zdCIsIC4uLn0="}'
```

A config is marked eligible for VPN channel publishing only when the worker
reports `"working": true` from the Iranian network.

## Building Release Outputs (publish/)

Regenerate the distributable outputs after every meaningful change:

```bash
pip install -r requirements-dev.txt   # includes PyInstaller
python scripts/build_publish.py
```

This produces:

- `publish/windows/` — standalone executables (`telegram-suite.exe` for the
  all-in-one process, plus `telegram-admin-bot.exe`, `telegram-collector.exe`,
  and `iran-vpn-worker.exe`) with the configuration template and a
  `README.txt`. Put `config/configuration.json` next to the executables
  before running them. Windows-only build step.
- `publish/ubuntu/telegram-admin-bot-<version>.tar.gz` — source bundle for
  Ubuntu servers. Extract it and run
  `sudo bash install.sh [main|collector|suite|worker|all]`
  to install to `/opt/telegram-admin-bot`, create the virtualenv, and copy
  the systemd units. The `suite` role installs the single all-in-one service
  instead of `main` + `collector`.
- `publish/BUILD_INFO.txt` — version, build timestamp, and platform.

Use `python scripts/build_publish.py --skip-exe` for a quick Ubuntu-only
rebuild. `publish/` and `build/` are generated and git-ignored.

Installing on Ubuntu from the bundle:

```bash
tar xzf telegram-admin-bot-0.1.0.tar.gz
cd telegram-admin-bot-0.1.0
sudo bash install.sh main        # or: collector / worker / all
sudo nano /opt/telegram-admin-bot/config/configuration.json
sudo systemctl enable --now telegram-admin-bot
```

## Ubuntu systemd Service

Deploy the project to `/opt/telegram-admin-bot`, create the service user,
and install the templates from `deploy/`:

```bash
sudo useradd --system --home /opt/telegram-admin-bot telegrambot
sudo chown -R telegrambot:telegrambot /opt/telegram-admin-bot

# Main server - option A: one service for everything (src.run_all)
sudo cp deploy/telegram-suite.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-suite

# Main server - option B: main app and collector as separate services
sudo cp deploy/telegram-admin-bot.service /etc/systemd/system/
sudo cp deploy/telegram-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-admin-bot telegram-collector

# Never enable option A and option B at the same time - posts would be
# collected and published twice.

# Iran server
sudo cp deploy/iran-vpn-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iran-vpn-worker
```

Check logs:

```bash
journalctl -u telegram-suite -f
journalctl -u telegram-admin-bot -f
journalctl -u telegram-collector -f
journalctl -u iran-vpn-worker -f
```

Note: run the collector once interactively before enabling its service so
the Telethon session file exists.

## Troubleshooting

- **Posts are collected but never reach MongoDB or the approval bot** —
  follow one message through the per-stage log lines (console and
  `logs/app.log`). Each collected message produces this sequence; the last
  line you see tells you which stage failed:

  | Log line | Meaning |
  | --- | --- |
  | `Received live message chat=... msg=...` / `Received backfill message chat=... msg=...` | Collector got the message from Telegram; the log includes media counts such as `photos=... videos=...` |
  | `Collector daily backfill queued source_count=... groups=... strategy=round_robin` | Startup/current-day backfill scanned all sources and will process them fairly across channels |
  | `Duplicate check passed ... provider=...` | AI dedup done (skips log `Skipping ... duplicate` instead) |
  | `Classified ... category=... provider=...` | AI classification done (`irrelevant` posts are stored with `skipped_reason=irrelevant` and stop here by design) |
  | `AI-pruned advertisement ...` | AI judged the source post to be promotional; it is stored with `skipped_reason=advertisement` and is not sent to approval |
  | `Classification unavailable; storing for manual approval ...` | Every enabled AI provider failed classification; post is still stored for admin review |
  | `Saved post to MongoDB id=...` | Post inserted into MongoDB |
  | `Enqueued approval_request post=...` | The valid post was queued for immediate admin preview |
  | `Enqueued quality_score_update post=... scheduled_at=...` | The already-approved post will have its metrics and score refreshed 20 minutes after source publication |
  | `Quality scored post=... score=.../100 provider=...` | Required AI quality score was stored in MongoDB and the post can continue to VPN testing or approval |
  | `Enqueued approval_request post=...` / `Enqueued vpn_test post=...` | Queued after quality scoring for approval or VPN testing |
  | `Repaired stored source post post=... chat=... msg=...` | Backfill found a post that was stored before but never reached quality scoring/approval/VPN flow, so it repaired the missing pipeline stage |
  | `Skipping media download for stored source chat=... msg=...` | The post is already in MongoDB and has enough stored media, so restart/backfill did not download its media again |
  | `Repaired stored source media post=... chat=... msg=... media=...` | Backfill found a stored post whose media was missing and updated MongoDB with the downloaded attachment |
  | `Approval video preview failed; retrying as document ...` | Bot API rejected `send_video`; the approval bot retried the same file with `send_document` |
  | `Media download timed out msg=... kind=...` | Telegram media download stalled; the collector continued with the text-only post |
  | `Queue item done id=... type=approval_request` | Approval message sent to admins |

  Common causes when the chain stops early:
  - Only `telethon.client.updates | Got difference ...` appears, with no
    `Received live/backfill message ...` lines — Telethon only synchronized
    account/channel state; the application did not receive a source-channel
    message event. Confirm `telegram.source_channels` contains the exact
    source channels you want, and keep
    `collector_daily_backfill_max_messages` above `0` so today's source
    posts are scanned on startup. The running collector also performs a
    periodic `Collector runtime catch-up ...` scan; if that line appears
    but the expected source still never logs `Received ...`, the configured
    account likely cannot read that channel or the channel is not in
    `telegram.source_channels`/SQLite.
  - `Classification unavailable; storing for manual approval ...` — every
    enabled AI provider failed or hit a quota/rate/payment limit. The post
    is kept and sent to admins with a conservative default category instead
    of being dropped. Check the `Effective configuration` block logged at
    startup: it shows provider order and active count without logging keys.
    A provider that returns HTTP 429/402/403/5xx, timeout, quota, or rate
    limit errors is temporarily cooled down in memory, so backfill bursts
    stop hammering the same exhausted free quota and move to the next
    enabled provider. Before AI duplicate detection, the collector now uses
    local text fingerprints to find only the top few likely duplicate
    candidates; unrelated recent posts are not sent into the prompt.
  - Every post logs `Skipping irrelevant post` or
    `AI-pruned advertisement` — the classifier judges the source content
    as irrelevant or promotional; the post is stored with
    `skipped_reason=irrelevant` or `skipped_reason=advertisement` and is
    intentionally not sent to approval.
    Check the source channel content or classification prompt if this is
    wrong.
  - `Approval message failed admin=...` — the admin has never opened the
    approval bot and pressed Start. Each admin must send `/start` to the
    approval bot once; the bot replies whether that user id is a
    configured admin. If every admin send fails, the approval queue item
    now fails and retries instead of being recorded as successfully sent.
  - `Skipping approval resend; approval already requested post=...` —
    startup or queue processing found a post that has already entered the
    approval stage. The bot intentionally does not resend it after restart,
    even if the old approval message is no longer tracked as active.
  - `Approval keyboard already current ...` — Telegram said the keyboard
    already had the requested markup. This is expected during config reloads
    and does not remove the tracked approval message.
- **`ConfigurationError: Configuration file not found`** — copy the example
  file to `config/configuration.json` or set `TELEGRAM_ADMIN_BOT_CONFIG`.
- **Persian text looks like `Ø³ÙØ§Ù` (Mojibake)** — a file was written
  without UTF-8. All project I/O uses `encoding="utf-8"`; make sure any
  manual edits keep the file UTF-8 and that services run with
  `PYTHONIOENCODING=utf-8` (already set in the service templates).
- **Collector exits immediately under systemd** — the Telethon session was
  never created; run `python -m src.workers.collector` interactively once.
- **Main bot waits for a Telegram login code** — the scheduler user session
  was never created. Run `python -m src.main` interactively once, enter the
  scheduler account phone/code when prompted, then restart the service
  normally. You may also set `telegram.scheduler_phone` for the first run.
- **Approval buttons answer "دسترسی غیرمجاز"** — the clicking user id is
  not listed in `telegram.admin_user_ids`.
- **Publishing fails with `TelegramPublishError`** — the main bot is not an
  admin of the destination channel, the scheduler/destination user session
  is not an admin, or the `chat_id` is wrong (channel ids are negative and
  usually start with `-100`). Transient Telethon disconnects are retried
  once automatically; persistent `Cannot send requests while disconnected`
  errors usually mean the session file is stale or the user session needs
  to be logged in again interactively.
- **Scheduled publishing fails** — the scheduler user account is not an
  admin in the destination channel, lacks post permission, or Telegram
  cannot resolve the destination `chat_id` from that user session. The app
  now reconnects the session before scheduled-history lookup and scheduling
  requests, then retries once after a disconnect.
- **All VPN tests fail with `xray binary not found`** — set
  `vpn_testing.xray_binary_path` on the Iran server.
- **Iran worker returns 401** — `worker_api_token` differs between the two
  servers' configuration files.
- **AI errors in logs** — check `ai.providers` order, enabled flags, keys,
  model names, and network reachability. The service tries the next enabled
  provider after quota/rate/payment/temporary provider failures such as
  `429`, `402`, `403`, `500`, `502`, `503`, or `504`. Timeouts are
  configurable per provider or through `ai.request_timeout_seconds`.
  OpenRouter can also route within its own configured model list when
  `fallback_models` is set; logs show `route=fallback` and `models_count`
  for those requests.
- **AI calls fail with `HTTP 400`** — the log line includes the provider,
  model name, and API response body. The usual cause is using a model name
  from another provider. Set the model on the matching `ai.providers` entry
  or leave the template defaults in place.
- **Published posts still contain the source channel's @username** — the
  destination channel needs a `public_id`; without it mentions cannot be
  rewritten (a warning is logged at publish time). Set it with
  `/setdest <chat_id> public_id @your_channel` in the management bot. The
  rewriter replaces mentions/links of all configured source identifiers
  plus the usernames the collector resolved for the source channels, then
  removes any remaining Telegram `@username`, `t.me/...`, or
  `telegram.me/...` mentions that do not point to the destination.
- **USD price job fails with `PriceFetchError: Nobitex request failed`** —
  `apiv2.nobitex.ir` is unreachable from the server (DNS/filtering). Either
  fix connectivity or switch to `usd_price.provider: "http_json"` with a
  custom `source_url` / `price_json_path`.
- **Posts never expire** — MongoDB TTL runs about once a minute; also
  confirm the `expires_at` index exists (`db.posts.getIndexes()`).
