# Telegram Assist Bot

پایهٔ ماژولار یک دستیار مدیریت کانال‌های تلگرام با Python و معماری تمیز است. در
وضعیت فعلی فقط Scaffold و ابزارهای توسعه وجود دارند و هیچ اتصال اجرایی به
Telegram، MongoDB یا سرویس هوش مصنوعی ساخته نشده است.

## پیش‌نیاز توسعه

- CPython 3.12 یا 3.13؛ نسخهٔ پایه 3.12 است.
- `uv` برای ساخت محیط، قفل وابستگی‌ها و اجرای فرمان‌ها.
- Git برای کشف فایل‌های تغییرکرده و بررسی Policyهای مخزن.

نصب تکرارپذیر وابستگی‌های توسعه از ریشهٔ مخزن:

```powershell
uv sync --locked --group dev
```

`pyproject.toml` منبع declarationها و `uv.lock` منبع نسخه‌های resolveشده است.
Lockfile دوم یا نصب مستقل dependencyها نباید به workflow رسمی افزوده شود.
گزینهٔ `--no-build-isolation` نیز عمدی است: نسخهٔ دقیق `hatchling` از گروه
قفل‌شدهٔ توسعه استفاده می‌شود و Build در CI resolve پنهان و جداگانه ندارد.

## Quality Gateهای محلی

```powershell
uv lock --check
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
$trackedFiles = git ls-files
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline $trackedFiles
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
uv run python -c "import telegram_assist_bot"
git diff --check
git status --short
```

حالت `--changed` فایل‌های staged، unstaged و untracked غیرignored را بررسی
می‌کند. حالت `--all` همهٔ فایل‌های متنی tracked و untracked غیرignored را با
UTF-8 سخت‌گیرانه اسکن می‌کند و برای CI مناسب است.

## ساختار اولیه

کد Package زیر `src/telegram_assist_bot/` قرار دارد. زیرPackageهای `domain`،
`application`، `infrastructure`، `presentation`، `workers`، `shared` و
`bootstrap` در T001 عمداً فقط importable هستند و رفتار محصولی ندارند.

تست‌های پیش‌فرض شبکه، دیتابیس، credential یا سرویس زنده لازم ندارند. تست‌های
آینده با Markerهای `integration`، `contract`، `e2e` و `live` دسته‌بندی می‌شوند؛
Marker `live` در اجرای پیش‌فرض غیرفعال است.

## دادهٔ حساس و Runtime

Token، API key، فایل Session، Config محلی، Media، Log و داده‌های `var/` نباید
Commit شوند. `.gitignore` مانع معمول را فراهم می‌کند و baseline ابزار
`detect-secrets` در Quality Gate بررسی می‌شود. فایل نمونهٔ Configuration در
Task بعدی تعریف خواهد شد.
