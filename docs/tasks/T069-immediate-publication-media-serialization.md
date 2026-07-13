# T069 — Immediate publication claiming and media serialization

## Status

Completed

## Goal

Keep immediate destination publication independent from native scheduling waits,
and serialize every Telethon media upload through one type-preserving component.

## Requirement references

- `docs/REQUIREMENTS.md`: `5.16`–`5.19`, `13`, `14`, `16`.
- `docs/ARCHITECTURE.md`: operational runtime ownership and publication flow.

## Dependencies

- T027–T033 and T060–T068 are Completed.

## Scope

- Run immediate and native scheduling polling as independently supervised loops.
- Claim newly due immediate commands within the bounded one-second poll interval.
- Share one confined Telethon media serializer between immediate and native paths.
- Preserve original filenames, media kinds, album order, captions and entities.
- Persist safe failure category/type without retrying ambiguous outcomes.
- Recover a video proposal control card when only Bot API reply association is
  rejected, without duplicating its already-persisted content.

## Out of scope

- Migrating or executing legacy scheduled jobs.
- Changing approval selection semantics.
- Reading local configuration, live Telegram calls or live MongoDB mutation.
- AI implementation tasks.

## Expected files or modules

- Runtime publication composition and due worker.
- Publication/scheduling ports and MongoDB repositories.
- Telethon publisher, native scheduler and shared media serializer.
- Focused unit, fake-Telethon and MongoDB integration tests.
- Roadmap, Status, Architecture and Code Map.

## Implementation notes

- Immediate polling must not await native schedule execution or reconciliation.
- Uploaded media use application-owned `MediaType` and safe original filenames.
- An exception after a possible Telegram request becomes outcome-unknown.

## Acceptance criteria

- A new immediate command is claimed on the next poll while native reconciliation
  is blocked.
- Immediate and native publication use the same serializer implementation.
- Photo, video, animation, document and ordered mixed albums retain their type.
- Hash storage names never reach destination-visible upload filenames.
- Safe category/type persist and appear in structured failure events.
- Ambiguous jobs remain terminal and are not duplicated.
- Video proposals retain a usable destination keyboard even when Telegram
  rejects replying the control card to the media message.

## Required unit tests

- Independent runtime loops and critical supervision.
- Shared serializer media mapping and safe filename fallback.
- Due-worker safe exception persistence and event fields.

## Required integration tests

- MongoDB immediate claim latency, lease/restart and failure detail persistence.
- Fake Telethon immediate/native parity for text, media and mixed albums.

## Verification commands

```text
uv run --python 3.12 pytest <focused publication/runtime tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
TEST_MONGODB_URI=mongodb://127.0.0.1:27017/?directConnection=true uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
```

## Required documentation updates

- Update Roadmap, Status, Architecture and Code Map.

## Definition of done

All acceptance criteria and verification pass, T069 is Completed and T034 is
restored as the only Active task.

## Verification results

- Focused publication/runtime/Bot API/MongoDB suite: `60 passed`.
- Complete unit suite: `852 passed`.
- Complete non-live suite with local MongoDB: `928 passed`, `0 skipped`, branch
  coverage `90.08%`.
- Ruff, format, mypy, lock, diff, UTF-8/mojibake and secret scan: passed.
- Wheel/sdist build and distribution validation: passed.
- No local configuration was read or changed and no live Telegram request was
  made.
