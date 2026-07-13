# T067 — Approval media delivery and native Telegram scheduling

## Status

Completed

## Goal

Deliver approval media with its real type and confined path, drain historical approvals in rolling batches, and schedule new scheduled selections directly in Telegram Scheduled Messages through the runtime-owned Telethon client.

## Requirement references

- `docs/REQUIREMENTS.md`: 5.12–5.19, 13, 14, 16

## Dependencies

- T020–T033
- T060–T066

## Scope

- Resolve approval media beneath the configured private media root and use type-correct Aiogram methods.
- Preserve captions, entities and safe failure subtypes with upload-specific timeout.
- Replace the lifetime historical cap with rolling batches and live-first claiming.
- Add a separate native-scheduling outbox, per-destination lease and Telethon worker.
- Persist native scheduled message identifiers and exact UTC due time.
- Cancel native schedules through Telegram before a scheduled-to-immediate follow-up.
- Keep legacy internal scheduled jobs inert and unchanged.

## Out of scope

- Reading or modifying `config/configuration.local.json`.
- Migrating, executing, cancelling or deleting existing internal scheduled jobs.
- Live Telegram calls in automated tests.
- AI features, advertisements, dashboard or unrelated refactoring.

## Expected files or modules

- Approval application ports/services and operational orchestration.
- Aiogram approval adapter and MongoDB approval loader.
- Native scheduling domain/application ports, MongoDB repository and Telethon adapter.
- Approval-bot and operational-runtime composition roots.
- Typed example configuration, tests and project-memory documentation.

## Implementation notes

- Approval album preview sends the first Photo, or the first real member when no Photo exists; final native album scheduling retains every member.
- Historical batches contain ten successful proposals and pause for ten seconds; live proposals always take priority.
- Native due time is exactly five minutes after `max(now, latest Telegram scheduled time)`.
- Expired work after the Telegram request starts becomes outcome-unknown instead of being resent.
- The legacy `schedule-worker` command fails closed before opening a User API session.

## Acceptance criteria

- Text, photo, video, animation, document and album approval previews use valid confined paths and correct Bot API methods.
- Useful safe media failure types reach WARNING/ERROR structured events without raw provider details.
- Historical backlog continues automatically across rolling batches and failures cannot starve healthy work.
- New scheduled selections appear in Telegram Scheduled Messages through the shared runtime client.
- External scheduled messages participate in slot calculation and local scheduling is serialized per destination.
- Native schedule IDs and UTC due time persist; cancellation and scheduled-to-immediate transitions are restart-safe.
- Python due workers never claim legacy scheduled jobs.

## Required unit tests

- Media path validation, type mapping, captions/entities, album preview and upload timeout.
- Safe log level/subtype behavior.
- Live-first rolling batches and failure accounting.
- Native due calculation, state transitions, payload mapping and cancellation follow-up.

## Required integration tests

- MongoDB native command uniqueness, destination leases, concurrent workers, retry/restart and cancellation races.
- Non-live Telethon scheduling and deletion for text/media/album.
- Callback-to-runtime-to-approval-card flow and legacy job inactivity.

## Verification commands

```text
uv run --python 3.12 pytest <focused tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
TEST_MONGODB_URI=mongodb://127.0.0.1:27017/?directConnection=true uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
```

## Required documentation updates

- README, Architecture, Code Map, Decisions, Roadmap, Status and example configuration.

## Definition of done

All acceptance criteria and non-live verification pass without touching local configuration or real Telegram/MongoDB data; T067 is Completed and T034 is restored as the only Active task.

## Verification results

- Focused unit and MongoDB/Telegram-fake tests: `39 passed`.
- Complete non-live suite: `924 passed`, `0 skipped`, branch coverage `90.14%`.
- Ruff check, Ruff format check, mypy, lock check and `git diff --check`: passed.
- Changed-file UTF-8/Persian/mojibake validation and secret scan: passed.
- Wheel/sdist build, distribution validation, CLI help and import smoke: passed.
- Legacy `schedule-worker` failed closed before configuration or User API startup.
