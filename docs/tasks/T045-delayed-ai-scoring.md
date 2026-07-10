# T045 — امتیازدهی تأخیری و ویرایش هدر مدیران

## وضعیت

Planned

## هدف

زمان‌بندی پایدار امتیازدهی AI پس از Delay تنظیم‌شده از زمان انتشار مبدا، ذخیره نتیجه و بروزرسانی best-effort هدر همه پیام‌های مدیران بدون حذف Keyboard یا تغییر انتخاب مقصدها.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.20` «امتیازدهی هوش مصنوعی».
- `docs/REQUIREMENTS.md`، بخش `5.12` فقط جداسازی هدر مدیر از محتوای قابل انتشار.
- `docs/REQUIREMENTS.md`، بخش `11.12` برای شکست امتیازدهی.
- `docs/ARCHITECTURE.md`، بخش‌های `5`، `8`، `11`، `12`، `14` و `17` بند `4`.

## وابستگی‌ها

- T022 — هدر و محتوای پیام تأیید.
- T025 — همگام‌سازی پیام تمام مدیران.
- T035 — صف AI پایدار، اولویت و Lease.
- T039 — Routing، Retry، Fallback و شکست نهایی.

تعارض `minimum AI score` با امکان ارسال زودتر برای تأیید در معماری باز است. این Task فقط امتیازدهی تأخیری و نمایش نتیجه را پیاده می‌کند و نباید بی‌صدا انتشار/تأیید را به امتیاز وابسته کند؛ هر Gate جدید نیازمند تصمیم محصولی جداست.

## دامنه

- محاسبه `due_at = source_published_at + configured_delay` با Clock/Timezone قرارداد موجود.
- enqueue پایدار و یکتای Job scoring با `next_run_at` مناسب.
- mapping نتیجه معتبر شامل score صفر تا صد و اجزای مصوب Schema.
- ذخیره نتیجه حتی اگر Post پیش‌تر منتشر شده باشد.
- بازسازی هدر از state تازه و fan-out ویرایش پیام‌های Approval موجود به‌صورت best-effort.
- حفظ Keyboard/Callback و DestinationSelectionها عیناً.
- ثبت Retry مستقل برای failure ویرایش هر پیام طبق زیرساخت موجود.

## خارج از دامنه

- جلوگیری از تأیید/انتشار بر اساس minimum score تا تعیین تصمیم محصول.
- ویرایش پیام منتشرشده در کانال مقصد.
- ساخت Schema/Prompt یا Provider جدید.
- طراحی UX جدید Approval و تغییر Keyboard.
- تحلیل عملکرد واقعی پست‌های منتشرشده.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/use_cases/schedule_ai_scoring.py`
- `src/telegram_assist_bot/application/use_cases/apply_ai_score.py`
- `src/telegram_assist_bot/application/ai/task_handlers/scoring.py`
- تغییر محدود در renderer/synchronizer موجود زیر `src/telegram_assist_bot/presentation/telegram_bot/`
- تغییر محدود در Worker AI موجود زیر `src/telegram_assist_bot/workers/`
- `tests/unit/application/test_delayed_ai_scoring.py`
- `tests/unit/application/test_apply_ai_score.py`
- `tests/integration/workflows/test_delayed_ai_scoring.py`

## نکات پیاده‌سازی

- **Configuration:** delay حداقل از Config معتبر خوانده شود؛ مقدار پیش‌فرض/حداقل جدید خارج از Requirement اختراع نشود و ZoneInfo معتبر باشد.
- **Migration:** فیلدهای due/result/attempt در Schema موجود فقط با Migration سازگار افزوده شوند؛ Index صف T035 دوباره ساخته نشود مگر نیاز اثبات‌شده.
- **Compatibility:** Header renderer و callback markup موجود reuse شوند؛ قرارداد پیام/Callback تغییر نکند.
- **Concurrency:** Job scoring یکتا و completion idempotent باشد؛ ویرایش یک مدیر شکست سایرین را rollback نکند و state selection snapshot نشود.
- **Security:** خطای Provider برای مدیر Sanitized باشد؛ Admin ID، token و متن Session در Log ممنوع است.
- **زمان:** زمان Domain UTC-aware و تبدیل فقط در مرز نمایش انجام شود؛ DST/Timezone با Clock ثابت تست شود.

## معیارهای پذیرش عینی

1. Job پیش از `due_at` Claim/اجرا نمی‌شود و در/پس از مرز اجراپذیر است.
2. Restart Job را از بین نمی‌برد و enqueue تکراری Job دوم نمی‌سازد.
3. نتیجه معتبر صفر تا صد با Metadata AI ذخیره می‌شود.
4. نتیجه پس از Publication نیز ذخیره می‌شود اما پیام مقصد ویرایش نمی‌شود.
5. هدر تمام Approval referenceهای معتبر بروزرسانی و Keyboard/selection بدون تغییر حفظ می‌شوند.
6. شکست ویرایش یک Reference ثبت می‌شود و ویرایش بقیه ادامه دارد.
7. شکست همه Providerها «امتیاز در دسترس نیست»/Retry آینده را فقط طبق policy مصوب بازتاب می‌دهد.
8. هیچ Gate انتشار بر مبنای score بدون تصمیم مستند اضافه نمی‌شود.

## Unit Testهای الزامی

- مرز due time، delay قابل تنظیم و زمان UTC/محلی.
- scoreهای 0، 100 و خارج محدوده.
- Post منتشرشده، منقضی و completion تکراری.
- renderer هدر با pending/success/unavailable و حفظ markup.
- fan-out partial failure و عدم تغییر DestinationSelection.
- enqueue idempotent.

## Integration Testهای الزامی

- بازیابی Job scoring بعد از Restart با MongoDB و Clock Fake.
- اجرای Provider Fake، ذخیره score و ویرایش چند Approval با یک failure تزریق‌شده.
- رقابت دو Worker و یک completion/یک fan-out منطقی.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_delayed_ai_scoring.py tests/unit/application/test_apply_ai_score.py
uv run pytest tests/integration/workflows/test_delayed_ai_scoring.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

هدرهای فارسی، Keyboard و Diff انسانی باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- بروزرسانی همین Task، `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- همگام‌سازی timing/fan-out واقعی در `docs/ARCHITECTURE.md`.
- تصمیم درباره minimum score و اثر آن بر Approval/Publication باید جداگانه در `docs/DECISIONS.md` یا Requirement ثبت شود؛ این Task آن را حل نمی‌کند.

## تعریف Done

- زمان‌بندی، restart، idempotency و fan-out با تست‌های الزامی اثبات شده‌اند.
- Keyboard/selection و محتوای مقصد دست‌نخورده‌اند.
- ابهام score gate بی‌صدا حل نشده و همه Quality Gateها و بازبینی Persian پاس شده‌اند.
