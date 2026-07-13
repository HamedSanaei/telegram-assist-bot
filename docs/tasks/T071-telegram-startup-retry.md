# T071 — Telegram startup retry

## Status

Completed

## Goal

Retry safe Telegram validation and initial session connection when Runtime starts
during a transient network outage.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.1`–`5.3`, `13`, `16`.
- `docs/ARCHITECTURE.md`: startup ordering, retry and single-session ownership.

## Dependencies

- T005–T012 and T060–T070 are Completed.

## Scope

- Propagate transient channel-resolution failures for retry.
- Apply the existing bounded ingestion retry policy to startup validation.
- Apply the same policy to opening the owned runtime client.
- Emit safe existing retry events and preserve one session owner.
- Keep configuration, authorization and permission failures fail-fast.

## Out of scope

- Reading or changing local configuration.
- Infinite retry or daemon restart management.
- Live Telegram calls in automated tests.
- Changes to ingestion, approval or publication business rules.

## Expected files or modules

- Telegram validation use case and text-ingestion composition root.
- Focused validation/startup tests.
- Architecture, Code Map, Roadmap and Status.

## Implementation notes

- Both operations are read/connect-only and explicitly safe to retry.
- Retry attempts, delay and cap come from typed ingestion configuration.
- Raw network errors, phone data and session paths are never logged.

## Acceptance criteria

- A transient account-validation failure is retried before startup fails.
- A transient channel-resolution failure retries the complete validation snapshot.
- A transient initial `open` failure is retried on the same gateway/session owner.
- Permanent validation failures are not retried.
- Exhausted retries preserve the original exception cause and close resources.

## Required unit tests

- Transient resolution propagates and permanent resolution aggregates.
- Validation and open recover on a later bounded attempt.
- Exhaustion and permanent failure remain safe.

## Required integration tests

- Existing non-live startup/restart suite remains green.

## Verification commands

```text
uv run --python 3.12 pytest tests/unit/application/test_validate_telegram_session.py tests/unit/test_text_ingestion_bootstrap.py
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
```

## Required documentation updates

- Update Roadmap, Status, Architecture and Code Map.

## Definition of done

Focused and non-live verification pass, T071 is Completed and T034 is restored
as the only Active task.

## Verification results

- Focused validation/startup suite: `30 passed`.
- Complete non-live suite with local MongoDB: `933 passed`, `0 skipped`, branch
  coverage `90.04%`.
- Ruff, format, mypy and lock checks: passed.
- No local configuration was read or changed and no live Telegram request was
  made.
