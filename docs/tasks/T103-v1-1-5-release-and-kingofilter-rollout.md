# T103 — v1.1.5 release and scoped kingofilter rollout

## Status
Released/deployed; historical observation is superseded by the T104 incident and
restoration task. This is not an active implementation task.

## Goal
Publish the verified T100–T102 fixes as 1.1.5 and update only kingofilter runtime
and its compatible media cleanup worker, preserving all existing data/sessions.

## Requirement references
REQUIREMENTS.md sections 5.2–5.7, 13–16, 18.7–18.8 and 19.

## Dependencies
T100, T101 and T102 completed. T102 committed as 6d72890 after fresh validation.

## Scope
Version/lock/image/installer/manager synchronization, release notes/checklist,
release contract tests, immutable tag and CI build, backup, digest-pinned scoped
runtime/cleanup rollout and production observation.

## Out of scope
New features, media-policy changes, manual rejected-media retry, MongoDB or
approval-bot recreation, other instances, volume deletion and restoring runtime's
restart policy during initial observation.

## Expected files
Package/pyproject/lock, Dockerfile/Compose, installers/manager, distribution and
acceptance scripts, release contracts, README and release/task/state docs.

## Implementation notes and risks
Use a new version/tag; never overwrite 1.1.4. Back up database/config/session/media
before rollout. Preserve existing mounts/commands and runtime restart=no. Retain
old image and an explicit stopped rollback. Additive indexes do not require data
rewrites. Never print secrets or send private configuration to external agents.

## Objective acceptance criteria
1. Existing changes committed with configured human identity.
2. All current release entry points agree on 1.1.5 and required checks pass.
3. Tag/image/assets refer to the verified release commit and CI succeeds.
4. Verified server-side backup exists before production mutation.
5. Only kingofilter runtime and media-cleanup-worker are recreated with the
   release digest; existing database, session, media and other services survive.
6. Production readiness and bounded ingestion progress are observed without a
   recurring whole-source failure; rollback remains available.

## Required unit tests
Release version, distribution, package import, installer/manager/Compose contracts.

## Required integration tests
Non-live suite on isolated MongoDB, CI Docker acceptance, image and deployed
runtime/cleanup health/identity observation.

## Verification commands
```text
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 -q
uv run pytest tests/unit/deployment tests/unit/scripts/test_check_distribution.py tests/unit/test_package_import.py -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv lock --check
uv pip check
uv run python scripts/check_text_integrity.py --all
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```
Secret scan, GitHub Quality/release jobs, release assets/digest verification and
server-side backup/deployment commands are recorded below as executed.

## Required documentation updates
Task, STATUS, ROADMAP, RELEASE_NOTES, RELEASE_CHECKLIST, README and relevant
OPERATIONS/CODE_MAP entries; no architecture change.

## Definition of done
Local and CI checks pass; publication and scoped rollout are verified, data and
sessions preserved, actual results/limitations recorded and documentation committed.

## Verification and execution record

- Existing T102 source: fresh full non-live suite on isolated MongoDB 7.0.32:
  2141 passed, branch coverage 90.29%; committed as `6d72890`.
- 1.1.5 release/deployment/package/distribution contracts: 93 passed. Initial
  Windows shell selection failures were resolved by using Git Bash in PATH;
  no assertions were weakened. Changed Python files were formatted to LF.
- Ruff, formatting, strict mypy, text integrity, lock and dependency checks pass.
- Wheel and sdist built in `artifacts/t103-dist`; distribution validation passed.
- Owner selected the existing Netherlands SSH connection. Verified kingofilter
  runtime and cleanup stopped, approval-bot and MongoDB running. Runtime restart
  policy is `no`; existing mounts and other container identities recorded.
- Full server-side backup started before rollout: database dump, instance files,
  Telegram session and media archives under a root-only timestamped directory.
- CI publication and scoped deployment are pending; no production service has
  been restarted at this checkpoint.

- First CI run `39660172668`: all application tests and Docker/installer
  acceptance passed, but Linux mypy rejected Windows-only `ctypes.WinDLL` use
  inside the resource sampler. Necessary release prerequisite: guard the entire
  Windows counter block by platform and retain an empty sample elsewhere. Two
  host-resource regressions passed; strict mypy with both `--platform linux` and
  `--platform win32` now passes. No ingestion/publication behavior changed.
- Server backup completed with four hashed/readable archives; MongoDB dump
  compression integrity passed. Existing publication count 931 and approval
  reference count 127558 were recorded before rollout (live counts may grow).

- Second CI run `39660863014`: tests, Linux typing and Docker acceptance passed.
  The secret hook required two synthetic-fixture baseline line numbers to move
  from 307/308 to 313/314. Reproduced scanning in a read-only Linux container;
  no new finding fingerprints, detector changes, filters or suppressions were
  added. Updated only those line numbers and reran the exact CI gate.
- Re-read and verified SHA256SUMS for all four server-side backup archives.

- Final publication evidence recovered during T104: Quality `39661849137` and
  Release `39662376200` succeeded for `c9e7a22`; v1.1.5 was published and deployed.
  Production runtime used digest
  `sha256:03653f1aab8bdb519a4c4724bfaec966b2ef3800bebce3d3548fe3922ef8558b`,
  starting 2026-10-21 20:05:42 UTC and stopping 2026-10-25 04:26:46 UTC after a
  native scheduling NetworkTimeout. The observation-only restart=no remained
  pinned. T104 owns the subsequent repair/recovery policy and final observation.
