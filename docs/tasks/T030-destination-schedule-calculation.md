# T030 — محاسبه اتمیک صف هر مقصد

## وضعیت

`Planned`

## هدف

محاسبه و ثبت اتمیک Slot انتشار زمان‌بندی‌شده برای هر Destination مستقل، با فاصله Configurable و Clock قابل تست؛ بدون اجرای Job یا لغو آن.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.18 انتشار زمان‌بندی‌شده`، به‌ویژه قانون فاصله و استقلال صف مقصدها.
- `docs/REQUIREMENTS.md`، بخش `4 مدیریت تنظیمات` برای فاصله زمانی و منطقه زمانی.
- `docs/ARCHITECTURE.md`، بخش `4` (`ScheduledPublication`)، بخش `5` (`SchedulePost`)، بخش `6` (`ScheduleRepository` و `Clock`)، بخش‌های `9`، `11` و `14`.

## وابستگی‌ها

- `T002`، `T004` و `T024` باید کامل شده باشند.

## دامنه کار

- تعریف مدل/Command/Result ایجاد Schedule برای یک `Post × Destination`.
- محاسبه `now + interval` وقتی صف فعال خالی است و `last_due_at + interval` در غیر این صورت.
- نگهداری زمان‌های aware به UTC و استفاده از ZoneInfo فقط در مرز نمایش/Config.
- رزرو اتمیک Slot در `ScheduleRepository` برای جلوگیری از Slot یکسان در درخواست‌های هم‌زمان.
- ثبت Job پایدار با idempotency key، due_at، status و metadata پایه تلاش.
- حفظ صف مستقل هر Destination و رد State/Permission/انتخاب نامعتبر.

## خارج از دامنه

- Claim/اجرای Job و انتشار (`T031`).
- لغو و Recompaction (`T032`).
- تغییر Toggle یا همگام‌سازی پیام مدیران.
- Scheduler درون‌حافظه‌ای یا Broker جدید.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/scheduled_publication.py`
- `src/telegram_assist_bot/application/scheduling/schedule_post.py`
- `src/telegram_assist_bot/application/ports/schedule_repository.py`
- `src/telegram_assist_bot/application/ports/clock.py`
- `src/telegram_assist_bot/infrastructure/mongodb/schedule_repository.py`
- توسعه Configuration فاصله فقط طبق قرارداد T002.
- `tests/unit/application/scheduling/test_schedule_calculation.py`
- `tests/integration/mongodb/test_atomic_schedule_slot.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** interval باید مثبت، bounded و به واحد صریح باشد؛ timezone نامعتبر در Startup رد شود.
- **Migration:** Indexهای destination/status/due_at و unique key باید idempotent ساخته و اسناد قدیمی با default روشن مدیریت شوند.
- **Compatibility:** timestampهای ذخیره‌شده UTC و field nameها پایدار باشند؛ تغییر واحد Config نیازمند migration/version است.
- **Concurrency:** خواندن آخرین Slot و درج بعدی باید یک رزرو اتمیک/sequence per destination باشد؛ Transaction process-local کافی نیست.
- **Security:** Actor/Destination server-side اعتبارسنجی و شناسه/خطای حساس از Log حذف شود؛ این Task Secret جدید ندارد.

## معیارهای پذیرش عینی

1. صف خالی Slot را دقیقاً یک interval پس از Clock و صف غیرخالی یک interval پس از آخرین Slot فعال می‌سازد.
2. صف Destinationهای مختلف مستقل است.
3. درخواست‌های هم‌زمان برای یک Destination Slotهای یکتا و مرتب می‌گیرند.
4. همان درخواست idempotency key یک Job دوم نمی‌سازد.
5. همه زمان‌ها در Persistence به UTC aware ذخیره می‌شوند.
6. هیچ Job اجرا یا لغو نمی‌شود.

## تست‌های واحد الزامی

- صف خالی/غیرخالی با Fake Clock و intervalهای Configurable.
- استقلال دو Destination و مرز تغییر روز/Timezone.
- رد interval نامعتبر، State نامعتبر و مقصد غیرمجاز.
- ساخت idempotency key و نتیجه AlreadyScheduled.

## تست‌های یکپارچه‌سازی الزامی

- رزرو هم‌زمان چند Slot MongoDB برای یک Destination و اثبات ترتیب/یکتایی.
- رزرو هم‌زمان برای دو Destination و اثبات استقلال.
- ساخت Indexها و idempotency روی درخواست تکراری.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/scheduling/test_schedule_calculation.py
uv run pytest tests/integration/mongodb/test_atomic_schedule_slot.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت الگوریتم رزرو Slot، Indexها و UTC policy در `docs/ARCHITECTURE.md`.
- افزودن مدل/Use Case/Repository به `docs/CODE_MAP.md`.
- به‌روزرسانی نمونه Config در صورت تغییر کلید interval و سپس `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که محاسبه با Clock قطعی و رزرو هم‌زمان MongoDB اثبات، زمان‌ها UTC، Config معتبر، همه Quality Gateها موفق و Worker/لغو خارج از Scope باشد.
