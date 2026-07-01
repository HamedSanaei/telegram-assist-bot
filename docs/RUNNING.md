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
| `telegram.source_channels` | Usernames (`"@channel"`) or numeric ids to collect from |
| `telegram.destination_channels` | Objects: `chat_id`, `title`, `kind` (`news`/`breaking`/`technology`/`vpn`), `publish_usd_price` |
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
| `usd_price.source_url` / `price_json_path` | JSON endpoint and dotted path of the USD price value |

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

### Running the Main Bot (approval bot + queue worker + scheduler)

```bash
python -m src.main
```

This single process runs the approval bot (long polling), the SQLite queue
worker (VPN test dispatch + approval dispatch), and the scheduler (USD price
publishing and daily cleanup).

### Running the Approval Bot

The approval bot runs inside `src.main`; it has no separate entrypoint.

### Running the Collector

```bash
python -m src.workers.collector
```

On first run Telethon asks for a phone number and login code and stores the
session at `telegram.collector_session`. Run the first login interactively
before enabling the systemd service.

### Running the Scheduler

The scheduler runs inside `src.main`. Times are configured in the
`scheduler` section (`Asia/Tehran` by default).

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

- `publish/windows/` — standalone executables (`telegram-admin-bot.exe`,
  `telegram-collector.exe`, `iran-vpn-worker.exe`) plus the configuration
  template and a `README.txt`. Put `config/configuration.json` next to the
  executables before running them. Windows-only build step.
- `publish/ubuntu/telegram-admin-bot-<version>.tar.gz` — source bundle for
  Ubuntu servers. Extract it and run `sudo bash install.sh [main|collector|worker|all]`
  to install to `/opt/telegram-admin-bot`, create the virtualenv, and copy
  the systemd units.
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

# Main server
sudo cp deploy/telegram-admin-bot.service /etc/systemd/system/
sudo cp deploy/telegram-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-admin-bot telegram-collector

# Iran server
sudo cp deploy/iran-vpn-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iran-vpn-worker
```

Check logs:

```bash
journalctl -u telegram-admin-bot -f
journalctl -u telegram-collector -f
journalctl -u iran-vpn-worker -f
```

Note: run the collector once interactively before enabling its service so
the Telethon session file exists.

## Troubleshooting

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
- **Posts never expire** — MongoDB TTL runs about once a minute; also
  confirm the `expires_at` index exists (`db.posts.getIndexes()`).
