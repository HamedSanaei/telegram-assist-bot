# T051 — انتشار یکتا، Retry و Audit تبلیغ

## وضعیت

`Completed`

نتیجهٔ نهایی: انتشار Slot با claim اتمیک، lease/version، Publication یکتای T029،
Retry محدود، بازیابی پس از Restart و Audit امن پیاده‌سازی و با MongoDB واقعی اثبات شد.

## هدف

Claim و انتشار idempotent یک Advertisement Slot مجاز با User API، Retry محدود و ثبت Audit کامل زمان/نتیجه، بدون تصمیم‌گیری درباره Collision با صف عادی.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `6.3 جلوگیری از انتشار تکراری تبلیغ`.
- `docs/REQUIREMENTS.md`، بخش `6.5 گزارش اجرای تبلیغات` در حد ثبت داده اجرا.
- `docs/REQUIREMENTS.md`، بخش `13 مدیریت خطا و Retry`.
- `docs/ARCHITECTURE.md`، بخش `4` (`AdvertisementSlot`)، بخش `5` (`PublishAdvertisementSlot`)، بخش `6` (`AdvertisementRepository` و `TelegramPublisherGateway`)، بخش‌های `7`، `9`، `11` و `14`.

## وابستگی‌ها

- `T029` و `T050` باید کامل شده باشند.

## دامنه کار

- Claim اتمیک Slot Due و از پیش مجازشده برای اجرا با owner/lease/version.
- استفاده از Snapshot متن/Media/Album T049 و Publisher idempotent T029 برای Destination.
- Unique key `campaign + destination + scheduled slot` و بازگرداندن نتیجه موفق قبلی بدون ارسال دوم.
- Retry محدود فقط برای خطای موقت قطعی؛ outcome مبهم کورکورانه Retry نشود.
- ثبت scheduled_at، actual published_at، destination، message IDs، status، attempts، last error و execution delay.
- بازیابی Lease منقضی پس از Restart و تکمیل Terminal سازگار.

## خارج از دامنه

- تصمیم Collision/فاصله با پست عادی (`T052`).
- تولید Slot یا Refresh Cache.
- Command/گزارش مدیران (`T053`).
- تغییر متن تبلیغ یا استفاده از Bot API برای انتشار مقصد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/advertisements/publish_advertisement_slot.py`
- توسعه `src/telegram_assist_bot/domain/advertisement_slot.py`
- توسعه `src/telegram_assist_bot/application/ports/advertisement_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/advertisement_repository.py`
- `src/telegram_assist_bot/workers/advertisement_publication_worker.py`
- `tests/unit/application/advertisements/test_publish_advertisement_slot.py`
- `tests/integration/advertisements/test_idempotent_advertisement_publication.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** max attempts، lease، timeout و backoff Campaign باید bounded؛ مقدار unbounded/نامعتبر Fail-fast باشد.
- **Migration:** Unique Index Slot publication و audit fields باید با Slotهای T050 سازگار و idempotent باشند؛ duplicate preflight شود.
- **Compatibility:** status/key/audit قرارداد پایدارند؛ Snapshot version و IDهای Album بدون migration تغییر شکل ندهند.
- **Concurrency:** claim/heartbeat/complete با owner+version؛ چند Worker یا Restart حداکثر یک اثر Publication برای key بسازند.
- **Security:** User API account/Destination مجاز دوباره بررسی، Session/Media path/raw error redacted و header مدیریتی از محتوا حذف شود.

## معیارهای پذیرش عینی

1. Slot واجد شرایط متن/Media/Album را با User API و Snapshot درست منتشر می‌کند.
2. درخواست ترتیبی/هم‌زمان/Restart برای key یکسان انتشار دوم نمی‌سازد.
3. Retry فقط bounded و failure-aware است و outcome مبهم دوباره کور ارسال نمی‌شود.
4. همه فیلدهای Audit بخش `6.5` دقیق و execution delay قابل محاسبه ثبت می‌شوند.
5. Lease منقضی ناموفق بازیابی و success Terminal دوباره Claim نمی‌شود.
6. هیچ Collision resolution یا گزارش Bot در این Task انجام نمی‌شود.

## تست‌های واحد الزامی

- success، AlreadyPublished، transient/permanent/ambiguous و max attempts.
- محاسبه execution delay و Audit برای متن و Album.
- رد Snapshot missing/stale طبق policy مصوب و Destination نامعتبر.
- ownership/version و lease-lost.

## تست‌های یکپارچه‌سازی الزامی

- چند Worker MongoDB + Publisher Fake و اثبات یک تماس موثر.
- Crash پس از Claim، lease expiry و Restart.
- timeout مبهم پس از ارسال و عدم ارسال دوم.
- persistence همه Audit fields و IDهای Album.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/advertisements/test_publish_advertisement_slot.py
uv run pytest tests/integration/advertisements/test_idempotent_advertisement_publication.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت lifecycle، idempotency key، Lease و audit در `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md`.
- ثبت Decision فقط اگر سیاست outcome مبهم نسبت به T029 تفاوت مهم دارد.
- به‌روزرسانی Config retry/worker و سپس `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که انتشار یکتا و Audit با رقابت/Crash/Restart اثبات، Retry bounded و Secret/metadata safety رعایت، همه Quality Gateها موفق و Collision/Report خارج از Scope باشد.

## نتیجهٔ راستی‌آزمایی نهایی

- تست واحد T051: ۷ آزمون موفق.
- تست یکپارچه MongoDB T051: ۳ آزمون موفق.
- رگرسیون متمرکز Publication/Slot: ۴۱ آزمون موفق.
- مجموعهٔ کامل non-live: ۱۳۱۴ آزمون موفق.
- `uv lock --check`، Ruff، Ruff format، MyPy، بررسی یکپارچگی متن و
  `git diff --check`: موفق.
