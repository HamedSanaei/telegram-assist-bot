# T073 — Link-preview ingestion isolation

## Status

Completed

## Goal

Prevent Telegram link previews and malformed per-message content from terminating
live ingestion or the operational runtime.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.2`, `5.3`, `13`, `14`, `16`.
- `docs/ARCHITECTURE.md`: Telegram live ingestion and media boundaries.

## Dependencies

- T007–T012 and T060–T072 are Completed.

## Scope

- Treat only `MessageMediaPhoto` and `MessageMediaDocument` as downloadable.
- Preserve `MessageMediaWebPage` as ordinary text with entities.
- Resolve downloads from the concrete `message.photo` or `message.document`.
- Isolate safe mapping, content and media failures per message.
- Keep real subscription and Telegram connection failures supervised.
- Remove raw exception messages from live-listener logs.

## Out of scope

- Local configuration changes or live Telegram tests.
- Publication, approval, AI or scheduling behavior.
- Broad ingestion redesign.

## Expected files or modules

- Telegram source port, mapper, media adapter and live adapter.
- Live listener worker and focused unit/contract tests.
- Architecture, Code Map, Roadmap and Status.

## Implementation notes

- SDK objects remain inside Infrastructure.
- Per-message failures expose only safe identity, category and exception type.
- Connection failures retain bounded reconnect and critical supervision behavior.

## Acceptance criteria

- A webpage preview never reaches `iter_download`.
- Photo and Document streams use their concrete Telegram objects.
- A malformed mapped or processed message is logged and skipped while the next
  message is processed on the same subscription.
- Raw exception messages and payloads are absent from logs.
- A real connection failure still reconnects or terminates according to policy.

## Required unit tests

- Webpage preview maps to normal text.
- Photo and Document mapping and concrete download selection.
- Malformed mapping and processing failures do not stop later messages.
- Safe logging contains no raw exception message.

## Required integration tests

- Existing live-listener and runtime ingestion suites remain green.

## Verification commands

```text
uv run --python 3.12 pytest tests/unit/infrastructure/telegram/user tests/unit/workers/test_live_text_listener.py tests/contract/telegram/test_live_message_contract.py
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
```

## Required documentation updates

- Update Architecture, Code Map, Roadmap, Status and this task.

## Definition of done

Focused and complete non-live verification pass, T073 is Completed and T034 is
restored as the only Active task.

## Verification results

- Focused mapper/media/live contract suite: `30 passed`.
- MongoDB ingestion integration suite: `5 passed`.
- Full non-live suite: `936 passed`, `0 skipped`, branch coverage `90.06%`.
- Ruff check, Ruff format, Mypy and `uv lock --check`: passed.
