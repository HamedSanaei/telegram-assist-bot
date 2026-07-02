# Running the Telegram Admin Bot

## Requirements

- Python 3.12 or newer
- MongoDB 5 or newer (local or remote)
- SQLite (bundled with Python)
- Telegram credentials:
  - Main bot token (publishes to destination channels; must be admin there)
  - Approval bot token (talks to admins)
  - API ID and API hash from https://my.telegram.org (for the collector
    user session that reads source channels)
- z.ai API key (primary AI provider) and DeepSeek API key (fallback)
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
| `telegram.bot_token` | Main publishing bot token |
| `telegram.approval_bot_token` | Approval assistant bot token |
| `telegram.api_id` / `api_hash` | Telegram API credentials for the collector |
| `telegram.collector_session` | Telethon session file path (default `data/collector`) |
| `telegram.collector_daily_backfill_max_messages` | Maximum messages scanned from each source for the current Gregorian day when the collector starts (default `5000`; set `0` to disable) |
| `telegram.source_refresh_seconds` | How often the running collector reloads the source channel list from SQLite (default `60`; set `0` to disable live refresh) |
| `telegram.source_channels` | Usernames (`"@channel"`) or numeric ids to collect from (initial seed; manage at runtime with the management bot) |
| `telegram.destination_channels` | Objects: `chat_id`, `title`, `public_id`, `kind` (`news`/`breaking`/`technology`/`vpn`), `publish_usd_price`, `post_interval_minutes` (minimum minutes between scheduled posts on that channel, default `30`). `chat_id` is the numeric Telegram id of the channel (negative, usually starting with `-100`); forward a channel post to `@userinfobot` or open the channel in Telegram Web and prefix the number in the URL with `-100` to find it. `public_id` is the public destination handle/link (for example `@my_channel`) used to replace source-channel mentions before publishing. The main bot must be an admin of every destination channel. |
| `telegram.admin_user_ids` | Telegram user ids allowed to approve posts |
| `ai.*` | Provider keys, optional base URL / model overrides, timeouts |
| `database.sqlite_path` | SQLite file (default `data/app.db`) |
| `database.mongodb_connection_string` | e.g. `mongodb://localhost:27017` |
| `storage.media_directory` | Downloaded media location |
| `storage.retention_days` | Post retention (default 14) |
| `vpn_testing.worker_api_url` | Iran worker base URL, e.g. `http://1.2.3.4:8088` |
| `vpn_testing.worker_api_token` | Shared secret for the worker API |
| `vpn_testing.xray_binary_path` | xray path on the Iran server |
| `scheduler.usd_price_publish_times` | e.g. `["09:00", "21:00"]` (in `scheduler.timezone`) |
| `usd_price.provider` | `"nobitex"` (default; free-market USDT rate from the public Nobitex API, no key needed, published in Toman) or `"http_json"` for a custom endpoint |
| `usd_price.source_url` / `price_json_path` | Only for `provider: "http_json"`: JSON endpoint and dotted path of the USD price value |

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

Do the first collector login separately (`python -m src.workers.collector`)
before using this entrypoint, because the interactive Telethon prompt would
otherwise stall the approval bot during login.

The Iran VPN worker is not part of this command; it runs on the Iran server
(see below).

### Running the Main Bot (approval bot + queue worker + scheduler)

```bash
python -m src.main
```

This single process runs the approval bot (long polling), the SQLite queue
worker (VPN test dispatch + approval dispatch + scheduled channel publishing),
and the scheduler (USD price publishing and daily cleanup). It also polls the
main management bot (`telegram.bot_token`) for admin commands.

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

`configuration.json` only **seeds** the channel lists on first start; after
that, SQLite (edited through these commands) is the source of truth and
bot-made changes survive restarts. Admin ids remain config-only.

### Running the Approval Bot

The approval bot runs inside `src.main`; it has no separate entrypoint.

Every approval message starts in **scheduled mode** (first keyboard row
shows the current delivery mode). In scheduled mode, confirming a channel
puts the post into that channel's paced queue: posts are published in
order with at least `post_interval_minutes` between them (counting from
the channel's last published or last queued post). Tapping the toggle
switches the message to **immediate mode**, where confirmation publishes
right away. Channels with a queued post show ⏱ on the keyboard, published
ones show ✅; neither can be selected twice.

### Running the Collector

```bash
python -m src.workers.collector
```

On first run Telethon asks for a phone number and login code and stores the
session at `telegram.collector_session`. Run the first login interactively
before enabling the systemd service.

At startup the collector scans from the first message of the current
Gregorian day in `scheduler.timezone` (Asia/Tehran by default) for every
source channel, then waits for live updates. Messages are processed
oldest-first so same-day history enters the same dedup/classify/store
pipeline as live messages. The normal exact-hash and AI deduplication
prevents restarts, cross-channel reposts, and already processed posts from
being stored twice. `telegram.collector_daily_backfill_max_messages` is only
a safety cap per source; increase it for very high-volume channels or set it
to `0` only when you want a strict live-only listener.

While running, the collector reloads the source channel list from SQLite
every `telegram.source_refresh_seconds` seconds. Sources added with the
management bot's `/addsource` (or seeded from `configuration.json` at
startup) are resolved and backfilled from today's first message without
restarting the process; sources removed with `/delsource` stop being
collected on the next refresh.

When the collector resolves a source channel, it stores the channel title
and username in SQLite. Approval previews then show that readable source
label instead of the raw `-100...` chat id. The collector downloads photos,
videos, and documents into `storage.media_directory`; approval previews and
destination publishing send the first available media file with the post
caption, falling back to a text-only message when no media file exists.

### Running the Scheduler

The scheduler runs inside `src.main`. Times are configured in the
`scheduler` section (`Asia/Tehran` by default). The USD price job uses
the source selected by `usd_price.provider` — with the default
`"nobitex"` no further price configuration is required.

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
  | `Duplicate check passed ... provider=...` | AI dedup done (skips log `Skipping ... duplicate` instead) |
  | `Classified ... category=... provider=...` | AI classification done (`irrelevant` posts stop here by design) |
  | `Classification unavailable; storing for manual approval ...` | AI failed after fallback; post is still stored for admin review |
  | `Saved post to MongoDB id=...` | Post inserted into MongoDB |
  | `Enqueued approval_request post=...` / `Enqueued vpn_test post=...` | Queued for the next stage |
  | `Queue item done id=... type=approval_request` | Approval message sent to admins |

  Common causes when the chain stops early:
  - Only `telethon.client.updates | Got difference ...` appears, with no
    `Received live/backfill message ...` lines — Telethon only synchronized
    account/channel state; the application did not receive a source-channel
    message event. Confirm `telegram.source_channels` contains the exact
    source channels you want, and keep
    `collector_daily_backfill_max_messages` above `0` so today's source
    posts are scanned on startup.
  - `Classification unavailable; storing for manual approval ...` — both AI
    providers failed (for example z.ai returned 429 and DeepSeek returned
    402/payment required). The post is kept and sent to admins with a
    conservative default category instead of being dropped. Check the
    `Effective configuration` block logged at startup: it shows whether
    each API key is `set` or `EMPTY` (values are never logged).
  - Every post logs `Skipping irrelevant post` — the classifier judges the
    source content as ads/spam; check the source channel content.
  - `Approval message failed admin=...` — the admin has never opened the
    approval bot and pressed Start. Each admin must send `/start` to the
    approval bot once; the bot replies whether that user id is a
    configured admin.
- **`ConfigurationError: Configuration file not found`** — copy the example
  file to `config/configuration.json` or set `TELEGRAM_ADMIN_BOT_CONFIG`.
- **Persian text looks like `Ø³ÙØ§Ù` (Mojibake)** — a file was written
  without UTF-8. All project I/O uses `encoding="utf-8"`; make sure any
  manual edits keep the file UTF-8 and that services run with
  `PYTHONIOENCODING=utf-8` (already set in the service templates).
- **Collector exits immediately under systemd** — the Telethon session was
  never created; run `python -m src.workers.collector` interactively once.
- **Approval buttons answer "دسترسی غیرمجاز"** — the clicking user id is
  not listed in `telegram.admin_user_ids`.
- **Publishing fails with `TelegramPublishError`** — the main bot is not an
  admin of the destination channel, or the `chat_id` is wrong (channel ids
  are negative and usually start with `-100`).
- **All VPN tests fail with `xray binary not found`** — set
  `vpn_testing.xray_binary_path` on the Iran server.
- **Iran worker returns 401** — `worker_api_token` differs between the two
  servers' configuration files.
- **AI errors in logs** — check keys and network reachability of z.ai; the
  system falls back to DeepSeek automatically, so both failing means both
  keys/endpoints are broken. Timeouts are configurable via
  `ai.request_timeout_seconds`.
- **USD price job fails with `PriceFetchError: Nobitex request failed`** —
  `apiv2.nobitex.ir` is unreachable from the server (DNS/filtering). Either
  fix connectivity or switch to `usd_price.provider: "http_json"` with a
  custom `source_url` / `price_json_path`.
- **Posts never expire** — MongoDB TTL runs about once a minute; also
  confirm the `expires_at` index exists (`db.posts.getIndexes()`).
