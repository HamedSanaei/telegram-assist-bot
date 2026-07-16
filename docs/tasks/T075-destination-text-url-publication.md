# T075 — Destination text URL publication recovery

## Status

Completed

## Goal

Preserve `text_url` metadata from Telegram ingestion through prepared destination
payloads, safely omit only malformed legacy link entities, and recover explicitly
proven pre-send publication failures without risking duplicate Telegram sends.

## Requirement references

- `docs/REQUIREMENTS.md`: 5.17–5.19, 13, 14, 16

## Dependencies

- T027–T033, T060–T074 — Completed

## Scope

- Add optional URL metadata to the SDK-independent Telegram entity.
- Persist and rebase URL metadata without changing visible text or UTF-16 bounds.
- Map valid links to Telethon `MessageEntityTextUrl`.
- Omit only legacy `text_url` entities whose URL metadata is absent.
- Type all publisher validation failures before the Telegram send boundary.
- Provide exact, proof-gated recovery for affected immediate jobs.

## Out of scope

- Approval Bot document-preview behavior from T074.
- AI, scheduling policy, media serialization, or Telegram session changes.
- Automatic recovery of ambiguous or unproven publication outcomes.

## Expected files or modules

- domain Telegram entity and safe errors
- Telegram message mapper and User API publisher
- MongoDB post/content/approval serializers and content rebasing
- publication recovery CLI and focused tests

## Implementation notes

Legacy entities deliberately allow `url=None`. The publisher filters only that
entity and emits an allowlisted reason. Other validation failures become permanent
`PublisherError` values with `request_may_have_reached_telegram=False`. Recovery
requires an exact Post ID, expired claim, matching historical failure metadata, no
published identifiers, and a prepared legacy `text_url` without URL metadata.

## Acceptance criteria

- Valid URL metadata round-trips and reaches `MessageEntityTextUrl` unchanged.
- Persian text and UTF-16 offsets remain exact.
- Legacy missing URL does not block publication or remove visible text.
- Invalid pre-send payloads cannot become `OutcomeUnknown` or call Telegram.
- Network/RPC uncertainty remains ambiguous.
- Recovery is exact and idempotent.

## Required unit tests

- Domain URL validation and legacy compatibility.
- Mapper and rebase URL preservation.
- Publisher valid, legacy, bounds, and no-send behavior.
- CLI exact Post ID validation and dispatch.

## Required integration tests

- MongoDB prepared artifact and publication payload round-trip.
- Proof-gated recovery idempotency.
- Telethon mapping with Persian UTF-16 offsets.

## Verification commands

```powershell
$env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017/?directConnection=true"
$env:UV_CACHE_DIR = Join-Path $PWD ".uv-cache"
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
git diff --check
```

## Documentation updates

- `README.md`, `docs/ARCHITECTURE.md`, `docs/CODE_MAP.md`
- `docs/ROADMAP.md`, `docs/STATUS.md`, and this task

## Definition of done

All acceptance criteria and verification commands pass, tests make no live Telegram
request or production mutation, T075 is Completed, and T034 is the only Active task.
