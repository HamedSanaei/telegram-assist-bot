# AGENTS.md

## 1. Purpose

This file defines the mandatory working rules for developers and AI coding
agents, including Codex, working on this repository.

This is a long-running, multi-session project. Repository files are the source
of truth. Do not rely on chat history as persistent project memory.

The complete product requirements belong in:

```text
docs/REQUIREMENTS.md
```

Do not duplicate the full requirements in this file. This file defines how the
project must be implemented and how work must continue safely across multiple
sessions.

All developers and AI agents working on this repository must follow these
instructions.

---

## 2. Project Overview

This project is a Python-based Telegram channel administration assistant.

Its responsibilities include:

- Collecting posts from configured public Telegram source channels.
- Persisting posts and media metadata in MongoDB.
- Preventing duplicate collection and duplicate publication.
- Processing posts through configurable content pipelines.
- Preserving Telegram entities, Premium Emoji, Custom Emoji, Persian text, and
  media groups.
- Sending candidate posts to authorized administrators for approval.
- Publishing approved posts immediately or through a persistent schedule.
- Using multiple configurable AI providers through retry and fallback pipelines.
- Supporting future growth without rewriting the domain and application core.

The project must be implemented entirely in Python.

MongoDB is the primary database unless an approved architectural decision says
otherwise.

---

## 3. Repository Documentation as Project Memory

The repository must contain the following project-memory files:

```text
AGENTS.md
docs/REQUIREMENTS.md
docs/ARCHITECTURE.md
docs/ROADMAP.md
docs/STATUS.md
docs/CODE_MAP.md
docs/DECISIONS.md
docs/tasks/
```

Their responsibilities are:

### `docs/REQUIREMENTS.md`

Contains product behavior, business requirements, acceptance criteria, and
phase definitions.

It describes what the application must do.

### `docs/ARCHITECTURE.md`

Describes the architecture that is currently implemented or explicitly planned
for the active milestone.

It must include:

- Layer boundaries.
- Dependency direction.
- Main domain models.
- Application use cases.
- Ports and interfaces.
- Infrastructure adapters.
- MongoDB persistence.
- Telegram User API responsibilities.
- Telegram Bot API responsibilities.
- Scheduling and worker design.
- AI provider pipeline design.
- Testing strategy.

Do not let this file describe an imaginary architecture that no longer matches
the code.

### `docs/ROADMAP.md`

Contains the ordered milestones and implementation tasks.

Every task must have a unique task ID, for example:

```text
T001
T002
T101
T102
```

Completed tasks must be marked clearly.

### `docs/STATUS.md`

Contains only the current project state.

It must remain concise and include:

- Current milestone.
- Active task.
- Last completed task.
- Known blockers.
- Known failing tests.
- Last verified commit when available.
- Next recommended action.

Do not turn `STATUS.md` into a historical changelog.

### `docs/CODE_MAP.md`

Provides a concise map of the actual repository.

It must help a new developer or AI agent locate:

- Main entry points.
- Domain models.
- Application use cases.
- Repository interfaces and implementations.
- Telegram clients and handlers.
- AI providers and orchestration.
- Background workers.
- Configuration models.
- Tests.
- Deployment files.

Update it whenever important files, modules, responsibilities, or data flows
change.

### `docs/DECISIONS.md`

Records significant architectural decisions and their consequences.

Do not add routine implementation details. Add an entry only when a decision is
important enough that a future developer may otherwise reverse or misunderstand
it.

### `docs/tasks/`

Contains one Markdown file for each implementation task.

Each task file must define:

1. Task ID and title.
2. Status.
3. Goal.
4. Requirement references.
5. Dependencies.
6. Scope.
7. Explicit out-of-scope items.
8. Expected files or modules.
9. Implementation notes.
10. Objective acceptance criteria.
11. Required unit tests.
12. Required integration tests.
13. Verification commands.
14. Required documentation updates.
15. Definition of done.

---

## 4. Required Reading Order

Before starting any implementation task:

1. Read this `AGENTS.md`.
2. Read `docs/STATUS.md`.
3. Read the active task file referenced by `docs/STATUS.md`.
4. Read only the relevant sections of `docs/REQUIREMENTS.md`.
5. Read the relevant sections of `docs/ARCHITECTURE.md`.
6. Inspect the existing code and tests related to the active task.
7. Check `docs/DECISIONS.md` for decisions that affect the task.
8. Check the actual project commands in `pyproject.toml`, scripts, CI files, or
   existing documentation before inventing commands.

Do not read, redesign, or refactor the entire project unless the active task
explicitly requires it.

---

## 5. Incremental Development Workflow

Work on exactly one active task from `docs/tasks/` at a time.

### Task boundaries

- Respect the task's scope, out-of-scope items, dependencies, and acceptance
  criteria.
- Do not implement later roadmap tasks opportunistically.
- Do not add speculative abstractions for features that are not required by the
  active task.
- Do not perform unrelated cleanup or broad refactoring.
- A small prerequisite fix is allowed only when it is necessary to complete the
  active task and is documented in the final response.
- If the active task is too large to complete and verify in one session, split
  it into smaller task files before implementing it.
- Prefer vertical slices that produce working, testable behavior.
- Keep the project runnable at the end of every completed task.

### Missing dependencies

Do not begin implementation when a required task dependency is incomplete.

Record the blocker in `docs/STATUS.md` and report it clearly.

### Before editing

Before making changes:

1. Inspect the relevant code and tests.
2. Confirm that task dependencies are complete.
3. Produce a short implementation plan.
4. Identify the expected files to change.
5. Identify the tests required to prove the behavior.
6. Identify migration, configuration, compatibility, concurrency, and security
   risks.
7. Confirm that the task can be completed without implementing out-of-scope
   features.

---

## 6. Architecture Rules

The project must follow Clean Architecture principles.

Dependencies must point inward toward the domain and application core.

A recommended structure is:

```text
src/
  domain/
  application/
  infrastructure/
  presentation/
  workers/
  shared/
tests/
  unit/
  integration/
docs/
config/
deploy/
```

The exact folder structure may evolve through an explicit architectural
decision, but separation of concerns is mandatory.

### Domain layer

The domain layer contains:

- Entities.
- Value objects.
- Enums.
- Domain rules.
- Domain exceptions.
- Pure domain services when required.

The domain layer must not import or depend on:

- Telegram libraries.
- MongoDB drivers.
- AI provider SDKs.
- HTTP clients.
- Filesystem implementations.
- Scheduling frameworks.
- Presentation handlers.
- Infrastructure modules.

### Application layer

The application layer contains:

- Use cases.
- Application services.
- Commands and queries when appropriate.
- Data transfer models.
- Ports, protocols, and interfaces for external systems.
- Transaction and workflow orchestration.

It may depend on the domain layer.

It must not directly depend on concrete Telegram, MongoDB, AI, filesystem, or
scheduler implementations.

### Infrastructure layer

The infrastructure layer contains concrete adapters for:

- MongoDB repositories.
- Telegram User API clients.
- Telegram Bot API clients.
- AI providers.
- HTTP services.
- Media storage.
- Persistent job storage.
- Logging and monitoring integrations.

Provider-specific request and response models must not leak into the domain
layer.

### Presentation layer

The presentation layer contains:

- Telegram bot command handlers.
- Callback handlers.
- Administrator-facing messages.
- Request validation.
- Input and output mapping.

Presentation handlers must remain thin and delegate business work to application
use cases.

### Workers

Workers execute long-running or scheduled operations, including:

- Source-channel collection.
- AI processing jobs.
- Media processing.
- Delayed scoring.
- Scheduled publication.
- Cleanup and expiration handling.
- Retryable synchronization operations.

Workers must call application use cases rather than contain core business logic.

---

## 7. Core Implementation Rules

- Use Python type hints for all new and materially changed functions, methods,
  and public attributes.
- Public modules, classes, functions, and methods must include useful English
  documentation.
- Prefer small, focused modules and services.
- Avoid large functions and classes with unrelated responsibilities.
- Use explicit input and output models.
- Keep side effects isolated.
- Use dependency injection or explicit dependency construction.
- Avoid hidden global state.
- Do not hardcode Telegram channel IDs, administrator IDs, provider names,
  secrets, or production URLs in business logic.
- Preserve backward compatibility unless the active task explicitly permits a
  breaking change.
- Do not rename public contracts, configuration keys, database fields, or
  callback formats without documenting migration and compatibility impact.
- Do not add a new dependency unless it is required by the active task and its
  purpose is documented.
- Do not weaken, remove, skip, or rewrite existing tests merely to make a task
  pass.
- Do not silently ignore errors.
- Do not claim success based only on code generation; verify behavior.

---

## 8. Mandatory Text Encoding and Persian Content Rules

All text files must use UTF-8.

This includes:

- Python files.
- Markdown files.
- JSON files.
- YAML files.
- TOML files.
- Configuration templates.
- Log files.
- Shell scripts.
- Service files.
- Test fixtures.
- Telegram message templates.

### Python file operations

Python file operations must explicitly use:

```python
encoding="utf-8"
```

Example:

```python
from pathlib import Path

content = Path("message.txt").read_text(encoding="utf-8")
Path("output.txt").write_text(content, encoding="utf-8")
```

When using `open`, specify the encoding explicitly:

```python
with open("message.txt", "r", encoding="utf-8") as file:
    content = file.read()
```

### JSON containing Persian text

JSON containing Persian text must use:

```python
ensure_ascii=False
```

Example:

```python
import json
from pathlib import Path

data = {"message": "سلام"}

Path("data.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

### Persian Telegram content

Persian Telegram content must be preserved exactly.

Do not unintentionally change:

- Persian letters.
- Arabic and Persian variants of characters.
- Zero-width non-joiners.
- Spacing.
- Line breaks.
- Punctuation.
- Emoji.
- Custom Emoji entities.
- Premium Emoji entities.
- Telegram entity offsets and lengths.
- Text casing in usernames or identifiers where casing is meaningful.

Normalization must be explicit, limited to the requirement being implemented,
and covered by tests.

### Mojibake prevention

Mojibake is not acceptable.

Examples of corrupted content include:

```text
Ø³ÙØ§Ù
ÙØªÙ
Ú©Ø§Ø±Ø¨Ø±
```

Correct content must remain readable:

```text
سلام
متن
کاربر
```

When a task touches Persian, RTL, emoji, or Telegram text:

1. Review the human-readable diff.
2. Search touched text files for suspicious corruption markers such as:
   `Ø`, `Ù`, `Û`, `Ã`, `Â`, `�`, and unexpected `????`.
3. Verify representative Persian text manually.
4. Run encoding-specific tests when available.
5. Do not treat passing Python syntax, lint, or unit tests as sufficient proof
   that Persian text is intact.

---

## 9. Telegram Rules

- Use the Telegram User API for source-channel crawling and final publication
  where preserving Premium Emoji or account-specific entities requires it.
- Use the Telegram Bot API for administrator interaction, commands, and callback
  controls.
- Keep User API and Bot API responsibilities separate.
- Store Telegram sessions securely.
- Never commit generated session files.
- Treat source channel ID and source message ID as the idempotency identity for a
  collected message.
- Preserve original post content separately from destination-specific rewritten
  content.
- Process media groups as one logical post.
- Validate every administrator callback against:
  - Administrator identity.
  - Administrator active status and permission.
  - Post existence.
  - Current post state.
  - Destination-channel permission.
  - Duplicate-publication state.
- Callback operations must be safe under concurrent administrator actions.
- Publishing must be idempotent per post and destination channel.
- Final publication must not accidentally include administrator-only metadata
  headers.

---

## 10. MongoDB and Persistence Rules

MongoDB is the primary database.

- Define repository ports outside the infrastructure layer.
- Keep MongoDB-specific models, indexes, queries, and driver usage in
  infrastructure.
- Use a unique compound index for source channel ID and source message ID.
- Use TTL indexes for records that expire automatically.
- Temporary post data must use an `expires_at` field compatible with a MongoDB
  TTL index.
- Persistent jobs must survive application restarts.
- Job acquisition must be atomic.
- Use a lease, lock expiration, or equivalent approach so multiple workers do
  not process the same job concurrently.
- Do not store large binary media in MongoDB unless an explicit decision permits
  it.
- Store enough media metadata to retrieve, validate, expire, and republish the
  media safely.
- Database changes and index changes must be covered by integration tests when
  practical.
- Never assume that checking before insertion is sufficient for uniqueness;
  enforce uniqueness in MongoDB.

---

## 11. AI Provider Pipeline Rules

AI providers must be accessed through application-layer ports or protocols.

The domain and application logic must not depend on a specific provider SDK or
response structure.

The AI pipeline must be configuration-driven.

For each AI task:

1. Select enabled providers and models that support the task.
2. Order them by configured priority.
3. Skip providers that are disabled, rate-limited, in cooldown, or have an open
   circuit breaker.
4. Call the first eligible provider with a bounded timeout.
5. Validate the response against the task schema.
6. Apply only bounded retries appropriate for the failure type.
7. Fall back to the next configured model or provider when necessary.
8. Stop after the first valid result.
9. Record failure details when all providers fail.
10. Never invent or label a fabricated result as an AI result.

Additional rules:

- Invalid JSON, empty responses, schema violations, timeouts, provider errors,
  and rate limits may trigger fallback.
- Authentication failures and permanently unavailable models should not be
  retried repeatedly.
- Retry and fallback are different operations and must be represented
  separately.
- Free-provider quotas and rate limits must be respected.
- AI jobs must be persistent and restart-safe when the active task requires
  asynchronous processing.
- Cache keys must include the task type, normalized input hash, prompt version,
  schema version, and relevant language information.
- Prompt versions must be stored with AI results.
- Provider-specific results must be converted into application-owned result
  models.
- API keys must never be stored in source code or logs.

---

## 12. Configuration and Secrets

The repository must contain a safe configuration template, such as:

```text
config/configuration.example.json
```

The real local configuration must not be committed.

Configuration rules:

- Validate required configuration during startup.
- Fail with a clear configuration error when required values are missing or
  invalid.
- Keep configuration access centralized.
- Keep configuration models typed.
- Update the example configuration whenever keys change.
- Use environment variables or an approved secret-management mechanism for real
  secrets.
- Do not include real:
  - Telegram bot tokens.
  - Telegram API hashes.
  - Telegram session files.
  - AI API keys.
  - MongoDB passwords.
  - Worker tokens.
  - Private production URLs.
- Verify `.gitignore` when adding secret-bearing or generated files.

---

## 13. Reliability, Concurrency, and Error Handling

Every external operation must have a bounded timeout.

Retry behavior must be:

- Limited.
- Failure-type aware.
- Observable in logs.
- Safe against duplicate side effects.
- Implemented with backoff when appropriate.

Operations that may be delivered more than once must be idempotent.

This includes:

- Telegram update handling.
- Initial crawling and live-listener overlap.
- MongoDB inserts.
- AI jobs.
- Callback actions.
- Scheduled publications.
- Immediate publications.
- Message edits.
- Cleanup jobs.

Use explicit exceptions or result types for expected failures.

Distinguish between:

- Validation failures.
- Permanent configuration failures.
- Permission failures.
- Temporary network failures.
- Rate limits.
- Timeouts.
- Provider failures.
- Conflict or concurrency failures.
- Already-completed idempotent operations.

Do not silently swallow exceptions.

Do not expose sensitive exception details to administrators.

---

## 14. Logging and Observability

Use structured logging.

Logs should include relevant non-sensitive context such as:

- Correlation ID.
- Task or job ID.
- Post ID.
- Source channel ID.
- Source message ID.
- Destination channel ID.
- Administrator ID where appropriate.
- Provider and model name.
- Retry attempt.
- Fallback count.
- Processing state.
- Error category.

Never log:

- API keys.
- Tokens.
- Session content.
- Passwords.
- Authorization headers.
- Full secret-bearing URLs.
- Private credentials.

Persian log content must remain UTF-8-safe.

---

## 15. Testing Rules

Use `pytest` unless the repository has an explicitly approved alternative.

Every behavior change must include appropriate tests.

### Unit tests

Unit tests should cover pure domain and application behavior, including:

- State transitions.
- Idempotency decisions.
- Callback toggle logic.
- Schedule calculation.
- Text pruning and replacement.
- Entity offset reconstruction.
- Provider selection.
- Retry and fallback decisions.
- Response validation.
- Cache key generation.
- Configuration validation.
- Persian and UTF-8 preservation.

### Integration tests

Integration tests should cover infrastructure behavior, including:

- MongoDB repositories.
- Unique indexes.
- TTL indexes.
- Atomic job acquisition.
- Restart-safe scheduling.
- AI adapter request and response mapping.
- Telegram adapters when testable through fakes or approved test environments.
- Media storage lifecycle.

### Test quality

- Tests must assert behavior, not implementation trivia.
- Tests must be deterministic.
- Tests must not use production credentials or real private channels.
- Do not make uncontrolled live API requests in the default test suite.
- Do not delete an existing test simply because a new implementation breaks it.
- If a test cannot be run, report the exact reason.
- Never claim that an unexecuted test passed.

---

## 16. Verification Before Completion

Before declaring a task complete:

1. Run the verification commands listed in the task file.
2. Run relevant unit tests.
3. Run relevant integration tests.
4. Run the project lint command.
5. Run formatting checks.
6. Run static type checking.
7. Run configuration or migration checks when relevant.
8. Review the final diff for unrelated changes.
9. Review touched Persian, RTL, emoji, and Telegram text manually.
10. Check touched files for mojibake markers.
11. Verify that no secret, local configuration, generated session file, private
    media, or credential was added.
12. Verify that every objective acceptance criterion is satisfied.
13. Verify that repository documentation matches the implementation.

A task must not be marked complete while required verification fails.

When a check cannot be executed, keep the task incomplete unless the task file
explicitly permits that limitation. Report the limitation accurately.

---

## 17. Required Documentation Updates

At the end of every completed task:

- Mark the task completed in `docs/ROADMAP.md`.
- Update `docs/STATUS.md`.
- Update the active task file with its final status and verification results.
- Update `docs/CODE_MAP.md` when files, modules, responsibilities, or data flows
  changed.
- Update `docs/ARCHITECTURE.md` when the implemented architecture changed.
- Add an entry to `docs/DECISIONS.md` only for significant decisions.
- Update `docs/REQUIREMENTS.md` only when the product requirement itself was
  intentionally changed.
- Update configuration and running documentation when commands, settings, or
  deployment behavior changed.

Keep documentation concise.

Remove obsolete information instead of repeatedly appending conflicting notes.

---

## 18. Definition of Done

A task is complete only when all applicable conditions below are true:

1. The task dependencies were complete.
2. Work remained within the active task scope.
3. All objective acceptance criteria were satisfied.
4. Clean Architecture boundaries were preserved.
5. New code has appropriate type hints and English documentation.
6. Required unit tests were added or updated.
7. Required integration tests were added or updated.
8. All required verification commands passed.
9. Idempotency and concurrency behavior were considered where applicable.
10. External calls have bounded timeouts.
11. Retry behavior is bounded.
12. No secrets or generated session files were committed.
13. All text files use UTF-8.
14. Python file operations explicitly use `encoding="utf-8"`.
15. JSON containing Persian text uses `ensure_ascii=False`.
16. Persian Telegram content was preserved exactly.
17. No Mojibake was introduced.
18. Relevant documentation was updated.
19. The final diff contains no unrelated changes.
20. A suitable Git commit message was suggested.

Do not claim that a task is complete unless its acceptance criteria and required
verification have been checked.

---

## 19. End-of-Task Response

After completing a task, every final response must include the following exact
sections:

### Changed files

List every changed file with a brief explanation.

### Implementation summary

Explain what was implemented and the most important design decisions.

### Tests executed

List every test, lint, formatting, type-checking, migration, or verification
command that was actually run, together with its result.

If a check was not run, state that clearly and explain why.

### Known limitations

List unresolved limitations, assumptions, blockers, deferred behavior, or areas
that still require manual review.

Use `None` only when there are genuinely no known limitations.

### Suggested Git commit message

Provide one short English Git commit message that accurately describes the
completed task.

When relevant, also include:

- Verification results.
- Important architectural decisions.
- Source-text safety checks.
- The next recommended task.

Never invent successful test results or claim completion without verification.
