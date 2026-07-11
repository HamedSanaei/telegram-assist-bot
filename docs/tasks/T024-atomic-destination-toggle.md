# T024 — Toggle اتمیک حالت مقصد

## وضعیت

`Planned`

## هدف

پیاده‌سازی تغییر اتمیک وضعیت `Post × Destination` میان انتخاب‌نشده، فوری و زمان‌بندی‌شده، با کنترل نسخه و قوانین مجوز؛ بدون اجرای انتشار یا ساخت Job زمان‌بندی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.15 رفتار Toggle دکمه‌ها`.
- `docs/REQUIREMENTS.md`، بخش `5.13 مدیران مجاز` برای کنترل Actor و Destination.
- `docs/ARCHITECTURE.md`، بخش `4` (`DestinationSelection`)، بخش `5` (`ToggleDestinationSelection`)، بخش `6` (`Atomic Ports`)، بخش `9` و بخش `14` (`Concurrency`).
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام `2`.

## وابستگی‌ها

- `T003`، `T004` و `T023` باید کامل شده باشند.
- Decision معنای «فوری» و Confirm در `T023` باید ثبت شده باشد؛ اگر ثبت نشده است این Task Blocked است.

## دامنه کار

- تثبیت State machine مستقل هر Destination مطابق Decision مصوب.
- تعریف Command/Result صریح `ToggleDestinationSelection` با Actor، Post، Destination، requested mode و expected version.
- اعتبارسنجی Admin، وضعیت Post، مقصد مجاز و عدم Terminal بودن Publication پیش از Transition.
- پیاده‌سازی Atomic compare-and-set در MongoDB با نسخه و نتیجه Conflict صریح.
- تضمین mutual exclusivity حالت فوری و زمان‌بندی برای یک Destination.
- ثبت Actor، transition، زمان و correlation ID بدون اطلاعات حساس.

## خارج از دامنه

- فراخوانی User API، انتشار متن/Media یا Retry انتشار.
- محاسبه Slot یا ایجاد/لغو Schedule Job.
- fan-out و ویرایش پیام سایر مدیران (`T025`).
- تغییر UX/Callback قرارداد مصوب Taskهای قبلی.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/destination_selection.py`
- `src/telegram_assist_bot/application/approvals/toggle_destination_selection.py`
- توسعه Port مناسب در `src/telegram_assist_bot/application/ports/post_repository.py` یا Port اتمیک اختصاصی.
- توسعه MongoDB adapter/Schema و Indexهای مرتبط.
- `tests/unit/domain/test_destination_selection.py`
- `tests/unit/application/approvals/test_toggle_destination_selection.py`
- `tests/integration/mongodb/test_atomic_destination_toggle.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** modeهای مجاز از Contract ثابت Domain می‌آیند؛ Destination و Permission از Config معتبر T002 و Snapshot جاری خوانده شوند.
- **Migration:** افزودن selection/version باید با اسناد قدیمی سازگار، default صریح و Migration/Backfill امن داشته باشد؛ نام فیلد عمومی بدون طرح مهاجرت عوض نشود.
- **Compatibility:** Callback نسخه‌دار T021/T023 به Command داخلی Map شود و State label بیرونی از Enum ذخیره‌شده جدا بماند.
- **Concurrency:** check و write باید یک عملیات شرطی باشد؛ Conflict نتیجه مورد انتظار است و آخرین State دیتابیس دوباره خوانده می‌شود.
- **Security:** Actor و Destination مجدداً server-side اعتبارسنجی شوند؛ اعتماد به claims دکمه یا وضعیت client ممنوع است.

## معیارهای پذیرش عینی

1. کلیک مطابق قرارداد، حالت none/immediate/scheduled را Toggle می‌کند.
2. یک Destination هرگز هم‌زمان فوری و زمان‌بندی‌شده نیست.
3. Transition روی Post/Publication Terminal، Admin نامعتبر یا Destination غیرمجاز رد می‌شود.
4. دو درخواست با expected version یکسان فقط یک تغییر موفق دارند و دیگری Conflict صریح می‌گیرد.
5. نتیجه Use Case State و version قطعی جدید را برمی‌گرداند.
6. هیچ تماس Telegram User API یا ایجاد Job در این Task رخ نمی‌دهد.

## تست‌های واحد الزامی

- جدول کامل Transitionهای مجاز و Toggle معکوس.
- جایگزینی scheduled با immediate و برعکس مطابق Decision.
- رد Terminal state، Permission نامعتبر و mode ناشناخته.
- نگاشت Conflict و idempotent/no-opهای مصوب.

## تست‌های یکپارچه‌سازی الزامی

- compare-and-set واقعی MongoDB و افزایش version.
- اجرای هم‌زمان حداقل دو Toggle متعارض و اثبات یک State نهایی معتبر.
- سازگاری خواندن سند قدیمی/Backfill طبق طرح Migration.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/domain/test_destination_selection.py tests/unit/application/approvals/test_toggle_destination_selection.py
uv run pytest tests/integration/mongodb/test_atomic_destination_toggle.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت State machine و CAS/version در `docs/ARCHITECTURE.md` و مسیرها در `docs/CODE_MAP.md`.
- ثبت Decision فقط اگر قرارداد Toggle نسبت به تصمیم قبلی تغییر مهمی دارد.
- مستندسازی Migration/Backfill و به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و همین فایل.

## تعریف Done

Task زمانی Done است که Decision پیش‌نیاز موجود، State machine و Atomic CAS پیاده‌سازی و با تست رقابتی MongoDB اثبات، همه Quality Gateها موفق، Migration سازگار مستند و هیچ رفتار انتشار/زمان‌بندی وارد Scope نشده باشد.
