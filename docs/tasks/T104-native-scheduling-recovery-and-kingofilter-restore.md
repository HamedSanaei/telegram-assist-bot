# T104 — Native scheduling recovery and Kingofilter restoration

## Status
In progress. Owner authorized investigation, code changes, verification, commit,
push, publication, scoped production deployment and runtime restart.

## Goal
Recover the stopped Kingofilter runtime and prevent transient transport failures
from permanently stopping native scheduling or bypassing safe publication claims.

## Requirement references
REQUIREMENTS.md sections 5.17–5.19, 13–16 and 18.7–18.8; incident acceptance
criteria supplied by the owner on 2026-10-26.

## Dependencies
T067, T070, T071 and T100–T103 implemented. Release 1.1.5 at c9e7a22 is
published and deployed; T103 documentation remained at an earlier checkpoint.

## Scope
Trace scheduler and database/Telegram transport errors; classified recovery,
bounded jittered backoff, safe cancellation, durable scheduling certainty,
regressions, safe instance-specific automatic recovery, 1.1.6 release and
runtime-only Netherlands production deployment with ten-minute observation.

## Out of scope
Other instances; approval-bot/cleanup changes; MongoDB recreation; deleting
volumes, pending jobs or session data; replaying uncertain publication requests.

## Expected files or modules
Native scheduling application/adapter/bootstrap/worker and associated tests;
scoped deploy supervision, release version entry points and lockfile; project
status, roadmap, code map, architecture, decisions and operations documentation.

## Implementation notes
Production recorded runtime-native-scheduling NetworkTimeout at
2026-10-25 04:26:42 UTC, shutdown critical_task_failed and exit 3 at 04:26:46.
PyMongo defines NetworkTimeout; Telethon 1.44 does not. Current Mongo claims are
outside native use-case catch blocks. Exact historical command is not in the
application event; distinguish evidence from inference. Preserve shared client,
OS session lock, Mongo atomic claims and request_started/outcome_unknown fence.
Unless-stopped conflicts with the permanent-startup exit-zero contract; use
compatible scoped supervision rather than restoring a known restart-storm risk.

## Objective acceptance criteria
1. Failure lifecycle and restart=no rationale documented from code/log evidence.
2. Native scheduling survives transient transport failures with bounded delays,
   safe category/type/attempt/timing logs and no duplicate client or task.
3. Authentication/configuration/permanent database/programming errors propagate;
   cancellation remains prompt and shutdown closes resources once.
4. Ambiguous sends are never automatically replayed; claims and locks prevent
   concurrent duplicate execution.
5. Scoped runtime recovery handles unexpected exits and host/Docker restart,
   respects deliberate administrative stops and limits restart pressure.
6. Verified 1.1.6 source is committed/pushed and established CI/release succeeds.
7. Backed-up deployment replaces only Kingofilter runtime, preserving sessions,
   config, secrets, volumes, jobs and other container identities.
8. Runtime readiness, Telegram auth, scheduling, MongoDB and dependent-service
   health confirmed; resource/restart/error samples observed for at least 600 s.

## Required unit tests
Observed PyMongo NetworkTimeout, Telegram connection recovery, repeated temporary
errors, permanent failure propagation, FloodWait, cancellation during retry,
graceful shutdown, publication certainty, singleton and supervisor stop behavior.

## Required integration tests
Mongo native command atomicity/leases/restart certainty; Telegram scheduling
adapter mappings; actual isolated Docker unexpected-exit recovery and runtime
singleton contention; scoped production smoke and resource monitoring.

## Verification commands
```text
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv lock --check
uv pip check
uv run python scripts/check_text_integrity.py --all
uv build --no-build-isolation
uv run python scripts/check_distribution.py <fresh-build-directory>
```
Use isolated MongoDB through TEST_MONGODB_URI. CI additionally verifies Python
3.12/3.13/3.14 and Docker/installer acceptance. Record actual smoke commands,
release commit/image, backup and production observation below.

## Required documentation updates
This task, STATUS, ROADMAP, CODE_MAP, ARCHITECTURE, OPERATIONS, release notes and
checklist. DECISIONS records the scoped recovery safety decision.

## Definition of done
Every acceptance criterion and required gate passes; production is running and
stable for ten minutes; safe rollback retained; evidence and limitations recorded.

## Verification record
- Before fix: injected PyMongo NetworkTimeout at claim_next escaped at native
  scheduling line 64, reproducing the failed critical task without Telegram I/O.
- Confirmed SDK identity: NetworkTimeout is PyMongo, not Telethon. MongoDB logged
  client disconnect/reset at 04:26:42.065 UTC before scheduler failure at
  04:26:42.151 UTC. Retained logs do not identify the exact MongoDB command;
  claim_next/acquire_destination/claim_reconciliation are the unguarded candidates.
- Focused recovery suite: 147 passed, two Linux-only boot cases skipped locally.
- Final full non-live suite on isolated MongoDB 7.0.32 / Python 3.13:
  2225 passed, two Linux-only skips, three opt-in Docker tests deselected;
  branch coverage 90.36%. Docker tests separately executed: three passed.
- Real isolated MongoDB pause induced an actual socket NetworkTimeout; native
  cycle logged retry then recovery using the same client. No production fault
  injection was performed.
- Ruff, formatting, strict mypy (432 files), lock and dependency checks pass.
  UTF-8 integrity passes for 601 files; wheel/sdist and distribution checks pass.
  Linux bash syntax smoke for the boot script passes in the built image.
- Root-only production backup
  `/opt/telegram-assist-bot/instances/admin1/backups/t104-20261025T221106Z`
  contains configuration, sessions, MongoDB archive and container manifest.
  All four SHA256 checks and gzip integrity passed.
- Baseline workload: native pending 1 / outcome_unknown 1; scheduled Pending 1 /
  OutcomeUnknown 42; publications 956, duplicate post/destination groups zero.
- First CI run `40241111589`: Python 3.12/3.13 quality and Docker/installer
  acceptance passed; Python 3.14 exposed a pre-existing album fixture race:
  membership metadata was used to advance a frozen clock before history released
  its hold. Necessary verification prerequisite: wait for durable hold release
  before advancing time, retaining every publication/media/shutdown assertion.
  Full local Python 3.14 suite then passed 2225 tests (90.34% branch coverage);
  Python 3.13 lifecycle scenario passed both tests. No production behavior changed
  for this fixture correction. Python 3.14 emits a non-fatal SQLite ResourceWarning.
- Linux boot ownership smoke verifies marked runtime exclusion with dependent
  services retained, both with and without .env; other instance boot is unchanged.
- Publication, CI, scoped deployment and ten-minute observation remain pending.
