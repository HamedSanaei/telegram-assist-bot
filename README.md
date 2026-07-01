# Telegram Admin Bot

A Python-based Telegram channel administration and publishing system.

## What It Does

The system collects posts from configured source Telegram channels, removes
duplicates and classifies them with AI (z.ai primary, DeepSeek fallback),
tests vmess/vless VPN configurations from an Iran-based worker server, asks
administrators for approval through a dedicated approval bot, and publishes
approved posts to configured destination channels. It also publishes USD
price updates twice per day with the change compared to the previous record.

## Main Features

- Post collection from source channels via Telethon (user session).
- Exact-hash plus AI near-duplicate detection before storing anything.
- AI classification into general news / breaking news / technology / VPN /
  VPN config / irrelevant, with automatic z.ai → DeepSeek fallback.
- vmess/vless config extraction and connectivity testing on an Iran server
  through a token-protected worker API (xray-core based).
- Admin approval bot with per-channel inline buttons, final confirmation,
  and ✅ marks after successful publishing; duplicate publishing is blocked.
- USD price publishing twice per day with change vs. the previous price.
- 14-day retention: MongoDB TTL index plus a daily cleanup job.

## Architecture

Clean Architecture with strict inward-pointing dependencies:

```text
presentation -> application -> domain
workers      -> application -> domain
infrastructure -> application/domain interfaces
```

- **SQLite** — channels, admins, queue, publish log, price history, settings.
- **MongoDB** — raw posts, media metadata, AI results, extracted configs
  (TTL-expired after 14 days).
- **configuration.json** — all secrets and tunables (never committed).

See [docs/CODE_MAP.md](docs/CODE_MAP.md) for the full code map.

## Requirements

- Python 3.12+
- MongoDB 5+
- An Ubuntu server in Iran with xray-core installed (for VPN testing)
- Telegram bot tokens (main bot + approval bot) and API ID/hash

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/configuration.example.json config/configuration.json
# fill in config/configuration.json
python -m src.workers.collector   # terminal 1 (first run asks for Telegram login)
python -m src.main                # terminal 2
```

## Configuration

Copy `config/configuration.example.json` to `config/configuration.json` and
fill in the values. The real file is git-ignored. Every key is documented in
[docs/RUNNING.md](docs/RUNNING.md).

## Testing

```bash
pip install -r requirements-dev.txt
pytest              # everything
pytest tests/unit   # unit tests only
```

## Deployment

systemd service templates live in `deploy/`; installation steps are in
[docs/RUNNING.md](docs/RUNNING.md).

Release outputs are built with `python scripts/build_publish.py`, which
regenerates `publish/windows/` (standalone executables) and
`publish/ubuntu/` (a tar.gz bundle with an `install.sh`) on every run.
