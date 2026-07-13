# T061 — Operational approval bot and publication orchestration

## Status

Completed

## Goal

اتصال عملیاتی آماده‌سازی محتوا، تحویل تأیید به مدیران، callback امن، ایجاد کار پایدار انتشار فوری یا زمان‌بندی‌شده، اجرای انتشار با Telegram User API و همگام‌سازی نتیجه با تمام پیام‌های تأیید؛ به‌گونه‌ای که restart یا اجرای هم‌زمان باعث از دست‌رفتن یا تکرار کار نشود.

## Requirement references

- `docs/REQUIREMENTS.md`: بخش‌های `5.12` تا `5.19`، `13`، `14` و `16`.
- `docs/ARCHITECTURE.md`: مرز Bot API/User API، approval، publication، scheduling، worker و testing.

## Dependencies

- T020–T033: Completed.
- T060: Completed.

## Scope

- افزودن فرمان `approval-bot` با long polling، `/start` و callbackهای واقعی Aiogram.
- outbox/claim/lease پایدار برای تحویل هر Post آماده به تمام مدیران مجاز و resume تحویل ناقص.
- استفاده از token، authorization، toggle، keyboard و sync موجود.
- تبدیل انتخاب فوری به کار پایدار due-now و انتخاب زمان‌بندی‌شده به رکورد صف موجود.
- استفاده از cancellation/recompaction موجود در deselection.
- retry پایدار همگام‌سازی پیام‌ها و نمایش وضعیت نهایی هر مقصد.
- افزودن فرمان یکپارچه `runtime` برای ingestion، آماده‌سازی media/album و اجرای publication با یک مالک Telethon.
- تنظیمات typed و غیرمحرمانه با default امن، رویدادهای ساخت‌یافته، تست و مستندات عملیاتی.

## Out of scope

- T034–T046 و هر قابلیت AI، تشخیص تبلیغ، تشخیص تکرار معنایی و دسته‌بندی AI.
- کمپین تبلیغاتی، dashboard، webhook deployment و منو یا گزارش مدیریتی جدید.
- تغییر قراردادهای Milestone 3/4 یا refactor نامرتبط.
- تماس زنده با Telegram در تست خودکار.

## Expected files and modules

- `src/telegram_assist_bot/application/operational_approval.py`
- `src/telegram_assist_bot/application/ports/operational_approval.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/operational_approval_repository.py`
- `src/telegram_assist_bot/presentation/bot/runtime_handlers.py`
- `src/telegram_assist_bot/bootstrap/approval_bot.py`
- `src/telegram_assist_bot/bootstrap/operational_runtime.py`
- bootstrap، configuration، repository و exportهای موجود در صورت نیاز.
- تست‌های unit، integration و non-live E2E مرتبط.
- README و اسناد project memory.

## Implementation notes

- Bot runtime نباید Telethon client/session بسازد یا باز کند.
- unified runtime باید publisher را از همان Telethon client بازشده توسط ingestion بسازد.
- کلید پایدار تحویل logical برابر Post و هر reference برابر Post+Admin است.
- callback فقط پس از persistence موفق acknowledge شود؛ خطاهای ردشده هیچ side effect دامنه‌ای ندارند.
- publication نتیجه‌دار است؛ شکست edit نباید انتشار موفق را rollback کند و باید retry پایدار بسازد.
- هیچ secret، raw Update، session content یا media reference در log ثبت نمی‌شود.

## Acceptance criteria

1. هر Post آماده یک تحویل منطقی و برای هر مدیر حداکثر یک reference فعال دارد.
2. claim/lease هم‌زمان و restart-safe است و پیشرفت هر مدیر مستقل ماندگار می‌شود.
3. `/start` مجاز پیام کوتاه فارسی و غیرمجاز پاسخ عمومی denial می‌گیرد.
4. callback جعلی، منقضی، replayشده، غیرمجاز یا مقصد غیرمجاز side effect ندارد.
5. انتخاب canonical با CAS موجود انجام و تمام keyboardها نهایتاً همگرا می‌شوند.
6. فوری دقیقاً یک کار due-now و scheduled دقیقاً یک slot مرتب می‌سازد.
7. deselection از policy لغو/recompaction T032 استفاده می‌کند.
8. publication text/photo/album از payload loader و publisher موجود عبور می‌کند.
9. موفقیت، لغو یا خطای نهایی به state approval و همه پیام‌ها منتقل و retry می‌شود.
10. `runtime` تنها مالک User API است؛ `approval-bot` فقط Bot API+MongoDB است.
11. shutdown منابع Bot، MongoDB و User API را دقیقاً یک بار می‌بندد.
12. فرمان‌های قبلی سازگار می‌مانند و مستندات فرمان واقعی را نشان می‌دهند.

## Unit tests

- `/start` مجاز/غیرمجاز و عدم افشای اطلاعات.
- callback معتبر، forged، expired، replay، permission و idempotency.
- immediate/scheduled/deselection orchestration و rendering وضعیت نهایی.
- log امن و اثبات اینکه handler Bot، User API را باز نمی‌کند.
- shutdown idempotent و ownership یک session.

## Integration tests

- claim یکتا، دو worker، resume تحویل ناقص و persistence referenceها در MongoDB محلی.
- همگرایی چند مدیر و CAS هم‌زمان.
- uniqueness و ordering کار فوری/زمان‌بندی‌شده.
- retry edit، sync نتیجه publication و cancellation/recompaction از مسیر Bot.
- E2E غیرزندهٔ text، photo، album، چند مدیر، restart، callback تکراری، کاربر غیرمجاز و shutdown.

## Verification commands

```powershell
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27017/?directConnection=true'
uv run --python 3.12 pytest <focused approval/runtime/publication/scheduling tests>
uv run --python 3.12 pytest --basetemp .pytest-tmp/t061-<unique> --cov=telegram_assist_bot --cov-branch --cov-report=term-missing
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv run --python 3.12 python -m telegram_assist_bot --help
uv build
uv run --python 3.12 twine check dist/*
git diff --check
```

همچنین scan secret، بررسی UTF-8/Persian و جست‌وجوی markerهای mojibake مطابق `AGENTS.md` الزامی است؛ MongoDB اجباری نباید skip شود.

## Documentation updates

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`
- `docs/DECISIONS.md` برای تصمیم مالکیت session
- `docs/ROADMAP.md`
- `docs/STATUS.md`
- همین Task با نتیجهٔ verification
- `config/configuration.example.json` در صورت افزودن key.

## Definition of done

- تمام acceptance criteria و تست‌های الزامی پاس شده‌اند.
- suite کامل غیرزنده با coverage و MongoDB بدون skip اجباری پاس شده است.
- lint، format، mypy، build، distribution، CLI help، secret و UTF-8 checks پاس شده‌اند.
- هیچ تماس زنده، secret، session یا تغییر کانفیگ محلی رخ نداده است.
- T061 در Task/Roadmap Completed و T034 تنها Task Active شده است.
- diff نهایی فقط تغییرات مرتبط دارد و پیام commit مناسب پیشنهاد شده است.

## Verification results

- Full non-live suite روی Python 3.12 و MongoDB محلی: `863 passed`، `0 skipped` و branch coverage برابر `90.02%`.
- focused approval/publication/scheduling/runtime suites: Passed.
- Ruff، format check، mypy، `uv lock --check` و CLI help: Passed.
- UTF-8/Persian/mojibake checks در حالت changed و all: Passed.
- detect-secrets برای فایل‌های tracked و untracked غیرignored: Passed.
- build، distribution validation، package import و `git diff --check`: Passed.
- تماس زنده با Telegram، خواندن Session واقعی و تغییر `configuration.local.json`: انجام نشد.
