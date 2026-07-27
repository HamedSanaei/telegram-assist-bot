# T077 — Preserve Telegram proxy links and source URL buttons

## Status

Completed

## Goal

Keep functional Telegram proxy links during destination text cleanup and preserve
portable source URL-button rows through ingestion, MongoDB persistence, immediate
publication, and native scheduled publication without changing administrator
keyboards or Milestone 5/6 runtime behavior.

## Requirement references

- `docs/REQUIREMENTS.md`: 5.2، 5.4، 5.10، 5.17–5.19، 13، 14، 16.

## Dependencies

- T008–T012، T017، T027–T033، T060–T076 are complete.

## Scope

- Exempt valid `t.me/proxy` and `t.me/socks` configuration links from Telegram
  reference pruning while keeping ordinary unrelated channel/post links removable.
- Map portable `KeyboardButtonUrl` rows to application-owned immutable values.
- Persist URL buttons additively with legacy Post compatibility.
- Add buttons to destination publication payloads and map them to Telethon for
  immediate text/media publication and native scheduled publication.
- Preserve row order, button label, URL, Persian, ZWNJ, and Emoji exactly.

## Out of scope

- Source callback, request-phone, request-location, login, switch-inline, game,
  buy, or WebApp button behavior that cannot be portably copied to another channel.
- Bot approval keyboards and callback tokens.
- Milestone 5/6 operational activation or live Telegram calls.
- Link preview, proxy reachability, or proxy credential validation.

## Expected files

- `application/content/telegram_links.py`
- `domain/posts/models.py`
- `application/ports/telegram_source_gateway.py`
- `application/ports/publication.py`
- `infrastructure/telegram/user/message_mapper.py`
- `infrastructure/persistence/mongodb/post_mapper.py`
- `infrastructure/persistence/mongodb/publication_payload_loader.py`
- `infrastructure/telegram/user_publisher.py`
- `infrastructure/telegram/native_scheduler.py`
- Focused unit and MongoDB/adapter integration tests.

## Implementation notes

- Proxy preservation is route-aware: a channel merely named `proxy` without the
  functional proxy query contract is still treated as an ordinary Telegram reference.
- URL buttons are data, not executable source callbacks. Only explicit URL buttons
  with `http`, `https`, or `tg` schemes cross the boundary.
- Existing Post documents missing the additive keyboard field load as an empty keyboard.
- Publication remains idempotent; this task only enriches the immutable payload.

## Acceptance criteria

1. The reported `https://t.me/proxy?...` case remains byte-for-byte intact.
2. Ordinary unrelated `t.me/channel`, post, and query links are still removed.
3. Source URL-button rows survive mapper, ingestion, MongoDB round trip, payload load,
   immediate publication, and native schedule mapping.
4. Button ordering, labels, and URLs are unchanged.
5. Unsupported source button types are not reinterpreted as portable URL buttons.
6. Legacy documents and payload constructors remain compatible.
7. No administrator callback keyboard or Milestone 5/6 runtime is modified.

## Required tests

- Cleanup tests for proxy/socks boundaries, ordinary channel named `proxy`, and Persian.
- Message-mapper tests for URL rows, unsupported buttons, and malformed URL data.
- Domain and Post mapper legacy/round-trip tests.
- Publication payload loader and immediate/native Telethon adapter tests.
- Focused end-to-end persistence-to-fake-Telegram test when practical.

## Verification commands

```powershell
uv lock --check
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv run --python 3.12 pytest -m "not live"
uv run --python 3.12 python scripts/check_text_integrity.py --changed
uv run --python 3.12 python scripts/check_text_integrity.py --all
git diff --check
```

## Documentation updates

- Update ROADMAP, STATUS, CODE_MAP, ARCHITECTURE, and the intentional requirement
  clarification in section 5.10.
- Record a lasting decision only if needed to prevent URL/source-callback confusion.

## Verification results

- Focused mapper/domain/MongoDB/publication tests: `134 passed`.
- Phase 1–4 ingestion, persistence, publication and scheduling regressions:
  `192 passed`.
- Full non-live suite: `1368 passed`.
- `uv lock --check`, Ruff lint/format, MyPy, both text-integrity scans and
  `git diff --check`: passed.
- The local pytest cache emitted the existing non-functional `WinError 5` warning;
  no test failed and no production path uses that cache.

## Definition of done

All acceptance criteria and quality gates pass, Persian/UTF-8 is reviewed, legacy
documents load, and publication fakes prove no raw SDK object or unsupported callback
crosses application boundaries.
