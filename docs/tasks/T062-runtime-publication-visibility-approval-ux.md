# T062 — Runtime publication visibility and approval proposal UX

## Status

Completed

## Goal

شفاف‌کردن مالکیت و دسترس‌پذیری Runtime انتشار، حفظ و نمایش زمان دقیق صف، و تبدیل پیشنهاد تأیید به یک واحد دیداری content-first و restart-safe بدون تغییر محتوای canonical یا الگوریتم‌های انتشار و زمان‌بندی موجود.

## Requirement references

- `docs/REQUIREMENTS.md`: بخش‌های `5.12` تا `5.19`، `13`، `14` و `16`.
- T020–T033، T060 و T061.

## Dependencies

- T020–T033: Completed.
- T060 و T061: Completed.

## Scope

- heartbeat پایدار و امن برای Process عملیاتی `runtime` و تشخیص stale در approval bot.
- حفظ `due_at` آگاه از timezone برای scheduled و زمان ورود به صف برای immediate.
- نمایش صریح queued/publishing/published/failed و فعال/غیرفعال بودن Runtime.
- رویدادهای امن claim، attempt، defer، success، failure و completion انتشار.
- تحویل content-first و control card پاسخ‌داده‌شده با state مرحله‌ای پایدار و resume بدون تکرار فاز ثبت‌شده.
- کارت کوتاه فارسی با metadata منبع، preview، نوع محتوا، شمار رسانه و مقصد.
- label خودشناساننده برای هر دکمهٔ مقصد.
- فرمان read-only مشاهدهٔ صف و فرمان لغو صریح یک job با policy موجود.
- تست‌های واحد، MongoDB و non-live و به‌روزرسانی مستندات عملیاتی.

## Out of scope

- اجرای خودکار، حذف یا تغییر jobهای زندهٔ موجود.
- تماس زنده با Telegram در تست‌ها.
- تغییر متن/Entity/Media canonical آماده‌شده برای انتشار.
- AI، تبلیغات، dashboard، webhook و refactor نامرتبط.
- تغییر یا خواندن `config/configuration.local.json`.

## Expected files and modules

- `src/telegram_assist_bot/application/approvals/services.py`
- `src/telegram_assist_bot/application/operational_approval.py`
- `src/telegram_assist_bot/application/scheduling/run_due_publication.py`
- `src/telegram_assist_bot/application/ports/`
- `src/telegram_assist_bot/domain/admin_approval.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/`
- `src/telegram_assist_bot/infrastructure/telegram/bot/adapter.py`
- `src/telegram_assist_bot/bootstrap/approval_bot.py`
- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- `src/telegram_assist_bot/bootstrap/publication_queue.py`
- `src/telegram_assist_bot/bootstrap/cli.py`
- تست‌ها، README و اسناد project memory مرتبط.

## Implementation notes

- heartbeat فقط `instance_id`، `started_at`، `last_seen_at` و `status` را ذخیره می‌کند.
- زمان‌ها در MongoDB UTC-aware باقی می‌مانند و فقط برای UI به timezone برنامه تبدیل می‌شوند.
- callback فقط job پایدار می‌سازد؛ User API فقط در `runtime` اجرا می‌شود.
- control card به اولین Message محتوای تک‌پیام یا Album reply می‌شود.
- هر فاز persistشده در restart دوباره ارسال نمی‌شود؛ publication identity و lease موجود منبع exactly-once باقی می‌مانند.
- فرمان inspection payload، متن، media path یا secret را بارگذاری/نمایش نمی‌دهد.

## Acceptance criteria

1. heartbeat تازه Runtime را active و heartbeat stale/stopped آن را inactive نشان می‌دهد.
2. immediate و scheduled قبل از شروع Runtime durable می‌مانند و بعداً توسط همان worker موجود claim می‌شوند.
3. UI queued را هرگز published نشان نمی‌دهد و وضعیت availability صریح است.
4. scheduled `due_at` دقیق UTC را حفظ و تاریخ/زمان محلی کامل را با نام مقصد نمایش می‌دهد؛ immediate زمان ورود به صف را نمایش می‌دهد.
5. content پیش از card ارسال و card به اولین Message محتوا reply می‌شود.
6. content IDs و control ID و فاز delivery ماندگارند؛ restart فاز ثبت‌شده را تکرار نمی‌کند.
7. UUID داخلی در کارت عادی نیست و metadata منبع، preview، content type و destination دیده می‌شوند.
8. هر ردیف keyboard متعلق به یک مقصد و هر label شامل نام همان مقصد است.
9. worker رویدادهای امن lifecycle انتشار را بدون payload/error/secret ثبت می‌کند.
10. صف فقط با command read-only فهرست و فقط با job ID صریح و policy موجود لغو می‌شود.
11. job future زود claim نمی‌شود و job due برای یک identity فقط یک بار claim/publish می‌شود.

## Unit tests

- rendering زمان منبع و صف در timezone برنامه و نبود UUID داخلی.
- label مقصد در تمام دکمه‌ها و وضعیت Runtime offline/online.
- content-first/reply برای text/photo/video/animation/album و resume هر فاز.
- immediate/scheduled callback، due time و cancellation idempotent.
- heartbeat runtime wiring و eventهای امن publication worker.
- dispatch امن commandهای inspection/cancellation بدون شروع Runtime.

## Integration tests

- fresh/stale/stopped heartbeat در MongoDB.
- uniqueness صف immediate و claim پس از restart.
- عدم claim job زمان‌بندی‌شدهٔ future و claim یکتای job due.
- persistence metadata منبع، نوع محتوا و شمار media.
- state و referenceهای delivery مرحله‌ای در MongoDB موجود.

## Verification commands

```powershell
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27017/?directConnection=true'
uv run --python 3.12 pytest <focused approval/runtime/publication tests>
uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90 --basetemp <unique>
uv run --python 3.12 ruff check src tests scripts
uv run --python 3.12 ruff format --check src tests scripts
uv run --python 3.12 mypy src tests scripts
uv run --python 3.12 python -m telegram_assist_bot --help
git diff --check
```

همچنین بررسی UTF-8/Persian/mojibake، secret scan، build و distribution validation مطابق `AGENTS.md` الزامی است.

## Documentation updates

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`
- `docs/ROADMAP.md`
- `docs/STATUS.md`
- همین Task با نتیجهٔ verification.

## Definition of done

- همهٔ acceptance criteria و تست‌های متمرکز پاس شده‌اند.
- suite کامل non-live با Python 3.12، MongoDB محلی، صفر mandatory skip و branch coverage حداقل ۹۰٪ پاس شده است.
- lint، format، mypy، UTF-8/mojibake، secret، build، distribution و CLI help پاس شده‌اند.
- هیچ Telegram live call، اجرای job موجود، تغییر config محلی یا عملیات Git بیرونی رخ نداده است.
- T062 Completed و T034 دوباره تنها Task Active است.

## Verification results

- focused unit approval/runtime/publication/bootstrap: `148 passed`؛ تست مستقیم queue: `3 passed`.
- focused MongoDB operational runtime: `6 passed`.
- suite کامل non-live با Python 3.12 و MongoDB محلی: `880 passed`، `0 skipped`، exit code `0` و branch coverage برابر `90.0849%`.
- `ruff check .`، `ruff format --check .`، `mypy src tests scripts`، `uv lock --check` و `git diff --check`: Passed.
- UTF-8/Persian/mojibake برای تمام فایل‌های تغییرکرده و secret scan همان محدوده: Passed.
- build، distribution validation، package import و CLI help: Passed.
- بررسی `--all` متن به‌علت ۷۴۴ artifact موقت pytest که از commit پیشین `5475bec` tracked شده‌اند پاس نمی‌شود؛ فایل‌های T062 سالم‌اند و آن بدهی موجود در این Task حذف نشد.
- هیچ Telegram live call، اجرای job زنده، تغییر config محلی، commit یا push انجام نشد.
