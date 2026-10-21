# T103 — v1.1.5 release and scoped kingofilter rollout

## Status
In progress; owner authorized commit, next release, server connection and update.

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
