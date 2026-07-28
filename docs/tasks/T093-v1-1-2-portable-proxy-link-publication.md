# T093 — v1.1.2 Portable Proxy Link Publication

## وضعیت

Completed

## هدف

جلوگیری از گم‌شدن URLهای دکمه‌های قابل‌انتقال منبع هنگام انتشار نهایی با
Telegram User API و آماده‌سازی patch release نسخهٔ `1.1.2`.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `5.10`، `5.17` تا `5.19` و `18`.

## وابستگی‌ها

- T077.
- T092.

## محدوده

- تبدیل قطعی URLهای ذخیره‌شدهٔ `TelegramUrlButton` به fallback متنی در مرز
  Publisher و Native Scheduler مبتنی بر User API.
- حفظ ترتیب ردیف‌ها، ترتیب دکمه‌ها، label و URL بدون normalization.
- جلوگیری از ارسال keyboard غیرقابل‌پشتیبانی توسط User session.
- fail شدن پیش از تماس Telegram اگر fallback از حد پیام یا Caption عبور کند.
- حفظ persistence افزایشی T077 و سازگاری Postهای فاقد keyboard.
- همگام‌سازی سطوح فعال Package، Image، Installer و Release با نسخهٔ `1.1.2`.

## خارج از محدوده

- اجرای callback منبع یا تبدیل callback data به URL حدسی.
- تغییر مسئولیت User API و Bot API یا انتقال انتشار نهایی به Bot.
- refetch یا migration نامحدود Postهای legacy فاقد `inline_keyboard`.
- تغییر Post TTL، Duplicate window، Media retention یا Approval cleanup.
- Push، Tag، GitHub Release یا انتشار GHCR.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/infrastructure/telegram/user_publisher.py`
- `src/telegram_assist_bot/infrastructure/telegram/native_scheduler.py`
- تست‌های Adapter انتشار فوری و Native Schedule
- Surfaceهای نسخه و Release packaging
- مستندات معماری، عملیات و انتشار

## نکات پیاده‌سازی

- Telegram User API برای حفظ Premium/Custom Emoji مرز انتشار نهایی باقی
  می‌ماند، اما keyboard فقط توسط Bot account قابل ارسال است.
- fallback پس از آماده‌سازی متن مقصد و در انتهای متن افزوده می‌شود؛ بنابراین
  offset و length Entityهای موجود تغییر نمی‌کنند.
- هر دکمه در یک خط `label: URL` نمایش داده می‌شود و URL دقیق منبع باقی می‌ماند.
- Payloadهای legacy با keyboard خالی بدون تغییر منتشر می‌شوند.

## معیارهای پذیرش عینی

1. دو دکمهٔ proxy منبع به دو لینک قابل‌کلیک و مرتب در متن مقصد تبدیل شوند.
2. متن، Entityها، فارسی، ZWNJ، Emoji، label و URL تغییر نکنند.
3. Publisher فوری و Native Scheduler هیچ `buttons` به User client ندهند.
4. callback و انواع غیر URL همچنان وارد مدل Publication نشوند.
5. عبور از حد 4096 پیام یا 1024 Caption پیش از Telegram با خطای دائمی رخ دهد.
6. نسخهٔ فعال Package، Image، Installer، Bundle و مستندات `1.1.2` باشد.

## Unit Testهای الزامی

- ترتیب دو URL و label در fallback متنی.
- متن/Entity بدون keyboard بدون تغییر.
- boundary دقیق UTF-16 برای پیام و Caption و رد overflow.
- قرارداد عدم ارسال `buttons` در Publisher و Native Scheduler.
- قرارداد نسخهٔ `1.1.2`.

## Integration Testهای الزامی

- مسیر Mongo payload تا Publisher fake با دو URL واقعی `t.me/proxy`.
- انتشار متنی، Media و Native Schedule بدون تماس Telegram واقعی.
- سازگاری رکورد legacy فاقد keyboard.

## فرمان‌های راستی‌آزمایی

```powershell
uv lock --check
uv run pytest tests/unit/infrastructure/telegram/user/test_history_mapper.py tests/integration/telegram tests/integration/mongodb/test_publication_payload_loader.py -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- ROADMAP، STATUS، REQUIREMENTS، ARCHITECTURE، CODE_MAP، DECISIONS، README و
  RELEASE_CHECKLIST.

## نتیجهٔ راستی‌آزمایی

- تست‌های متمرکز mapper، Mongo payload، Publisher فوری، Media/Album، Native
  Scheduler، version و distribution برابر `71 passed` هستند.
- کل Suite غیرزنده با MongoDB loopback برابر `1863 passed` و branch coverage
  برابر `90.16%` است.
- `uv lock --check`، Ruff، format، mypy، text integrity changed/all،
  detect-secrets، Build wheel/sdist نسخهٔ `1.1.2`، distribution check،
  clean-wheel import، PowerShell syntax و `git diff --check` موفق‌اند.
- Ubuntu Quality Run
  [`30398566783`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30398566783)
  روی Commit `837f6837f6df022c7937696c18945ef28cbd70b1` با نتیجهٔ `success`
  کامل شد؛ Python `3.12`، `3.13` و `3.14` و Job
  `Docker and installer acceptance` همگی موفق بودند.
- محدودیت نبود Bash و Docker روی Host محلی با اجرای موفق Bash، Docker و
  Installer acceptance در Ubuntu Quality CI رفع و راستی‌آزمایی شد.
- Telegram live test اجرا نشده است؛ مسیرهای Telegram با Fakeهای non-live
  پوشش داده شدند.
- هیچ Tag، GitHub Release یا انتشار GHCR اجرا نشده است.

## تعریف انجام‌شدن

fallback متنی URL در هر دو مسیر User API با تست معنادار اثبات، تمام Gateهای
قابل‌اجرا موفق، محدودیت محیطی صریح، اسناد همگام و هیچ اثر خارجی Release ایجاد
نشده باشد.
