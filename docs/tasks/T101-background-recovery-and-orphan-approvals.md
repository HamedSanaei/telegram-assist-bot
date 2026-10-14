# T101 — Background recovery and orphan approvals

## Status
Completed; verified locally, commit/push requested for this task.

## Goal
Keep approval polling available when legacy content expires or a background
operation fails, and recover bounded media-cleanup cycles without losing history.

## Requirement references
REQUIREMENTS.md sections 5.4, 5.5, 5.12–5.19, 13, 14, 16.

## Dependencies
T064, T072, T079, T099 and T100 are completed.

## Scope
Typed unavailable-content outcome, atomic terminal sync disposition, isolated
background supervision, bounded cleanup recovery and regression tests.

## Out of scope
Production data deletion, session changes, live Telegram calls, new features,
release tagging and changing the bounded Docker restart policy.

## Expected files
Operational approval application/ports/MongoDB adapter, approval bot bootstrap,
media-cleanup worker/bootstrap, focused tests, distribution allowlist and project-memory documentation.

## Implementation notes
Preserve publication receipts, destination states and administrator history.
Use additive fields and owner/version guards. Never terminalize arbitrary
ValueError or network failures as missing content. Log safe types/categories,
never exception messages. Retain cancellation and bounded nonzero worker exits.

## Objective acceptance criteria
- Missing ready content cannot crash approval polling or recur indefinitely.
- Terminal sync disposition survives restart and cannot overwrite newer work.
- Publication and administrator history remain intact; callbacks fail safely.
- Background recovery is bounded, delayed and observable, with CRITICAL
  notification on suspension while polling and sibling tasks remain available.
- Cleanup respects configured cycles, supports shutdown during backoff, and
  exits nonzero after exhausted recovery for on-failure:20.

## Required unit tests
Missing-content handling versus unrelated errors; supervised recovery,
exhaustion, permanent failure, cancellation; cleanup retries and cycle timing.

## Required integration tests
Real isolated loopback MongoDB: unavailable loader outcomes, atomic orphan
disposition, concurrent versions/owners, restart and preserved destination history.

## Verification commands
```text
uv run pytest tests/unit/application/test_operational_approval.py tests/unit/test_approval_bot_runtime_bootstrap.py tests/unit/workers tests/unit/test_media_cleanup_bootstrap.py -q
uv run pytest tests/integration/mongodb/test_orphan_approval_sync.py tests/integration/mongodb/test_operational_approval_runtime.py -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
uv lock --check
git diff --check
```

## Required documentation updates
Task, ROADMAP, concise STATUS, CODE_MAP, ARCHITECTURE and OPERATIONS; DECISIONS
for the terminal sync and supervision contract.

## Definition of done
All acceptance criteria and required checks pass, UTF-8 and secret review are
complete, then commit and push only task changes using the configured identity.

## Root causes and final behavior

1. MongoDB TTL removes original `posts` while durable approval and publication
   history survives. The loader previously raised generic ValueError and the
   `FIRST_COMPLETED` lifetime monitor propagated sync failure to polling. Only
   an explicit missing-original outcome now terminalizes sync, with owner,
   version and live-lease guards. Missing preparation remains recoverable.
2. Four approval background lifetimes now retry independently up to three
   starts, with exponential waits from configured poll interval capped at 60
   seconds. Permanent failures/exhaustion emit CRITICAL and suspend only that
   task until service restart. Cancellation still propagates.
3. Supplied incident evidence was `media_cleanup_worker_stopped`, four cycles,
   exit 0, no OOM and zero restarts under `on-failure:20`. This proves graceful
   completion; production code reaches it on SIGINT/SIGTERM, but the old log
   cannot establish who sent the signal. Signal completion now drains and exits
   3 with explicit reason/number. Docker manual stop still suppresses restart.
   Cleanup cycles also retry transient failures with 5/10-second backoff, then
   exit 3 on exhaustion, preserving configured interval and batch limits.
4. Necessary packaging prerequisite: the strict wheel allowlist now includes
   `workers/background.py`. No dependencies, configuration keys or restart
   policies changed; only additive terminal sync fields are persisted.

## Verification results

Environment: Windows, Python 3.13.14; existing MongoDB 8.3 executable started
against a task-owned temporary data directory on loopback port 27028. Fixtures
use credential-free TEST_MONGODB_URI and unique guarded test database names.
Git Bash was prepended to PATH for deployment shell tests.

- Focused application/bootstrap/workers/MongoDB runs: 95 passed, then 106 passed;
  final focused run including Compose/entrypoint regressions: 124 passed. All
  additional final cases were included in the last complete suite.
- Final command (no skipped tests):
  `uv run pytest -m "not live" --basetemp=.pytest-tmp-t101-verified-20261014 --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 --tb=short -q`:
  **2063 passed**, branch coverage **90.34%**. Prior successful full run:
  2062 passed, 90.36%, before the final permanent-cleanup regression case.
- `uv run pytest tests/unit/scripts/test_check_distribution.py --basetemp=.pytest-tmp-t101-distribution -q`: **2 passed** after the necessary allowlist update.
- `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy src tests scripts`: passed (409 formatted files; 408 typed sources).
  Initial lint/typing findings in new code/tests were repaired without weakening
  existing assertions.
- `uv run python scripts/check_text_integrity.py --all`, `uv lock --check`,
  `git diff --check`: passed. Strict UTF-8 and mojibake scan of every changed
  text file passed; existing Persian fixtures/source text were reviewed intact.
- `uv run detect-secrets-hook --no-verify --baseline .secrets.baseline` with
  explicit changed-file arguments: passed; no real secrets, sessions, local
  configuration or private media were added.
- `uv build --no-build-isolation`: wheel/sdist built successfully.
  `uv run python scripts/check_distribution.py dist`: passed after the initial
  expected rejection of the newly added supervisor module was corrected in
  the strict allowlist.
- Initial default full-suite run: 1578 passed, 483 setup errors and one failed
  shell test. Setup errors were pre-existing Windows permission denial on
  `.pytest-tmp-current`; a fresh basetemp preserved that directory. The shell
  failure selected WSL Bash, which misinterpreted Windows paths. Both isolated
  backtests passed after choosing a fresh root and Git Bash. An intermediate
  full run was interrupted to apply the Bash environment correction; it has
  no completion result. Final full-suite results above supersede these attempts.

## Acceptance review and limitations

All objective criteria passed: orphan progress is durable and concurrent-safe;
publication receipts/statuses, preparations and administrator references are
preserved; callbacks see `approval_expired`; no arbitrary loader failure is
retired; sibling tasks/polling survive supervised failures; critical suspension,
cleanup cycle budgets, configured pacing and signal restart mapping are tested.

No production deployment or live Telegram calls were performed. The local
Docker daemon was unavailable, so actual container restart execution was not
run; the existing Compose restart/entrypoint contracts and signal mapping were
verified through tests. The incident signal sender remains unknown. Restored
content requires explicit operational review before reactivating a terminal
approval; suspended background tasks need a service restart after repair.

Suggested commit: `fix(runtime): isolate approval failures and recover media cleanup`
