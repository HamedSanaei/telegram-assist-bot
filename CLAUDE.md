# AGENTS.md

## Project Overview

This project is a Python-based Telegram channel administration and publishing system.

The application is designed to collect posts from source Telegram channels, classify and deduplicate them using AI providers, test VPN configurations when needed, store posts temporarily, request administrator approval, and publish approved posts to configured destination channels.

The project must always be implemented in **Python**.

The system supports multiple types of Telegram channels, including:

* General news channels
* Breaking news channels
* Technology-related channels
* VPN-related channels
* Channels that publish `vmess` and `vless` VPN configurations

The system must be designed for long-term maintainability, clean separation of responsibilities, and easy future extension.

---

## Mandatory Language and Encoding Rules

All source files, documentation files, configuration templates, JSON files, Markdown files, and text resources must be saved using:

```text
UTF-8 encoding
```

Persian text must never become Mojibake.

Examples of broken Mojibake that must be avoided:

```text
Ø³ÙØ§Ù
ÙØªÙ
Ú©Ø§Ø±Ø¨Ø±
```

Correct Persian text must remain readable:

```text
سلام
متن
کاربر
```

Rules:

* Always read and write files using UTF-8.
* Never use Windows-1252, ISO-8859-1, or any legacy encoding.
* When opening text files in Python, explicitly use `encoding="utf-8"`.
* JSON files must be written with `ensure_ascii=False` when Persian text is stored.
* Markdown files must be UTF-8.
* Logs containing Persian text must be UTF-8-safe.
* Telegram messages containing Persian text must be preserved exactly.

Example:

```python
from pathlib import Path

content = Path("message.txt").read_text(encoding="utf-8")
Path("output.txt").write_text(content, encoding="utf-8")
```

For JSON:

```python
import json
from pathlib import Path

data = {"message": "سلام"}
Path("data.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
```

---

## Documentation Language Rule

All code comments, docstrings, documentation files, architecture notes, README files, code maps, configuration explanations, and developer-facing explanations must be written in **English**.

Persian text may appear only when it is actual application content, Telegram message content, test sample content, or user-facing localized content.

---

## Required Architecture

The project must follow **Clean Architecture**.

The codebase must be organized so that business rules are independent from frameworks, databases, Telegram libraries, external AI providers, and infrastructure details.

The architecture must make future features easy to add without rewriting existing modules.

Required layers:

```text
src/
  domain/
  application/
  infrastructure/
  presentation/
  workers/
  shared/
tests/
docs/
config/
deploy/
```

Recommended responsibilities:

```text
domain/
  Core entities, value objects, enums, domain rules, and interfaces.
  This layer must not depend on Telegram, MongoDB, SQLite, AI APIs, or external libraries.

application/
  Use cases and orchestration logic.
  This layer coordinates domain logic through interfaces.
  It must not directly call Telegram, MongoDB, SQLite, or external AI APIs.

infrastructure/
  Implementations of repositories, AI providers, Telegram clients, MongoDB, SQLite,
  file storage, VPN testing adapters, and external services.

presentation/
  Telegram bot handlers, callback handlers, command handlers, and admin approval UI.

workers/
  Background workers for collecting posts, processing queues, testing VPN configs,
  scheduled price publishing, and cleanup tasks.

shared/
  Common utilities, constants, logging, configuration loading, encoding helpers,
  validation helpers, and error types.
```

Dependencies must point inward:

```text
presentation -> application -> domain
workers      -> application -> domain
infrastructure -> application/domain interfaces
```

The `domain` layer must never import from:

```text
infrastructure
presentation
workers
aiogram
telethon
pymongo
sqlite3
requests
httpx
```

---

## Main Application Responsibilities

The application must support the following features:

1. Collect new posts from configured source Telegram channels.
2. Store collected posts temporarily for up to 14 days.
3. Store unstructured post content and media metadata in MongoDB.
4. Store general application data in SQLite.
5. Store API tokens and sensitive settings in `configuration.json`.
6. Detect duplicate or near-duplicate posts using AI.
7. Use z.ai as the primary AI provider.
8. Use DeepSeek as the fallback AI provider if z.ai is unavailable.
9. Classify posts using AI into categories such as:

   * General news
   * Breaking news
   * Technology
   * VPN
   * VPN configuration
   * Irrelevant
10. Extract `vmess` and `vless` configurations from VPN-related posts.
11. Test VPN configurations from an Iran-based worker server.
12. Mark VPN configs as eligible only if they work from the Iran server.
13. Send new eligible posts to an approval assistant bot.
14. Show callback buttons under approval messages for each destination channel.
15. Require final admin confirmation before publishing.
16. Publish approved posts to selected Telegram channels.
17. Update callback buttons with a success mark after successful publishing.
18. Fetch and publish USD price updates twice per day.
19. Show USD price change compared to the previous recorded price.
20. Remove expired post data after 14 days.

---

## Database Rules

### SQLite

SQLite must be used for structured application data such as:

* Source channels
* Destination channels
* Admin users
* Approval states
* Publishing logs
* Queue states
* Scheduled job states
* Dollar price history
* Application settings
* Worker registration
* Error logs

SQLite migrations must be versioned and repeatable.

Do not store large Telegram media files directly in SQLite.

### MongoDB

MongoDB must be used for unstructured or semi-structured post data such as:

* Raw Telegram post text
* Raw Telegram metadata
* Media metadata
* Extracted links
* Extracted VPN configs
* AI classification results
* AI duplicate detection results
* Temporary post snapshots

MongoDB documents that should expire after 14 days must use an `expires_at` field and a TTL index.

Example MongoDB TTL requirement:

```text
Collection: posts
Field: expires_at
TTL: expire after the configured date
```

---

## Configuration Rules

Sensitive values must be stored in:

```text
config/configuration.json
```

The real `configuration.json` file must not be committed to GitHub.

A public empty template must always be committed:

```text
config/configuration.example.json
```

The template must show users which API keys and values are required.

The project must include a `.gitignore` rule like this:

```gitignore
config/configuration.json
```

The `configuration.example.json` file must always stay updated when new configuration keys are added.

Required configuration template:

```json
{
  "telegram": {
    "bot_token": "",
    "approval_bot_token": "",
    "api_id": "",
    "api_hash": "",
    "source_channels": [],
    "destination_channels": []
  },
  "ai": {
    "primary_provider": "zai",
    "fallback_provider": "deepseek",
    "zai_api_key": "",
    "deepseek_api_key": "",
    "deduplication_model": "",
    "classification_model": ""
  },
  "database": {
    "sqlite_path": "data/app.db",
    "mongodb_connection_string": "",
    "mongodb_database": "telegram_admin_bot"
  },
  "storage": {
    "media_directory": "data/media"
  },
  "vpn_testing": {
    "iran_worker_enabled": true,
    "worker_api_url": "",
    "worker_api_token": "",
    "test_timeout_seconds": 30
  },
  "scheduler": {
    "usd_price_publish_times": [
      "09:00",
      "21:00"
    ],
    "timezone": "Asia/Tehran"
  },
  "logging": {
    "level": "INFO",
    "file": "logs/app.log"
  }
}
```

---

## Code Documentation Requirements

Every class, function, method, and public module must be fully documented in English.

Documentation must explain:

* Purpose
* Parameters
* Return value
* Exceptions or failure cases
* Side effects
* Example usage when useful

Every Python function must include type hints.

Required function style:

```python
def classify_post(post_text: str, language_hint: str | None = None) -> PostCategory:
    """
    Classify a Telegram post into one of the supported post categories.

    Args:
        post_text:
            The raw text content of the Telegram post.
        language_hint:
            Optional language hint such as "fa" or "en". If omitted, the
            classifier should infer the language automatically.

    Returns:
        The detected post category.

    Raises:
        AiProviderError:
            Raised when all configured AI providers fail.
        InvalidPostError:
            Raised when the provided post text is empty or invalid.

    Example:
        category = classify_post("Breaking news: ...", language_hint="en")
    """
```

Every class must include a class-level docstring:

```python
class PostApprovalService:
    """
    Coordinates the approval workflow for collected Telegram posts.

    This service sends posts to the approval bot, tracks admin decisions,
    and dispatches approved posts to the publishing service.

    Example:
        service = PostApprovalService(repository, publisher)
        await service.request_approval(post_id)
    """
```

---

## Testing Requirements

Unit tests are mandatory for all major parts of the application.

Every new feature must include tests before it is considered complete.

Use `pytest` as the default test framework.

Recommended test structure:

```text
tests/
  unit/
    domain/
    application/
    infrastructure/
    presentation/
  integration/
    mongodb/
    sqlite/
    telegram/
    ai/
    vpn_worker/
```

Required test coverage areas:

* Post collection normalization
* Persian UTF-8 handling
* Mojibake prevention
* AI duplicate detection service
* AI fallback from z.ai to DeepSeek
* AI post classification
* VPN config extraction
* `vmess` parsing
* `vless` parsing
* Iran worker test request handling
* Approval callback logic
* Publishing state changes
* SQLite repositories
* MongoDB repositories
* 14-day expiration logic
* USD price history comparison
* Configuration loading and validation

All tests must be runnable with:

```bash
pytest
```

The project must also support:

```bash
pytest tests/unit
pytest tests/integration
```

When adding or changing functionality, update or add tests in the same change.

---

## AI Provider Rules

The application must support multiple AI providers through interfaces.

Do not hardcode z.ai or DeepSeek directly inside business logic.

Use an interface such as:

```python
class AiProvider(Protocol):
    """
    Defines the interface for AI providers used for classification and
    duplicate detection.
    """

    async def classify_post(self, text: str) -> AiClassificationResult:
        """
        Classify a Telegram post.

        Args:
            text:
                Raw post text.

        Returns:
            The AI classification result.
        """

    async def is_duplicate(self, new_text: str, existing_texts: list[str]) -> DuplicateCheckResult:
        """
        Check whether a new post is duplicate or near-duplicate.

        Args:
            new_text:
                New post text.
            existing_texts:
                Existing post texts to compare against.

        Returns:
            Duplicate check result.
        """
```

The primary AI provider must be z.ai.

If z.ai fails, times out, or returns an invalid response, the application must automatically use DeepSeek as fallback.

AI provider errors must be logged clearly.

---

## VPN Configuration Testing Rules

The system must detect and extract VPN configs from Telegram posts.

Supported config types:

```text
vmess
vless
```

VPN configuration testing must not run inside the main Telegram bot process.

VPN testing must be handled by a separate worker that can run on an Iran-based server.

The main application sends a test request to the Iran worker.

The Iran worker tests whether the config works from the Iran network.

If the config works, the post becomes eligible for VPN channel publishing.

If the config does not work, the post must not be published automatically to VPN channels.

The worker must expose a secure API protected by a token from `configuration.json`.

---

## Approval Bot Rules

The approval bot must receive new eligible posts and show inline callback buttons.

Each approval message must include buttons like:

```text
Send to Channel A
Send to Channel B
Send to Channel C
```

After an admin clicks a channel button, the bot must ask for final confirmation.

After successful publishing, the related button must be updated with:

```text
✅
```

Publishing state must be saved in SQLite.

The system must prevent duplicate publishing to the same channel.

The system must support publishing one post to multiple destination channels.

---

## USD Price Publishing Rules

The application must publish USD price updates twice per day.

USD price sources must be configurable.

Each fetched price must be stored in SQLite.

Each new published price message must include the change compared to the previous stored price.

The price publishing job must be documented in the run instructions.

The scheduled times must be configurable in `configuration.json`.

---

## Code Map Requirement

The project must always include a code map file:

```text
docs/CODE_MAP.md
```

This file must explain the full structure of the codebase.

It must be updated after every meaningful code change.

The code map must help future AI agents and developers understand the project quickly.

The code map must include:

* Project purpose
* Main architecture
* Folder structure
* Important modules
* Main classes and services
* Data flow
* Background workers
* Telegram bots
* Database usage
* Configuration files
* Test structure
* Deployment files
* How to add a new feature

Required minimum structure:

```markdown
# Code Map

## Project Purpose

## Architecture Overview

## Folder Structure

## Domain Layer

## Application Layer

## Infrastructure Layer

## Presentation Layer

## Workers

## Databases

## Configuration

## Telegram Bots

## VPN Testing Worker

## Scheduled Jobs

## Tests

## Deployment

## How to Add a New Feature

## Last Updated
```

The `Last Updated` section must be updated whenever the file is changed.

---

## Run Instructions Requirement

The project must always include an English run guide:

```text
docs/RUNNING.md
```

This file must explain how to install, configure, run, test, and deploy the project.

It must always be updated when the application behavior, commands, configuration keys, worker setup, or deployment process changes.

The file must include:

* Requirements
* Python version
* Virtual environment setup
* Dependency installation
* Configuration setup
* MongoDB setup
* SQLite setup
* Running migrations
* Running the main bot
* Running the approval bot
* Running the collector
* Running the scheduler
* Running tests
* Running the Iran VPN testing worker
* Installing the Iran worker on Ubuntu
* Enabling the Iran worker as a systemd service
* Testing `vmess` configs
* Testing `vless` configs
* Common troubleshooting steps

Required minimum structure:

```markdown
# Running the Telegram Admin Bot

## Requirements

## Installation

## Configuration

## Database Setup

## Running the Application

## Running Tests

## Running the Iran VPN Testing Worker

## Testing VLESS Configurations

## Testing VMESS Configurations

## Ubuntu systemd Service

## Troubleshooting
```

---

## Ubuntu Service File Requirement

The project must always include a systemd service file template for Ubuntu:

```text
deploy/telegram-admin-bot.service
```

If the service changes, this file must be updated.

Required template:

```ini
[Unit]
Description=Telegram Admin Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram-admin-bot
ExecStart=/opt/telegram-admin-bot/.venv/bin/python -m src.main
Restart=always
RestartSec=5
User=telegrambot
Group=telegrambot
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```

The project must also include a worker service template:

```text
deploy/iran-vpn-worker.service
```

Required template:

```ini
[Unit]
Description=Iran VPN Testing Worker
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram-admin-bot
ExecStart=/opt/telegram-admin-bot/.venv/bin/python -m src.workers.iran_vpn_worker
Restart=always
RestartSec=5
User=telegrambot
Group=telegrambot
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
```

The service files must be documented in `docs/RUNNING.md`.

---

## Publish Output Requirement

The project must always include a build script:

```text
scripts/build_publish.py
```

After every meaningful code change, the publish outputs must be regenerated by running:

```bash
python scripts/build_publish.py
```

The script must produce, on every run:

```text
publish/windows/   Standalone one-file executables (PyInstaller) for:
                   telegram-admin-bot.exe, telegram-collector.exe,
                   iran-vpn-worker.exe, plus the configuration template
                   and a short README.txt.
publish/ubuntu/    telegram-admin-bot-<version>.tar.gz source bundle
                   (src, deploy, docs, requirements, config template)
                   including an install.sh for /opt/telegram-admin-bot.
publish/BUILD_INFO.txt   Version, UTC timestamp, build platform.
```

Rules:

* The Windows executable build is only possible on Windows; on other
  platforms the script must still build the Ubuntu bundle and record the
  skipped executable build in `BUILD_INFO.txt`.
* `python scripts/build_publish.py --skip-exe` may be used for a quick
  Ubuntu-only rebuild, but a full build (with executables) must be run
  before delivering a change.
* The `publish/` and `build/` directories are generated artifacts and must
  stay git-ignored; they must never be committed.
* When entrypoints change, the launcher scripts in `scripts/entry_*.py`
  and this build script must be updated in the same change.

---

## Extensibility Rules

The code must be written so that future features can be added easily.

Required practices:

* Use interfaces and dependency injection.
* Avoid hardcoded provider names.
* Avoid hardcoded Telegram channel IDs in business logic.
* Avoid hardcoded API keys.
* Avoid mixing Telegram handlers with business logic.
* Avoid mixing database logic with use cases.
* Avoid large classes.
* Avoid long functions.
* Prefer small focused services.
* Prefer explicit data models.
* Prefer clear input and output types.
* Keep side effects isolated in infrastructure services.
* Use repository interfaces for persistence.
* Use provider interfaces for external APIs.
* Use separate services for classification, deduplication, approval, publishing, scheduling, and VPN testing.

When adding a new feature:

1. Add or update domain models if needed.
2. Add or update application use cases.
3. Add infrastructure implementations.
4. Add Telegram handlers or worker entrypoints only at the edge.
5. Add or update configuration template.
6. Add or update unit tests.
7. Add or update integration tests if required.
8. Update `docs/CODE_MAP.md`.
9. Update `docs/RUNNING.md`.
10. Update service files if execution commands changed.

---

## Logging Rules

The application must use structured logging.

Logs must be UTF-8-safe.

Logs must include enough context to debug issues, such as:

* Post ID
* Source channel ID
* Destination channel ID
* AI provider name
* Worker name
* Job name
* Error message
* Retry attempt
* Publishing status

Do not log sensitive values such as:

* Telegram bot tokens
* Telegram API hash
* z.ai API key
* DeepSeek API key
* Worker API token
* MongoDB password

---

## Error Handling Rules

The application must use explicit custom exceptions for expected failure cases.

Examples:

```text
ConfigurationError
AiProviderError
DuplicateDetectionError
PostClassificationError
TelegramPublishError
VpnConfigParseError
VpnConnectivityTestError
RepositoryError
ApprovalStateError
```

Do not silently ignore errors.

All recoverable errors must be logged.

External API calls must have timeouts.

Fallback logic must be tested.

---

## Queue and Background Job Rules

Redis is not required for this project.

Queue state can be stored in SQLite.

The application may use SQLite tables for:

* Pending collection tasks
* Pending AI classification tasks
* Pending VPN test tasks
* Pending approval tasks
* Pending publishing tasks
* Failed retryable tasks
* Completed tasks

Each queued item must have:

```text
id
type
status
attempts
last_error
scheduled_at
created_at
updated_at
```

Recommended statuses:

```text
pending
processing
waiting_approval
approved
published
failed
skipped
duplicate
expired
```

Workers must be safe to restart.

Workers must not process the same item twice at the same time.

---

## Security Rules

Never commit real secrets.

Never print real secrets in logs.

Never expose approval actions to unauthorized users.

Only configured admin Telegram user IDs may approve posts.

The Iran VPN testing worker must require authentication.

All external HTTP calls must use timeouts.

All callback actions must validate:

* Admin identity
* Post existence
* Current approval state
* Destination channel permission
* Duplicate publishing state

---

## GitHub Readiness Rules

The GitHub version of the project must include:

```text
AGENTS.md
README.md
docs/CODE_MAP.md
docs/RUNNING.md
config/configuration.example.json
deploy/telegram-admin-bot.service
deploy/iran-vpn-worker.service
tests/
.gitignore
```

The GitHub version must not include:

```text
config/configuration.json
API keys
Telegram bot tokens
Telegram API hash
MongoDB passwords
Real production logs
Downloaded private Telegram media
```

The README must briefly explain:

* What the project does
* Main features
* Architecture
* Requirements
* Quick start
* Configuration
* Testing
* Deployment documentation link

---

## Definition of Done

A task is not complete until all of the following are true:

1. The code follows Clean Architecture.
2. The code is written in Python.
3. All text files are UTF-8.
4. Persian text does not contain Mojibake.
5. Public classes and functions have English docstrings.
6. Function parameters and return values are documented.
7. Usage examples are included where helpful.
8. Unit tests are added or updated.
9. Existing tests pass.
10. `config/configuration.example.json` is updated if configuration changed.
11. `docs/CODE_MAP.md` is updated.
12. `docs/RUNNING.md` is updated.
13. systemd service files are added or updated if execution commands changed.
14. No secrets are committed.
15. The code remains easy to extend.
16. The publish outputs are regenerated with `python scripts/build_publish.py`
    (Windows executables and the Ubuntu bundle in `publish/`).
