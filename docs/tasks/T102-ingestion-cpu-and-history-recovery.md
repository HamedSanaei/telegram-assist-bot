# T102 — Ingestion CPU and history recovery

## Status
Completed; locally verified, not deployed.

## Goal
Eliminate whole-history retry loops and repeated completed work; isolate oversized
media durably without losing source metadata or blocking other messages/sources.

## Requirement references
REQUIREMENTS.md 5.2–5.7, 5.9, 13, 14, 16; reported production incident source
-1001648659094/message 68568, 100 MiB policy.

## Dependencies
T009–T019, T064, T077 and T101 are completed on main.

## Scope
Bounded classified operation retries/FloodWait, durable media rejection and explicit
authorized retry, crash-safe incomplete-stage recovery, incremental source history,
aggregate ingestion/resource metrics, regression fixture and measured synthetic
baseline/fixed workload. Preserve unique claims, source text/entities and albums.

## Out of scope
Production restart/deployment/recreation, production data or volume deletion,
media-limit increases, live Telegram calls, unrelated refactors and publication changes.

## Expected files or modules
Application ingestion/media/preparation/history use cases and ports; MongoDB,
local-storage and Telegram history adapters; ingestion composition; focused unit,
integration and regression tests; benchmark script and project-memory/operations docs.

## Implementation notes
Additive MongoDB state/indexes with bounded operations. Durable checkpoints must
never advance past unresolved work. No authorization caching. Keep cancellation
propagation and drain owned tasks. Retry known transient errors only, use bounded
exponential backoff/jitter and never shorten FloodWait to evade server limits.
Original metadata must survive rejection. Explicit retry must require existing
administrator permissions; no automatic retry merely because a policy changes.

## Objective acceptance criteria
1. No permanent error causes unbounded whole-source retries; other messages and sources continue.
2. Oversized streams stop and clean partial files; structured rejection survives restart.
3. Completed messages skip expensive stages; partial stages resume safely without duplicate claims.
4. Albums cannot publish incomplete media silently; rejected members remain auditable.
5. Ordered history stops at the local-day boundary and resumes safely after crashes.
6. Existing edit/deletion semantics remain intact and live/history overlap is safe.
7. Retries and delays are bounded, observable and FloodWait-correct; shutdown drains tasks.
8. Aggregates expose new/existing/skipped/media failures/oversize/retries/duration/DB operations/CPU/memory.
9. Measured baseline/fixed workload covers thousands of duplicates and the incident fixture.
10. All required checks pass; production remains stopped and supporting services untouched.

## Required unit tests
Permanent media/history failures; continuation; completed fast path; partial
recovery; bounded transient retries/FloodWait; albums; cancellation and shutdown.

## Required integration tests
MongoDB concurrent claims, rejection restart/authorized retry, checkpoint crash
recovery, live/history overlap. Synthetic production fixture without credentials.

## Verification commands
```text
uv run pytest -m "not live" --basetemp=.pytest-tmp-t102 --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
uv lock --check
uv pip check
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```
Run the added synthetic benchmark with an isolated loopback test MongoDB. Run
secret scanning on changed files. Report unavailable platform/container/live checks
explicitly; do not claim production CPU results from synthetic measurements.

## Required documentation updates
ROADMAP, STATUS, this task, CODE_MAP, ARCHITECTURE, significant DECISIONS and
OPERATIONS migration/deployment/rollback guidance. No release bump or commit requested.

## Definition of done
All acceptance criteria and locally executable checks pass; measured results and
remaining platform limitations documented; no production mutation; suggest commit.

## Root cause and implemented repair

Fetched `origin/main` remained `aca33c9`; initial worktree was clean. Its approval
and cleanup hardening did not fix ingestion. The reported production diagnosis is
consistent with the source: `run_history_crawl()` retried every exception forever
at a fixed delay; `MediaTooLargeError` escaped media ingestion; completed Posts
still entered media/preparation; history collected all pages before processing
and had no day cutoff or durable cursor. No production diagnostics were rerun.

The cutover streams acknowledged pages, isolates terminal items/sources, retries
only failed operations, and continues page-limited cursors fairly. Live ingestion
cannot advance the history watermark. Source payload conflicts keep immutable
originals and become durable dispositions rather than blocking older messages.
Media attempts are consumed durably before external work; oversized, permanent
and exhausted operations never reopen automatically. Exact source DTO snapshots
support crash and authorized older-day recovery, with original Post TTL. Album
holds cover startup-history members, not newer live albums. Member completion is
separate from anchor readiness so completed members cannot pin media forever.

The authorized `media-retry` command opens MongoDB only, checks active admin role,
toggle permission and source/destination access, preserves rejection audit, and
queues recovery without starting Telegram. An outstanding FloodWait remains a
lower bound even for an authorized reset. The 100 MiB policy is unchanged.

## Measured synthetic baseline and fixed workload

Windows, Python 3.13.14, real isolated loopback MongoDB 8.3.7; 2,000 previously
completed media messages plus synthetic source `-1001648659094` / message `68568`,
100 MiB limit, three explicit crawl invocations. Seed time and imports are
excluded. Provider data is synthetic; oversized failure needs no real media.
The same benchmark script imports either extracted `aca33c9` or the fixed source.
Baseline invocations are deliberately finite; the unsafe production outer loop
is established by source inspection and the supplied production evidence.

| Measurement | Baseline | Fixed |
|---|---:|---:|
| Elapsed seconds | 50.215636 | 3.767360 |
| Client CPU seconds | 35.515625 | 2.687500 |
| Active-work CPU percent of one core | 70.73 | 71.34 |
| MongoDB command attempts | 66,016 | 6,651 |
| Duplicate storage checks + preview calls | 18,000 | 0 |
| Provider media opens | 3 | 0 |
| Synthetic history pages fetched | 63 | 23 |
| Structured log events | 30,009 | 6 |
| Crawl failures | 3 | 0 |

Final fixed unchanged invocations each fetched one history page, used 305 Mongo
commands, and completed in 0.227432 / 0.210155 seconds. Short active-work CPU
percentages vary with scheduling; the measured gain is reduced total CPU work and
elapsed lifetime. These are not production-container or MongoDB-server CPU
measurements, and synthetic page batches are not measured live Telegram RPCs.

A separate actual `TextIngestionApplication` idle smoke used a blocking synthetic
live subscription, the real album/recovery background loop and real MongoDB after
the oversized fixture was durably rejected: 5.007852 seconds elapsed,
0.015625 CPU-seconds (0.312% of one core), 15 Mongo commands, zero media opens,
RSS/peak RSS 192,847,872 bytes in the instrumented smoke process. Shutdown left
zero named ingestion/publication tasks. This short sample is not a long-duration
production memory/CPU guarantee.

Reproduction (credential-free loopback test database only):

```text
uv run python scripts/benchmark_ingestion.py --source-root BASELINE_CHECKOUT --mongodb-uri mongodb://127.0.0.1:27029/?directConnection=true --messages 2000 --cycles 3
uv run python scripts/benchmark_ingestion.py --source-root . --mongodb-uri mongodb://127.0.0.1:27029/?directConnection=true --messages 2000 --cycles 3
```

## Regression acceptance map

| Required scenario | Deterministic evidence |
|---|---|
| 1. History oversized failure | Incident JSON fixture; runtime media incident tests; real-Mongo synthetic benchmark |
| 2. No infinite permanent retry | `test_permanent_source_failure_visits_next_source_without_whole_crawl_retry`; durable rejection replay tests |
| 3. Subsequent message ingestion | Streamed oversize continuation and immutable-conflict history tests |
| 4. Other sources progress | Permanent-source bootstrap regression |
| 5. Efficient completed skip | Runtime completion/outbox tests; zero duplicate media operations in benchmark |
| 6. Partial-stage resume | Stage completion races, outbox crash and registered-member recovery tests |
| 7. Rejection survives restart | Real Mongo incident restart and authorized audited reset |
| 8. Crash/checkpoint correctness | Page-limit, interrupted catchup, cursor/CAS boundaries and Mongo restart |
| 9. Live/history overlap | Existing concurrent ingestion and real runtime album overlap/restart suite |
| 10. Bounded transient retries | Durable crash-consumed attempt budget, exhaustion replay and jitter tests |
| 11. FloodWait | Actual lower-bound/over-limit history and media tests; authorized reset cannot shorten wait |
| 12. Mongo concurrency | Single-winner ingestion leases, source revision CAS and existing unique post claims |
| 13. Albums | Rejected member blocks incomplete publication; authorized recovery, snapshot holds and cleanup-safe non-anchor state |
| 14. Shutdown | Retry-sleep cancellation, actual runtime idle shutdown and existing critical-task lifecycle regressions |

## Verification progress

- Initial focused run found four integration/expectation failures; repaired BSON
  `Int64` snapshot identities, durable deferral/cancellation fixture isolation and
  obsolete whole-collection preparation counts/startup-failure assumptions.
- Integrated focused runs then passed 103 tests on MongoDB 8.3.7 and MongoDB
  7.0.32. Both are isolated local services, never the production database.
- Initial complete Python 3.13 run: 2,108 passed, 90.18% combined statement/branch coverage.
- Cross-version validation exposed the missing structured-log fields argument on
  immutable conflicts and additional untested recovery branches; the call was
  fixed and explicit conflict/provider/lease/cursor boundary regressions added.
- A concurrent full-suite run found that an active durable media attempt could
  appear as FloodWait to another caller of the same downloader. Weakly held
  per-item locks now coalesce those callers without caching completion or blocking
  unrelated identities. Deterministic waiter cancellation and the existing real
  Mongo album/pipeline concurrency contract passed together (17 tests).
- Stream cleanup failures now retain the original oversized/cancellation
  disposition and expose the secondary exception as its cause rather than
  suppressing it or replaying the unsupported operation.
- Actual `python -m telegram_assist_bot media-retry` smoke returned exit 0,
  `media_retry_queued=true`, and preserved durable actor/rejection audit across
  a fresh process. No Telegram session was opened.
- No release tag, commit, push or deployment was requested or performed.

## Final verification

Full non-live suites used `--cov=telegram_assist_bot --cov-branch
--cov-fail-under=90`, separate basetemps/coverage files, the locked environment
and isolated loopback databases:

| Python | MongoDB | Passed | Coverage (`--cov-branch`) |
|---|---|---:|---:|
| 3.12 | 7.0.32 | 2,141 | 90.27% |
| 3.13 | 8.3.7 | 2,141 | 90.30% |
| 3.14 | 7.0.32 | 2,141 | 90.32% |

- Ruff check and formatting: passed, 429 files formatted.
- Mypy `src tests scripts`: passed, 428 source files.
- `uv lock --check` and `uv pip check`: passed, 47 compatible packages.
- `uv build --no-build-isolation` and distribution validation: passed.
- Text integrity: 592 files passed; secret scan: all 50 changed files passed;
  diff whitespace passed. Representative Persian fixture content remains intact.
- Final local Docker image built successfully after one transient BuildKit EOF.
  Network-disabled, read-only, capability-dropped CLI startup and resource sampling
  passed as UID 10001. No production image was pushed.
- Fresh wheel installation with its declared dependencies passed CLI startup.
  The intentionally dependency-free import probe first lacked Pydantic; installing
  declared dependencies resolved that environment-only failure.
- Git Bash syntax checks passed for install/manage/menu/tabctl/permissions and
  Linux acceptance scripts. Windows PowerShell 5.1 parsed all four PowerShell
  entry points; PowerShell 7 was unavailable.
- Earlier concurrent matrix execution exposed intermittent failures in unchanged
  advertisement collision and Windows operator-lock tests. Final sequential
  suites passed without modifying those tests or subsystems.
- Python 3.14 emitted an interpreter-exit unclosed SQLite connection
  `ResourceWarning`; its origin was not established. It did not fail the suite.
- Owned local MongoDB validation services were stopped gracefully after the final
  benchmark. No Docker volume was removed.

## Migration, operational safety and limitations

See OPERATIONS.md, “History CPU incident recovery (T102),” for exact additive
indexes, authorized retry, deployment and stopped rollback instructions.

No configuration keys, dependency versions, media limit or public callback
formats changed. New checkpoint/failure collections and recovery fields are
additive. Only dedicated new source-snapshot documents have the TTL field;
existing preparation/publication/audit history is never expired by that index.

An abrupt death can delay incomplete-stage recovery until the one-hour lease
expires; graceful cancellation releases immediately. Expired originals cannot be
retried. Legacy partial Posts without source/media snapshots require eligible
history/live redelivery; absent legacy media descriptors cannot be invented.
NewMessage-only source listening and immutable original semantics remain intact;
this repair does not add source-edit/delete event propagation or tombstones.

Production runtime is intentionally stopped with restart policy `no`; no SSH,
production Docker operation, deployment, restart or deletion was performed.
MongoDB, approval bot and unrelated production containers remain untouched.
Linux-host two-instance installer acceptance needs Linux `sudo`, `getent` and
ownership tools unavailable on this Windows host; local Linux Docker image smoke
and the existing installer/Compose contract tests cover reachable boundaries.
