# Telegram Assist Bot

پایهٔ ماژولار یک دستیار مدیریت کانال‌های تلگرام با Python و معماری تمیز است.
Scaffold معماری، سامانهٔ typed Configuration، مدل خالص چرخهٔ عمر Post و
Repository یکتای Post با Adapter ناهمگام MongoDB آماده‌اند. Composition Root
Config، Logging، MongoDB و vertical slice دریافت متن Telegram User API را متصل
می‌کند. ورود صریح، اعتبارسنجی Session/Premium/کانال، crawl روز جاری و listener
زنده پیاده شده‌اند. ذخیره و پاک‌سازی خصوصی Media، Album پایدار، duplicate دقیق،
محتوای مستقل مقصد، دسته‌بندی پایه و pipeline بازیابی‌پذیر آماده‌سازی نیز پیاده
شده‌اند؛ پردازش هوش مصنوعی هنوز ساخته نشده است.
تعامل مدیریتی private با Bot API، callback opaque، پیام تأیید مستقل، Keyboard
مقصد، Toggle اتمیک و همگام‌سازی چندمدیره نیز در Milestone 3 آماده شده‌اند؛
انتشار فوری متن/Media/Album و زمان‌بندی بومی Telegram با outbox پایدار نیز آماده است.

## تعامل مدیران و تأیید

Bot SDK برابر `aiogram==3.29.1` است. فقط private chat مدیران عددی Config‌شده با
role `admin` و permissionهای `approval.view`/`approval.toggle` پذیرفته می‌شود.
Bot token فقط از Environment reference خوانده می‌شود و command خط فرمان حاوی
Secret وجود ندارد. long polling عملیاتی فقط با command صریح `approval-bot` و
resourceهای Composition Root اجرا می‌شود.

Callbackها opaque، یک‌بارمصرف، دارای عمر ۱۴روز و کمتر از ۶۴ بایت‌اند.
هر مقصد یک ردیف با `🕒 زمان‌بندی`/`⚡ فوری` دارد و حالت منتخب با `✅` نمایش داده
می‌شود. بیش از ۲۰ مقصد مجاز fail-fast است. پس از commit موفق Selection، حالت
فوری job پایدار Publication و حالت زمان‌بندی‌شده command بومی پایدار را بدون
Confirm جدا dispatch می‌کند؛ Handler هیچ User API Session یا ارسال مقصد ندارد.

هر مدیر یک header و content مستقل می‌گیرد. Sync حداکثر سه attempt برای شکست موقت
دارد، پیام حذف‌شده را inactive می‌کند و stale version را روی UI جدید نمی‌نویسد.

## پیش‌نیاز توسعه

- CPython 3.12، 3.13 یا 3.14؛ نسخهٔ پایه 3.12 است.
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
Infrastructure مربوط به Persistence، Startup و Milestone 1 را با این فرمان‌ها اجرا
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
    uv run pytest tests/integration/infrastructure/persistence/test_mongodb_post_repository.py tests/integration/test_foundation_startup.py tests/integration/test_crawl_today_text_posts.py tests/integration/test_live_text_listener.py tests/integration/test_concurrent_idempotent_ingestion.py tests/integration/test_ingestion_recovery.py tests/e2e/test_text_ingestion_restart.py --basetemp $integrationBaseTemp
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
MongoDB URI، Telegram credential، Bot token و AI key نباید هرگز در آن ثبت شود.
برای اجرای محلی، یک فایل ignored مانند `config/configuration.local.json` بسازید
و مقادیر واقعی را Commit نکنید. در فایل محلی، هر Secret می‌تواند همچنان مرجع
Environment باشد یا مستقیماً وارد شود؛ برای نمونه:

```json
{
  "mongodb": {"uri": "mongodb://127.0.0.1:27017"},
  "telegram": {
    "user": {
      "api_id": 123456,
      "api_hash": "your-api-hash",
      "phone_number": "+989120000000"
    },
    "bot": {"token": "your-bot-token"}
  }
}
```

این شکل مستقیم فقط در `configuration.local.json` یا
`configuration.<profile>.local.json` مجاز است. Config نمونه و Configهای
غیرمحلی فقط `environment_variable` را می‌پذیرند؛ برای Production همچنان
Environment Variable یا Secret Manager مسیر ترجیحی است.

API متمرکز Loader که Composition Root مصرف می‌کند چنین است:

```python
from pathlib import Path

from telegram_assist_bot.shared.config import load_configuration

loaded = load_configuration(Path("config/configuration.local.json"))
```

Loader فایل را با UTF-8 سخت‌گیرانه می‌خواند، همهٔ خطاهای مستقل را با مسیر فیلد
گزارش می‌کند و پیش از هر اتصال خارجی متوقف می‌شود. Configuration پس از Startup
immutable است و تغییر آن در نسخهٔ فعلی به Restart نیاز دارد.

## Startup و دریافت متن Telegram

فرمان پیش‌فرض فقط Configuration را validate، اتصال MongoDB را با timeout محدود
بررسی، Indexها را initialize و سپس همان client را تمیز می‌بندد:

```powershell
uv run python -m telegram_assist_bot --config config/configuration.local.json
```

ورود تعاملی فقط با command صریح انجام می‌شود. شماره تلفن، API hash، کد تأیید و
2FA از Environment/prompt امن می‌آیند و هیچ Secret آرگومان CLI نیست:

```powershell
uv run python -m telegram_assist_bot login --config config/configuration.local.json
```

اجرای vertical slice متن، Session موجود را بدون prompt اعتبارسنجی می‌کند، Premium
و دسترسی Source/Destination را می‌سنجد، ابتدا subscription محدود Listener را
می‌سازد، سپس پیام‌های متنی/Caption امروز را crawl می‌کند و در ادامه Listener را
مصرف می‌کند. همین فرمان validation، crawl و listener را یکجا اجرا می‌کند؛ command
مستقل و موازی برای crawl یا listener وجود ندارد:

```powershell
uv run --python 3.12 python -m telegram_assist_bot ingest --config config/configuration.local.json
```

فرمان قدیمی زیر alias سازگار همان runtime کامل متن، Media و آماده‌سازی محتوا است:

```powershell
uv run --python 3.12 python -m telegram_assist_bot ingest-text --config config/configuration.local.json
```

### Optional media previews

Set `media.preview_enabled` to `true` in your ignored local configuration to
create viewable copies in `data/media-preview`. Set it to `false` (the default)
to disable preview creation and startup backfill. Canonical extensionless files
under the configured `media.root` remain authoritative; previews are only
normal copies for local viewing and are never used for deduplication,
preparation, or publishing.

توقف `ingest` یا `ingest-text` با cancellation/interrupt، subscription، Telegram client،
Session lock و MongoDB clientهای مالکیت‌دار را در ترتیب معکوس می‌بندد. اجرای عادی
هرگز prompt ورود نمایش نمی‌دهد و Session نامعتبر با exit code غیرصفر متوقف می‌شود.

مسیر Config به‌ترتیب از `--config PATH`، سپس `TAB_CONFIG_PATH` و در نهایت
`config/configuration.json` نسبت به working directory انتخاب می‌شود. CLI فقط
مسیر غیرحساس Config را می‌پذیرد؛ URI، Token، Password، API key و Session هرگز
argument خط فرمان نیستند. مقدارهای مستقیم فقط از فایل Local Configِ ignored
resolve می‌شوند و در Log، `repr` یا پیام خطا نمایش داده نمی‌شوند.

قرارداد خروج پایدار است: `0` برای Startup/Shutdown موفق، `2` برای خطای
CLI/Configuration و `3` برای خطای Infrastructure. Eventهای lifecycle به‌صورت
JSON خطی UTF-8 و redacted روی stderr نوشته می‌شوند.

## انتشار فوری و زمان‌بندی بومی Telegram

انتشار مقصد فقط با Telegram User API انجام می‌شود؛ Bot API صرفاً برای تعامل مدیر
است. هویت Publication و Schedule از `Post + Destination + action + version 1`
ساخته می‌شود. claim و unique index در MongoDB مانع ارسال هم‌زمان تکراری‌اند.
خطای قطعی پیش از send با backoff/jitter محدود retry می‌شود؛ نتیجهٔ مبهم پس از send
در `OutcomeUnknown` متوقف می‌ماند و خودکار دوباره ارسال نمی‌شود.

انتشار فوری همچنان از outbox پایدار و due worker پایتون استفاده می‌کند. انتخاب‌های
زمان‌بندی‌شدهٔ جدید در collection مستقل `native_schedule_commands` قرار می‌گیرند و
فقط `runtime` آن‌ها را با همان Telethon client مشترک وارد Telegram Scheduled
Messages می‌کند. زمان هر مقصد برابر
`max(now, latest Telegram scheduled time) + 5 minutes` است؛ بنابراین پیام‌های
زمان‌بندی‌شده‌ای که خارج از برنامه ساخته شده‌اند نیز در Slot بعدی اثر دارند.

فرمان قدیمی زیر عمداً fail-closed است و پیش از بازکردن Session با exit code غیرصفر
پایان می‌یابد؛ jobهای داخلی قدیمی `action=scheduled` نه migrate می‌شوند، نه اجرا و
نه خودکار لغو می‌شوند:

```powershell
uv run python -m telegram_assist_bot schedule-worker --config config/configuration.local.json
```

لغو یا تغییر یک انتخاب بومی ابتدا IDهای Scheduled Message ذخیره‌شده را با درخواست
بومی Telegram حذف می‌کند؛ تغییر به Immediate فقط پس از حذف موفق، job فوری را
می‌سازد. lease مقصد و command نسخه‌دار مانع Slot یا Schedule تکراری است و نتیجهٔ
مبهم پس از شروع درخواست خودکار resend نمی‌شود.

## آماده‌سازی Media و محتوا

runtime فرمان `ingest` همان session/client بازشده را برای validation، History، Listener
و stream رسانه reuse می‌کند و Post، `media_items`، `media_groups` و
`content_preparations` را از یک مسیر idempotent می‌سازد. تنظیمات واقعی از بخش‌های
`media` و `categorization` در فایل نمونه گرفته می‌شوند.

پاک‌سازی محدود و one-shot با policy نگه‌داری تنظیم‌شده از این فرمان اجرا می‌شود:

```powershell
uv run --python 3.12 python -m telegram_assist_bot media-cleanup --config config/configuration.local.json
```

## اجرای عملیاتی تأیید و انتشار

Process اول تنها مالک Session و client مربوط به Telegram User API است و ingestion،
نهایی‌سازی Album و اجرای commandهای انتشار را با همان client انجام می‌دهد:

```powershell
uv run --python 3.12 python -m telegram_assist_bot runtime `
  --config config/configuration.local.json
```

Process دوم فقط Bot API و MongoDB را استفاده می‌کند و تحویل تأیید، `/start`،
callback امن و همگام‌سازی پیام مدیران را اجرا می‌کند:

```powershell
uv run --python 3.12 python -m telegram_assist_bot approval-bot `
  --config config/configuration.local.json
```

`runtime` یک heartbeat امن و پایدار در MongoDB می‌نویسد. کارت کنترل هر پیشنهاد
فعال یا غیرفعال بودن Runtime را کنار وضعیت صف نشان می‌دهد؛ غیرفعال بودن Runtime
job را حذف نمی‌کند و انتشار پس از شروع دوبارهٔ Runtime ادامه می‌یابد. محتوای آماده
ابتدا فرستاده می‌شود و کارت کنترل به همان پیام (برای Album به اولین عضو) reply
می‌شود. کارت، زمان انتشار منبع و زمان دقیق صف را در timezone برنامه نشان می‌دهد و
هر دکمه نام مقصد خودش را دارد.

پیش‌نمایش Media مسیر نسبی ذخیره‌شده را فقط زیر `media.root` resolve می‌کند و
absolute path، traversal، symlink، فایل گمشده و خروج از root را رد می‌کند. Bot API
بر اساس نوع واقعی از `send_photo`، `send_video`، `send_animation` یا
`send_document` استفاده می‌کند. برای Album فقط اولین Photo (یا در نبود آن اولین
عضو واقعی) پیش‌نمایش می‌شود؛ payload نهایی Album همچنان همهٔ اعضا را نگه می‌دارد.
timeout آپلود با `approval_media_upload_timeout_seconds` و lease مؤثر تحویل به‌شکل
جدا و bounded تنظیم می‌شود.

پس از بازشدن client مشترک Telethon، `runtime` اولین heartbeat را فوراً می‌نویسد،
publication worker را با poll مؤثر حداکثر یک ثانیه و live listenerها را فعال می‌کند
و سپس crawl اولیه را در task جداگانه آغاز می‌کند. بنابراین history حجیم، دانلود
Media یا آماده‌سازی Album مانع claim کار فوری/due نمی‌شود. شکست crawl با retry امن
ایزوله می‌ماند؛ شکست heartbeat، publication worker یا client مشترک runtime را با
خطای زیرساختی متوقف می‌کند.

پیش از شروع Runtime می‌توان صف را بدون بارگذاری متن یا Media و بدون اجرای job دید:

```powershell
uv run --python 3.12 python -m telegram_assist_bot publication-queue `
  --config config/configuration.local.json --status pending
```

لغو فقط برای یک شناسهٔ صریح و با policy موجود انجام می‌شود و تکرار آن idempotent است:

```powershell
uv run --python 3.12 python -m telegram_assist_bot publication-cancel `
  --config config/configuration.local.json --job-id <job-id>
```

هیچ‌یک از دو فرمان صف، Telegram User API یا Session را باز نمی‌کند و inspection
هیچ job موجودی را اجرا یا خودکار لغو نمی‌کند.

`telegram.bot.approval_delivery_max_per_startup` (default `10`) برای سازگاری نام
قدیمی را حفظ کرده، اما اکنون اندازهٔ هر batch تاریخی است. پس از ده تحویل موفق،
worker به‌اندازهٔ `approval_delivery_batch_pause_seconds` (default `10`) مکث می‌کند
و batch بعدی را خودکار ادامه می‌دهد تا backlog خالی شود. retry، deferred و failure
سهمیه را مصرف نمی‌کنند و proposalهای پس از watermark همیشه پیش از backlog بررسی
می‌شوند. فاصلهٔ pacing موفق‌ها با
`telegram.bot.approval_delivery_interval_seconds` (default `1`) تنظیم می‌شود.
ترتیب claim بر پایهٔ زمان تلاش بعدی، زمان ایجاد و شناسهٔ پایدار است و پیشرفت هر
مدیر جداگانه نگه‌داری می‌شود.

صف تحویل تأیید را می‌توان بدون بارگذاری متن یا مسیر Media و بدون اجرای Telegram
به‌صورت read-only دید:

```powershell
uv run --python 3.12 python -m telegram_assist_bot approval-queue `
  --config config/configuration.local.json --status retry
```

و فقط یک پیشنهاد دقیق را می‌توان به‌شکل idempotent برای retry صریح آزاد کرد؛
referenceهای موفق مدیران reset نمی‌شوند:

```powershell
uv run --python 3.12 python -m telegram_assist_bot approval-retry `
  --config config/configuration.local.json --approval-post-id <exact-id>
```

Documentهای قدیمی که مشخصاً با `media_rejected` terminal شده‌اند با فرمان محدود
زیر قابل بررسی‌اند. ابتدا حتماً `--dry-run` اجرا شود؛ خروجی فقط شناسهٔ کوتاه دارد
و متن، نام کامل فایل یا مسیر Media را نشان نمی‌دهد:

```powershell
uv run --python 3.12 python -m telegram_assist_bot approval-recover-documents `
  --config config/configuration.local.json `
  --approval-post-id <exact-id> --dry-run --limit 1
```

پس از بررسی، حذف `--dry-run` فقط همان deliveryهای `content_kind=document` با
`failure_category=media_rejected` را requeue می‌کند و تحویل‌های موفق مدیران را
reset نمی‌کند. به‌جای شناسهٔ دقیق می‌توان بازهٔ bounded و aware با
`--from-time <ISO_TIME> --to-time <ISO_TIME>` داد؛ `--limit` بین ۱ و ۱۰۰ اجباری
است.

فرمان‌های `ingest` و `ingest-text` سازگار مانده‌اند، اما نباید هم‌زمان با `runtime`
روی همان `session_path` اجرا شوند. `schedule-worker` دیگر Session باز نمی‌کند و
fail-closed است. `approval-bot` هیچ Session کاربری را باز نمی‌کند.

Media به‌صورت stream و با timeout/سقف حجم زیر root خصوصی (پیش‌فرض نمونه
`var/media`) نوشته می‌شود. temp یکتا فقط پس از تکمیل hash/size به‌طور اتمیک rename
می‌شود و MongoDB فقط metadata و مسیر نسبی امن را نگه می‌دارد، نه bytes فایل.
Cleanup در batch محدود و پس از recheck reference اجرا می‌شود؛ فایل referenced یا
غیرمنقضی، shared hash دارای reference و orphan جوان در grace حذف نمی‌شوند.

نام فایل ورودی مسیر ذخیره را کنترل نمی‌کند و absolute path، traversal و symlink
escape رد می‌شوند. روی POSIX مجوزهای خصوصی best-effort اعمال می‌شوند. `chmod` در
Windows جای ACL نیست؛ root runtime باید زیر حساب کاربری و ACL محافظت‌شده باشد.

Album arrival را پیش از دانلود Media ثبت می‌کند و quiet window را از زمان مشاهده
می‌سنجد، نه تاریخ قدیمی پیام history. finalizer از claim/lease پایدار، anchor
canonical و retry bounded استفاده می‌کند؛ یک گروه ناقص یا malformed فقط state همان
گروه را تغییر می‌دهد و heartbeat، publication و live ingestion را متوقف نمی‌کند.
عضو دیررس پس از finalization به‌صورت قطعی نادیده گرفته می‌شود. تنظیمات اختیاری
`album_finalization_retry_seconds`، `album_finalization_lease_seconds` و
`album_finalization_max_attempts` default امن دارند. exact normalization/hash نسخه `1` حروف فارسی/عربی، ZWNJ،
URL، punctuation و Emoji را تبدیل نمی‌کند. destination-content policy نسخه `1`
با offsetهای UTF-16 کار می‌کند. precedence دسته‌بندی پایه نیز manual override،
سپس keyword rule، سپس default منبع است؛ هیچ AI در این مسیر اجرا نمی‌شود.

برای recovery فقط یک failure قدیمی که به‌طور قطعی پیش از درخواست Telegram و بر
اثر `text_url` فاقد URL رخ داده است، ابتدا Runtime را متوقف و Post ID دقیق را بدهید:

```powershell
uv run --python 3.12 python -m telegram_assist_bot publication-recover-presend `
  --config config/configuration.local.json `
  --approval-post-id <exact-post-id>
```

فرمان در صورت هرگونه outcome اثبات‌نشده fail closed است و job عمومی یا published
را requeue نمی‌کند.

## ساختار اولیه

کد Package زیر `src/telegram_assist_bot/` قرار دارد. زیرPackageهای `domain`،
`application`، `infrastructure`، `presentation`، `workers`، `shared` و
`bootstrap` در T001 importable شدند. رفتار T002 فقط در `shared/config` و قرارداد
pure Domain مربوط به T003 فقط در `domain/posts` قرار دارد.

قرارداد pure Repository مربوط به T004 در `application/ports` و mapper، index
initializer و Adapter ناهمگام MongoDB در `infrastructure/persistence/mongodb`
قرار دارند. Composition Root و CLI در `bootstrap` و `__main__.py` مسیر Foundation
و دریافت متن Milestone 1 را wire می‌کنند. قراردادها و Use Caseهای Telegram در
`application`، Adapterهای Telethon در `infrastructure/telegram/user` و محرک‌های
crawl/listener در `workers` قرار دارند.
مدل‌های Media/duplicate/category در `domain`، use caseها و Portهای آماده‌سازی در
`application`، Storage محلی و repository محتوای MongoDB در `infrastructure` و
محرک‌های cleanup/album/content preparation در `workers` قرار دارند.

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

Session تلگرام فقط زیر `var/sessions/` ساخته می‌شود. Adapter روی POSIX به‌صورت
best-effort مجوز دایرکتوری `0700` و فایل `0600` را اعمال می‌کند. در Windows،
`chmod` معادل ACL کامل نیست؛ بنابراین runtime directory باید زیر حساب کاربری و
ACL محافظت‌شده باشد. lock فایل/فرایند از mutation هم‌زمان جلوگیری می‌کند، اما
جایگزین حفاظت ACL سیستم‌عامل نیست.
