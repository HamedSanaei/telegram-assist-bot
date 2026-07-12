# T015 — تجمیع Album و Media Group

## وضعیت

Completed

## هدف

تجمیع restart-safe و idempotent اعضای Telegram Media Group به یک Post منطقی با ترتیب درست و یک بار readiness، بدون ارسال به مدیر یا انتشار Album.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.6 مدیریت آلبوم‌ها`.
- `docs/REQUIREMENTS.md`، بخش `5.3 جلوگیری از پردازش تکراری`، رویداد/Worker تکراری.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، پردازش Media Group.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، `AssembleMediaGroup`.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`، Media Group.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`.

## وابستگی‌ها

- T011 — هم‌زمانی Crawl/Listener و Idempotency؛ باید Completed باشد.
- T013 — دانلود و ذخیره انواع Media؛ باید Completed باشد.

## محدوده

- تعریف identity گروه بر پایهٔ Source canonical + Telegram media group ID.
- ذخیرهٔ اتمیک هر عضو با message ID، order/date، caption/entities و Media metadata؛ عضو تکراری دوباره اضافه نشود.
- Use Case تجمیع و Worker finalize پس از quiet/debounce window محدود و قابل‌تنظیم.
- انتخاب deterministic متن/Caption مطابق semantics مستند Telegram fixtureها و حفظ original هر عضو در metadata لازم.
- مرتب‌سازی deterministic اعضا و تبدیل گروه به یک Post منطقی/aggregate reference.
- finalize اتمیک با expected status/version؛ فقط یک worker readiness را اعلام کند.
- بازیابی گروه نیمه‌کاره پس از Restart و امکان late member طبق سیاست صریح پیش از/پس از finalize.

## خارج از محدوده

- انتشار Album یا ارسال آن برای مدیر؛ T028/T022.
- دانلود behavior جدید؛ از T013 استفاده می‌شود.
- edit/delete عضو گروه، آلبوم‌های cross-channel یا merge دستی.
- AI، duplicate معنایی و text pruning.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/media/groups.py`
- `src/telegram_assist_bot/application/assemble_media_group.py`
- Port/Adapter محدود persistence برای aggregate/atomic finalize.
- `src/telegram_assist_bot/workers/media_group_assembler.py`
- `tests/unit/application/test_assemble_media_group.py`
- `tests/unit/workers/test_media_group_assembler.py`
- `tests/integration/test_media_group_aggregation.py`

## نکات پیاده‌سازی

- timer حافظه‌ای منبع حقیقت نیست؛ `last_member_at/finalize_after/status` در MongoDB بماند.
- تعداد واقعی اعضا از Telegram همیشه از ابتدا معلوم نیست؛ completeness بر quiet window است و ambiguity late member باید در Task تصمیم/مستند شود.
- **ریسک Configuration:** quiet window و max wait bounded؛ تغییر بعدی روی گروه‌های موجود semantics دارد و باید compatibility مستند شود.
- **ریسک Migration:** collection/document/index identity گروه صریح و unique است؛ schema تغییر کند version لازم است.
- **ریسک Compatibility:** ordering/caption semantics SDK با fixture contract تثبیت شود.
- **ریسک Concurrency:** عضو تکراری، arrival خارج ترتیب، دو finalizer و late member با atomic update تست شوند.
- **ریسک Security:** filename/payload Log نشود و MediaStorage containment T013 حفظ شود.

## معیارهای پذیرش عینی

1. اعضای خارج ترتیب به ترتیب deterministic صحیح در یک aggregate قرار می‌گیرند.
2. replay عضو document/Media دوم نمی‌سازد.
3. قبل از quiet window finalize رخ نمی‌دهد و پس از آن دقیقاً یک readiness ثبت می‌شود.
4. دو Worker هم‌زمان فقط یک finalizer موفق دارند.
5. Restart گروه نیمه‌کاره را بازیابی و finalize می‌کند.
6. policy late member و انتخاب caption صریح، تست‌شده و مستند است.
7. پیام standalone همچنان یک Post مستقل باقی می‌ماند.

## Unit Testهای الزامی

- identity، ordering، duplicate member و caption/entity selection.
- quiet window boundary، max wait و Clock ثابت.
- late member قبل/بعد finalize طبق policy.
- single readiness و conflict دو finalizer.
- Persian caption/Custom Emoji metadata بدون تغییر.

## Integration Testهای الزامی

- eventهای out-of-order/replayed + MongoDB و filesystem آزمایشی.
- crash/restart پیش از finalize.
- دو assembler هم‌زمان و یک aggregate/readiness.
- late member race و نبود orphan/duplicate Media reference.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_assemble_media_group.py tests/unit/workers/test_media_group_assembler.py
uv run pytest tests/integration/test_media_group_aggregation.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

تست race/restart نباید skip شود؛ بازبینی دستی caption فارسی، Persian diff و `git diff --check` الزامی است.

## نتایج نهایی راستی‌آزمایی

- فرمان واقعی متمرکز: `uv run pytest tests/unit/application/test_assemble_media_group.py --basetemp .pytest-tmp/m2-t015-final-20260712-100830-998 -q`؛ نتیجه `1 passed` و `0 skipped` بود. Integration واقعی MongoDB نیز در اجرای `m2-focused-final-20260712-100724-136` برابر `1 passed` بود.
- replay/out-of-order، ترتیب قطعی، deadline پایدار، restart، finalizer واحد، late-member و caption/Custom Emoji پاس شدند.
- Suite نهایی دو بار `702 passed` و `0 skipped`؛ Branch Coverage برابر `90.17%` است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T015 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن aggregate/worker/persistence flow به `docs/CODE_MAP.md`.
- ثبت identity، quiet window، ordering و late-member policy در `docs/ARCHITECTURE.md`.
- تصمیم مهم late-member/caption در `docs/DECISIONS.md` ثبت شود.
- Config نمونه برای window/max wait به‌روز شود.

## تعریف انجام‌شدن

- replay/out-of-order/restart/concurrency با Integration Test پاس شده‌اند.
- یک گروه یک Post منطقی و یک readiness دارد.
- Quality Gate، UTF-8 و storage safety پاس شده‌اند.
- ارسال/انتشار Album وارد Task نشده است.
