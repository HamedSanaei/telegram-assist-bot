# T072 — Approval sync Telegram server retry

## Status

Completed

## Goal

Prevent a transient Telegram Bot API server failure from terminating the durable
approval synchronization worker or the complete Approval Bot.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.12`–`5.16`, `13`, `16`.
- `docs/ARCHITECTURE.md`: durable approval synchronization and Bot supervision.

## Dependencies

- T020–T026 and T061–T071 are Completed.

## Scope

- Map Aiogram `TelegramServerError` to safe application-owned transient outcomes.
- Persist failed edits for retry through the existing sync state.
- Keep the sync loop alive while polling independently retries.
- Add focused adapter, synchronization and supervisor regressions.

## Out of scope

- Reading or changing local configuration.
- Live Telegram calls in tests.
- Changing proposal content, callbacks or publication behavior.

## Expected files or modules

- Aiogram Bot adapter and approval synchronization tests.
- Architecture, Code Map, Roadmap and Status.

## Implementation notes

- Raw Telegram error messages are never logged or persisted.
- The existing one-second durable retry state remains the source of truth.
- Permanent Bot rejections retain their existing behavior.

## Acceptance criteria

- A Bot API 5xx edit failure becomes retryable sync state.
- The `approval-sync` task does not terminate on that failure.
- Successful later retry marks all references current.
- Delivery operations classify the same server error as transient.
- No reserved fields or secrets enter structured logs.

## Required unit tests

- Adapter mapping for edit and delivery server errors.
- Durable sync failure then successful retry.
- Approval Bot supervision does not observe sync task termination.

## Required integration tests

- Existing non-live approval runtime suite remains green.

## Verification commands

```text
uv run --python 3.12 pytest tests/unit/presentation/bot/test_bot_boundary.py tests/unit/application/test_operational_approval.py tests/unit/test_approval_bot_runtime_bootstrap.py
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
```

## Required documentation updates

- Update Roadmap, Status, Architecture and Code Map.

## Definition of done

Focused and non-live verification pass, T072 is Completed and T034 is restored
as the only Active task.

## Verification results

- Focused approval suite: `51 passed`.
- Ruff check and format check: passed.
- Mypy: `Success: no issues found in 229 source files`.
- Lock validation: passed.
- Complete non-live suite: `933 passed`, `0 skipped`, branch coverage `90.04%`.
