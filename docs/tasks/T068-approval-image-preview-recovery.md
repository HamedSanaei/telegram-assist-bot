# T068 — Approval image preview recovery

## Status

Completed

## Goal

Recover the visual approval preview for legacy or incomplete image metadata so a
stored image is sent as a Bot API photo instead of a hash-named document.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.12`–`5.16`, `13`, `14`, `16`.
- `docs/ARCHITECTURE.md`: approval delivery and media preservation boundaries.

## Dependencies

- T020–T026 and T067 are Completed.

## Scope

- Recover a safe preview kind from persisted MIME metadata and bounded file
  signatures when a legacy record only says `Document`.
- Supply a safe non-hash upload filename when the original filename is absent.
- Preserve caption/entities, path confinement, timeout behavior and publication
  payload identity.
- Add focused Bot API boundary regression tests.

## Out of scope

- Rewriting MongoDB media records.
- Changing final publication media semantics.
- Reading local configuration, contacting Telegram or modifying live data.
- AI work from T034 onward.

## Expected files or modules

- `src/telegram_assist_bot/infrastructure/telegram/bot/adapter.py`
- `tests/unit/presentation/bot/test_bot_boundary.py`
- Project roadmap, status and this task file.

## Implementation notes

- Detection reads only a small fixed header from an already confined regular
  file.
- Only well-known image/animation signatures are promoted; arbitrary documents
  remain documents.
- Canonical stored files and publication metadata are immutable.

## Acceptance criteria

- A legacy JPEG/PNG record marked `Document` is previewed with `send_photo`.
- A legacy GIF record marked `Document` is previewed with `send_animation`.
- The upload filename is safe and never exposes the SHA-256 storage name.
- A real arbitrary document remains a document.
- Caption and Telegram entities remain unchanged.

## Required unit tests

- Legacy JPEG with MIME metadata and missing original filename.
- Legacy image with missing MIME recovered from file signature.
- GIF recovery and arbitrary-document fallback.
- Existing explicit photo/video/animation/document mappings remain valid.

## Required integration tests

- None; this regression is isolated at the Bot API adapter boundary with a fake
  Bot and real temporary files.

## Verification commands

```text
uv run --python 3.12 pytest tests/unit/presentation/bot/test_bot_boundary.py -q
uv run --python 3.12 ruff check src tests
uv run --python 3.12 ruff format --check src tests
uv run --python 3.12 mypy src tests scripts
git diff --check
```

## Required documentation updates

- Update Roadmap and Status; update architecture/code map only if boundaries
  change.

## Definition of done

The regression tests and required quality gates pass, T068 is Completed and
T034 is restored as the only Active task.

## Verification results

- Focused Bot API boundary suite: `6 passed`.
- Complete non-live suite with local MongoDB: `925 passed`, `0 skipped` and
  branch coverage `90.18%`.
- Ruff check, Ruff format check and mypy: passed.
- No live Telegram request, local configuration read or live-data mutation was
  performed.
