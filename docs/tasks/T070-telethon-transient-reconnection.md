# T070 — Telethon transient reconnection

## Status

Completed

## Goal

Keep the operational runtime alive across bounded transient Telegram transport
disconnects so live source updates resume on the same owned Telethon session.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.1`–`5.3`, `13`, `16`.
- `docs/ARCHITECTURE.md`: Telegram User API ownership and runtime supervision.

## Dependencies

- T007–T012 and T060–T069 are Completed.

## Scope

- Enable bounded Telethon automatic transport reconnection.
- Reuse the existing ingestion reconnect attempt and delay settings.
- Preserve one client, one session lock and existing event handlers.
- Keep exhausted reconnects as critical infrastructure failures.
- Add focused regression tests and safe operational documentation.

## Out of scope

- Reading or changing local configuration.
- Joining source channels automatically.
- Live Telegram calls in automated tests.
- Changing crawl, approval, publication or AI business behavior.

## Expected files or modules

- Telethon session adapter and runtime composition.
- Session and runtime bootstrap tests.
- Architecture, Code Map, Roadmap and Status.

## Implementation notes

- Reconnection is delegated to the already-owned Telethon client; no replacement
  client or competing session owner may be created.
- Retry attempts and delay remain bounded by typed ingestion configuration.
- The `disconnected` signal remains critical after Telethon exhausts retries.

## Acceptance criteria

- Production runtime clients enable `auto_reconnect` and receive configured bounds.
- A recoverable transport interruption does not complete the disconnected signal.
- Existing handlers remain registered on the same client after reconnect.
- Exhausted reconnects still terminate Runtime with an infrastructure failure.
- No secret or raw Telegram update is logged.

## Required unit tests

- Concrete client factory maps bounded reconnect arguments and enables reconnect.
- Runtime composition passes ingestion reconnect policy to the session owner.
- Existing critical-disconnect supervision remains covered.

## Required integration tests

- Non-live fake-client lifecycle proves no second User API client/session owner.

## Verification commands

```text
uv run --python 3.12 pytest tests/unit/infrastructure/telegram/user/test_session_adapter.py tests/unit/test_text_ingestion_bootstrap.py
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
```

## Required documentation updates

- Update Roadmap, Status, Architecture and Code Map.

## Definition of done

The reconnect regression and quality gates pass, T070 is Completed and T034 is
restored as the only Active task.

## Verification results

- Focused session/runtime suite: `38 passed`.
- Complete non-live suite with local MongoDB: `932 passed`, `0 skipped`, branch
  coverage `90.07%`.
- Ruff, format, mypy and lock checks: passed.
- No local configuration was read or changed and no live Telegram request was
  made.
