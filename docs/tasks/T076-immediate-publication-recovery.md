# T076 — Immediate publication and approval-toggle recovery

## Status

Completed

## Goal

Remove the proven MongoDB integer-subtype pre-send publication failure and keep
approval selections correct and recoverable after terminal publication failure.

## Requirement references

`docs/REQUIREMENTS.md` sections 5.17–5.19, 13, 14, and 16.

## Dependencies

T027–T033, T060–T075 (Completed).

## Scope

- Normalize MongoDB destination identifiers at the persistence/Telegram boundary.
- Persist and safely log publisher reason codes.
- Make terminal failed immediate cancellation idempotent.
- Compensate a selection CAS when downstream orchestration fails.
- Provide exact-ID, dry-run recovery for proven pre-send failures.

## Out of scope

AI work, ingestion, approval preview delivery, scheduling algorithms, live
Telegram execution, migration of successful or outcome-unknown publications.

## Expected files or modules

Publication domain/ports/use cases, MongoDB publication repositories, Telethon
publisher, approval callback orchestration, publication recovery CLI, tests, and
project-memory documentation.

## Implementation notes

`bson.int64.Int64` is a valid integer but must be converted to the application
owned `int` before transport validation. Recovery requires terminal permanent
failure, no Telegram receipt, and either the legacy BSON subtype proof or the
persisted pre-send reason code.

## Acceptance criteria

- Valid MongoDB destination IDs reach Telegram exactly once.
- A pre-send failure stores and logs its safe `reason_code`.
- Permanent failed immediate selections can be cleared or changed to scheduled.
- Callback replays remain rejected and post-CAS failures restore canonical state.
- Recovery cannot reset successful or outcome-unknown work.

## Unit tests

Publisher reason propagation, callback replay, terminal cancellation,
deselection/rescheduling, and post-CAS compensation.

## Integration tests

Telethon mapping accepts `Int64`; isolated MongoDB dry-run, clear, requeue, and
keyboard-sync request behavior.

## Verification commands

Ruff check/format, mypy, full non-live pytest with branch coverage, and
`git diff --check`, all with Python 3.12.

## Documentation updates

Update Roadmap, Status, Code Map, and this task file.

## Definition of done

All acceptance criteria and mandatory non-live verification pass without live
Telegram calls or changes to local configuration/production data.
