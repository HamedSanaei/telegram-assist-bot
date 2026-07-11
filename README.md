# Telegram Assist Bot

پایهٔ ماژولار یک دستیار مدیریت کانال‌های تلگرام با Python و معماری تمیز است.
Scaffold معماری، سامانهٔ typed Configuration، مدل خالص چرخهٔ عمر Post و
Repository یکتای Post با Adapter ناهمگام MongoDB آماده‌اند. Composition Root
پایه نیز Config، Logging و MongoDB را برای یک Startup check کنترل‌شده متصل
می‌کند. اتصال Telegram، پردازش هوش مصنوعی و Workerهای اجرایی هنوز ساخته نشده‌اند.

## پیش‌نیاز توسعه

- CPython 3.12 یا 3.13؛ نسخهٔ پایه 3.12 است.
- `uv` برای ساخت محیط، قفل وابستگی‌ها و اجرای فرمان‌ها.
- Git برای کشف فایل‌های تغییرکرده و بررسی Policyهای مخزن.
- MongoDB 7.0 یا جدیدتر فقط برای اجرای تست‌های Integration مربوط به Persistence.

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
$env:UV_CACHE_DIR = Join-Path $PWD ".uv-cache"
uv lock --check
$env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017/?directConnection=true"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$pytestTempRoot = Join-Path $env:UV_CACHE_DIR "pytest-tmp"
New-Item -ItemType Directory -Path $pytestTempRoot -Force | Out-Null
$fullBaseTemp = Join-Path $pytestTempRoot "full-$timestamp"
$pytestExitCode = 1
try {
    uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90 --basetemp $fullBaseTemp
    $pytestExitCode = $LASTEXITCODE
} finally {
    Remove-Item Env:\TEST_MONGODB_URI
}
if ($pytestExitCode -ne 0) {
    throw "pytest failed with exit code $pytestExitCode"
}
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

## تست Integration با MongoDB

MongoDB آزمایشی محلی را روی loopback و پورت `27017` اجرا کنید؛ سپس تست‌های
Infrastructure مربوط به T004 و Startup مربوط به T006 را با این فرمان‌ها اجرا
کنید:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD ".uv-cache"
$env:TEST_MONGODB_URI = "mongodb://127.0.0.1:27017/?directConnection=true"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$pytestTempRoot = Join-Path $env:UV_CACHE_DIR "pytest-tmp"
New-Item -ItemType Directory -Path $pytestTempRoot -Force | Out-Null
$integrationBaseTemp = Join-Path $pytestTempRoot "integration-$timestamp"
$pytestExitCode = 1
try {
    uv run pytest tests/integration/infrastructure/persistence/test_mongodb_post_repository.py tests/integration/test_foundation_startup.py --basetemp $integrationBaseTemp
    $pytestExitCode = $LASTEXITCODE
} finally {
    Remove-Item Env:\TEST_MONGODB_URI
}
if ($pytestExitCode -ne 0) {
    throw "pytest failed with exit code $pytestExitCode"
}
```

Harness فقط URI بدون credential، بدون نام database و با
`directConnection=true` روی loopback را می‌پذیرد. هر تست database یکتایی با
پیشوند `tab_t004_` می‌سازد و cleanup محافظت‌شده فقط همان database را حذف
می‌کند. هیچ‌گاه این متغیر را به MongoDB تولید یا دادهٔ مشترک اشاره ندهید. CI
نیز یک MongoDB نسخه‌ثابت و موقت برای همین تست‌ها بالا می‌آورد.
Suiteها را موازی روی یک basetemp اجرا نکنید؛ هر invocation باید مسیر یکتایی زیر
`.uv-cache/pytest-tmp/` یا یک مسیر ignored معادل داشته باشد.

## Configuration محلی

فایل [نمونهٔ امن](config/configuration.example.json) قرارداد Schema نسخهٔ ۱ را
نشان می‌دهد. این فایل فقط نام Environment Variableها را نگه می‌دارد؛ مقدار
MongoDB URI، Telegram credential، Bot token و AI key باید در Environment قرار
گیرد. برای اجرای محلی، یک فایل ignored مانند
`config/configuration.local.json` بسازید و مقادیر واقعی را Commit نکنید.

API متمرکز Loader که Composition Root مصرف می‌کند چنین است:

```python
from pathlib import Path

from telegram_assist_bot.shared.config import load_configuration

loaded = load_configuration(Path("config/configuration.local.json"))
```

Loader فایل را با UTF-8 سخت‌گیرانه می‌خواند، همهٔ خطاهای مستقل را با مسیر فیلد
گزارش می‌کند و پیش از هر اتصال خارجی متوقف می‌شود. Configuration پس از Startup
immutable است و تغییر آن در نسخهٔ فعلی به Restart نیاز دارد.

## Startup check پایه

Entry point فعلی فقط Configuration را validate، اتصال MongoDB را با timeout
محدود بررسی، Indexهای T004 را initialize و سپس همان client را تمیز می‌بندد. هیچ
Telegram client، Worker یا Feature محصولی در این فرمان ساخته نمی‌شود:

```powershell
uv run python -m telegram_assist_bot --config config/configuration.local.json
```

مسیر Config به‌ترتیب از `--config PATH`، سپس `TAB_CONFIG_PATH` و در نهایت
`config/configuration.json` نسبت به working directory انتخاب می‌شود. CLI فقط
مسیر غیرحساس Config را می‌پذیرد؛ URI، Token، Password، API key و Session هرگز
argument خط فرمان نیستند. مقادیر Secret همچنان فقط از Environment Variableهای
ارجاع‌شده داخل Config resolve می‌شوند.

قرارداد خروج پایدار است: `0` برای Startup/Shutdown موفق، `2` برای خطای
CLI/Configuration و `3` برای خطای Infrastructure. Eventهای lifecycle به‌صورت
JSON خطی UTF-8 و redacted روی stderr نوشته می‌شوند.

## ساختار اولیه

کد Package زیر `src/telegram_assist_bot/` قرار دارد. زیرPackageهای `domain`،
`application`، `infrastructure`، `presentation`، `workers`، `shared` و
`bootstrap` در T001 importable شدند. رفتار T002 فقط در `shared/config` و قرارداد
pure Domain مربوط به T003 فقط در `domain/posts` قرار دارد.

قرارداد pure Repository مربوط به T004 در `application/ports` و mapper، index
initializer و Adapter ناهمگام MongoDB در `infrastructure/persistence/mongodb`
قرار دارند. Composition Root و CLI پایهٔ T006 در `bootstrap` و `__main__.py`
قرار دارند و فقط همین مسیر Foundation را wire می‌کنند.

مجموعهٔ کامل تست‌ها برای Integrationهای Persistence به MongoDB آزمایشی loopback
نیاز دارد، اما به credential، Telegram، AI یا سرویس تولیدی متصل نمی‌شود.
Markerهای `integration`، `contract`، `e2e` و `live` تست‌ها را دسته‌بندی می‌کنند؛
Marker `live` در اجرای پیش‌فرض غیرفعال است.

## دادهٔ حساس و Runtime

Token، API key، فایل Session، Config محلی، Media، Log و داده‌های `var/` نباید
Commit شوند. `.gitignore` مانع معمول را فراهم می‌کند و baseline ابزار
`detect-secrets` در Quality Gate بررسی می‌شود. فقط
`config/configuration.example.json` برای Commit مجاز است و Configurationهای
محلی/محیطی طبق `.gitignore` خارج از Git می‌مانند.
