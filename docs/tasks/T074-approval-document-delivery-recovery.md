# T074 — Approval document delivery recovery

## Status

Completed

## Goal

Deliver prepared Document proposals, including `.npvt` files, without losing
valid Persian captions and provide a safe bounded recovery path for existing
terminal `media_rejected` deliveries.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.12`–`5.16`, `13`, `14`, `16`.
- `docs/ARCHITECTURE.md`: phased approval delivery and Bot API boundary.

## Dependencies

- T020–T026 and T061–T073 are Completed.

## Scope

- Trace and validate Document file, filename, size, caption and entity mapping.
- Classify Bot API document rejection with safe reason codes.
- Retry exactly once without preview entities only for definite entity rejection.
- Preserve visible caption text and canonical publication entities.
- Add explicit bounded/dry-run recovery for terminal Document media rejections.

## Out of scope

- Publication, ingestion, scheduling or AI behavior.
- Live Telegram calls in automated tests.
- Broad approval queue redesign or automatic recovery of unrelated failures.

## Expected files or modules

- Admin approval port and Aiogram Bot adapter.
- Operational approval repository and CLI recovery command.
- Focused unit and MongoDB integration tests.
- Architecture, Code Map, Roadmap and Status.

## Implementation notes

- Raw Telegram errors, captions, full paths and complete filenames remain secret.
- Recovery never resets already successful administrator deliveries.
- Retry without entities is allowed only before terminal persistence.

## Acceptance criteria

- `.npvt` Document proposals work with no caption and Persian captions.
- Valid entities are preserved; invalid ranges are rejected safely before Bot API.
- Custom Emoji is removed only from the admin preview entity list.
- A definite entity-related 400 performs one entity-free retry with unchanged text.
- Missing, empty and unreadable files have explicit safe failures.
- Existing terminal Document `media_rejected` records can be selected by exact
  Post ID or bounded time range with dry-run and maximum limit.
- Photo and Video delivery remain unchanged.

## Required unit tests

- Document filename/path/size/caption/entity cases and safe rejection mapping.
- Entity fallback idempotency and no fallback for ambiguous failures.
- CLI validation and dry-run output safety.

## Required integration tests

- MongoDB recovery query updates only matching terminal administrator states.
- Existing approval delivery and restart tests remain green.

## Verification commands

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## Required documentation updates

- Update Architecture, Code Map, CLI documentation, Roadmap, Status and this task.

## Definition of done

Focused and complete non-live verification pass, recovery is bounded and safe,
T074 is Completed and T034 is restored as the only Active task.

## Verification results

- Focused unit and MongoDB tests: `97 passed`.
- Complete non-live suite: `952 passed`, `0 skipped`.
- Branch coverage: `90.10%`.
- Ruff on project sources, formatting, mypy, lock check, scoped diff check,
  text-integrity check, CLI help and tracked-file secret scan passed.
- The root-wide Ruff invocation also inspected the unrelated
  `npvt-link-extractor/` tool directory and reported its lint findings;
  that directory was not modified because it is outside T074.
