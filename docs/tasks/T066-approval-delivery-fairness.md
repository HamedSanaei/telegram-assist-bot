# T066 — Approval delivery fairness and continuous queue processing

## Status

Completed

## Goal

Prevent failed approval proposals from consuming the historical startup budget or starving healthy proposals, keep delivery active for proposals created after startup, and provide safe queue inspection and explicit retry commands.

## Requirement references

- `docs/REQUIREMENTS.md`: 5.12–5.19, 13, 14, 16

## Dependencies

- T020–T026
- T061–T065

## Scope

- Count only unique successfully completed historical proposals against the startup cap.
- Use a startup watermark so new proposals remain continuously eligible.
- Add fair durable claim ordering, bounded backoff, permanent failure, and per-administrator progress.
- Add safe delivery diagnostics and idle/resumed events.
- Add read-only `approval-queue` and explicit idempotent `approval-retry` CLI commands.
- Preserve restart-safe phased content/control-card delivery.

## Out of scope

- Live Telegram calls or modification of local production data.
- Publication, scheduling, AI, ingestion, or album-finalization redesign.
- Changes to `config/configuration.local.json`.

## Expected files or modules

- `src/telegram_assist_bot/application/operational_approval.py`
- `src/telegram_assist_bot/application/ports/operational_approval.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/operational_approval_repository.py`
- `src/telegram_assist_bot/bootstrap/approval_bot.py`
- `src/telegram_assist_bot/bootstrap/approval_queue.py`
- `src/telegram_assist_bot/bootstrap/cli.py`
- configuration models/example and focused unit/integration tests
- repository project-memory documentation

## Implementation notes

- MongoDB remains the durable source of truth.
- Retry scheduling must be fair by next due time, creation time, and stable ID.
- Existing successful administrator delivery references must never be resent.
- Structured logs must use only non-reserved safe fields and never include content, media paths, tokens, callback data, or raw Telegram errors.

## Acceptance criteria

- Retries, deferrals, completed records, and permanent failures consume no startup success slots.
- A failed proposal cannot immediately monopolize the claim order.
- New proposals after the startup watermark are delivered for the complete process lifetime.
- Retry delay is bounded and permanent failures are terminal until an explicit retry.
- Delivery progress and failure diagnostics are retained per administrator.
- Queue inspection is read-only and explicit retry preserves successful administrators.

## Unit tests

- Startup success accounting, watermark behavior, worker lifetime, retry backoff, permanent failure, per-administrator recovery, safe events, and CLI validation/output.
- Failure isolation for text, photo, video, document, and album content kinds.

## Integration tests

- MongoDB fair ordering, leases, retry scheduling, watermark filtering, permanent failure, explicit retry, and restart recovery.

## Verification commands

```text
uv run --python 3.12 pytest <focused approval tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
TEST_MONGODB_URI=mongodb://127.0.0.1:27017/?directConnection=true uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
```

## Documentation updates

- Update `README.md`, `docs/ARCHITECTURE.md`, `docs/CODE_MAP.md`, `docs/ROADMAP.md`, and `docs/STATUS.md` when implementation completes.

## Definition of done

All acceptance criteria and focused/full non-live verification pass with no live Telegram calls or local production-data mutation; T066 is Completed and T034 is restored as the only Active task.

## Verification results

- Focused approval unit tests: passed.
- MongoDB approval runtime integration tests: `8 passed`.
- Complete non-live suite: `911 passed`, `0 skipped`, exit code `0`.
- Branch coverage: `90.3752899009066%`.
- Ruff, formatting, mypy, lock check, diff check, changed-text integrity, CLI help, build, distribution validation, and tracked-file secret scan: passed.
