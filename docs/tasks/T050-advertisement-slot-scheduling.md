# T050 — ساخت Slotهای چندزمانه و پایدار

## وضعیت

`Planned`

## هدف

گسترش Campaign فعال به Advertisement Slotهای مستقل برای هر Destination و زمان محلی مصوب، و ذخیره idempotent آن‌ها در MongoDB به‌گونه‌ای که Restart آن‌ها را از بین نبرد؛ بدون اجرای انتشار.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `6.2 زمان‌بندی چندگانه`.
- `docs/REQUIREMENTS.md`، بخش `6.3 جلوگیری از انتشار تکراری تبلیغ` در حد هویت Slot.
- `docs/ARCHITECTURE.md`، بخش `4` (`AdvertisementCampaign` و `AdvertisementSlot`)، بخش `5` (`ExpandAdvertisementSlots`)، بخش `6` (`AdvertisementRepository` و `Clock`)، بخش‌های `9`، `11` و `14`.

## وابستگی‌ها

- `T031`، `T048` و `T049` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید horizon/refill تولید Slot، سیاست missed slot پس از downtime، معنای start/end boundary، رفتار زمان ناموجود/تکراری DST و زمان اثر ویرایش Campaign تصویب و در `docs/DECISIONS.md` ثبت شود. Task نباید این سیاست‌های زمانی را حدس بزند.

## دامنه کار

- تعریف هویت پایدار Slot به شکل `campaign + destination + scheduled instant` با timezone metadata.
- تبدیل days-of-week و چند time روزانه داخل بازه start/end به UTC aware با `Clock`/`ZoneInfo`.
- گسترش bounded مطابق horizon مصوب و upsert idempotent Slotهای مستقل برای همه Destinationهای مجاز.
- ثبت status پایه، due_at، local scheduled value، campaign/source snapshot version و metadata لازم برای اجرای آتی.
- بازیابی/تکمیل horizon پس از Restart طبق policy مصوب.
- Index یکتا و claim-friendly، بدون Claim یا انتشار در این Task.

## خارج از دامنه

- اجرای Slot، Telegram publication و Retry (`T051`).
- حل تداخل با صف عادی (`T052`).
- گزارش مدیران یا تغییر Campaign Config T048.
- Scheduler درون‌حافظه‌ای یا تولید نامحدود آینده.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/advertisement_slot.py`
- `src/telegram_assist_bot/application/advertisements/expand_advertisement_slots.py`
- توسعه `src/telegram_assist_bot/application/ports/advertisement_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/advertisement_repository.py`
- Wiring Clock/expansion trigger در Composition Root یا Worker محدود موجود.
- `tests/unit/application/advertisements/test_expand_advertisement_slots.py`
- `tests/integration/mongodb/test_advertisement_slots.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** timezone، weekdays، times، start/end و horizon باید typed، bounded و Fail-fast باشند؛ local time مبهم نیازمند policy مصوب است.
- **Migration:** Unique Index کلید Slot و Index status/due_at باید idempotent ساخته شود؛ duplicate موجود preflight شود و خودکار حذف نشود.
- **Compatibility:** UTC instant و timezone/local representation هر دو پایدار ذخیره شوند تا تغییر timezone Campaign Slot گذشته را بازتفسیر نکند.
- **Concurrency:** چند expander/Restart با upsert+unique index Slot تکراری نسازند و ویرایش Campaign با version کنترل شود.
- **Security:** فقط Campaign/Destination معتبر Config پردازش و شناسه/خطای حساس redacted شود؛ این Task تماس خارجی ندارد.

## معیارهای پذیرش عینی

1. هر time×day×destination معتبر در horizon دقیقاً یک Slot مستقل می‌سازد.
2. تبدیل timezone به UTC در مرز روز و DST مطابق Decision قطعی است.
3. اجرای دوباره Expander و Restart هیچ Slot duplicate نمی‌سازد.
4. Campaign غیرفعال، خارج از start/end یا Destination نامعتبر Slot نمی‌سازد.
5. Slot به نسخه دقیق Snapshot T049 متصل یا سیاست اتصال آن صریح ثبت می‌شود.
6. هیچ Slot در این Task Claim یا منتشر نمی‌شود.

## تست‌های واحد الزامی

- چند time، چند weekday، چند Destination و بازه start/end.
- timezone/مرز روز و DST مطابق policy ثبت‌شده.
- horizon/refill، missed slot و Campaign غیرفعال.
- key generation و deduplication ورودی‌های تکراری.

## تست‌های یکپارچه‌سازی الزامی

- upsert/Unique Index MongoDB برای expanderهای هم‌زمان.
- Restart و refill horizon بدون duplicate.
- ویرایش Campaign/version در مرز expansion و جلوگیری از overwrite نامعتبر.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/advertisements/test_expand_advertisement_slots.py
uv run pytest tests/integration/mongodb/test_advertisement_slots.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision horizon/DST/missed-slot و boundaryها در `docs/DECISIONS.md`.
- ثبت الگوریتم expansion، identity و Indexها در `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md`.
- به‌روزرسانی example Config زمان‌بندی و سپس `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که Decisionهای زمانی ثبت، expansion چندزمانه/چندمقصدی و Restart با MongoDB واقعی آزمایشی اثبات، UTC/DST درست، Quality Gateها موفق و اجرای Publication خارج از Scope باشد.
