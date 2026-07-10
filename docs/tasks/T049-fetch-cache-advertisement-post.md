# T049 — دریافت/Cache پست تبلیغ از URL

## وضعیت

`Planned`

## هدف

دریافت idempotent پست تبلیغاتی Configured از URL تلگرام، حفظ متن، Entity، Premium Emoji، Media و Album، و نگهداری Snapshot/Cache قابل ردیابی مطابق سیاست مصوب؛ بدون ساخت Slot یا انتشار.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `6.1 دریافت پست تبلیغاتی`.
- `docs/REQUIREMENTS.md`، بخش‌های `5.5` تا `5.7` فقط برای حفظ Media، Album و Entity.
- `docs/ARCHITECTURE.md`، بخش `4` (`AdvertisementCampaign`)، بخش `5` (`FetchAdvertisementSource`)، بخش `6` (`AdvertisementRepository`، `TelegramSourceGateway` و `MediaStorage`)، بخش‌های `7`، `9`، `10` و `14`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `10` و `12`.

## وابستگی‌ها

- `T008`، `T013`، `T015` و `T048` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید default سیاست `latest`/`cached`/`periodic refresh`، بازه Refresh، رفتار Edit/Delete منبع، طول عمر Cache/Media، fallback هنگام عدم دسترسی به منبع و زمان اثر Config جدید تصویب و در `docs/DECISIONS.md` ثبت شود. این Task مجاز نیست این مقادیر را از خود انتخاب کند.

## دامنه کار

- Parse و Validation URL پست عمومی تلگرام به source/message identity بدون اعتماد به redirect یا hostname دلخواه.
- دریافت Post یا همه اعضای Media Group از `TelegramSourceGateway` و تبدیل فوری DTOهای SDK به مدل داخلی.
- استفاده مجدد از Media download/storage و Album ordering Taskهای T013/T015.
- ذخیره Snapshot نسخه‌دار شامل source identity، fetched/edited time، content hash، text/caption/entities، Media references و cache policy metadata.
- Refresh اتمیک و idempotent مطابق Decision، بدون از دست‌دادن Snapshot معتبر قبلی در شکست.
- Timeout و Retry محدود برای خطای موقت؛ خطای دائمی/حذف منبع نتیجه صریح بگیرد.

## خارج از دامنه

- گسترش زمان‌ها و ساخت Advertisement Slot (`T050`).
- انتشار، Retry انتشار، Collision یا گزارش مدیران.
- Crawl عمومی کانال، Edit/Delete همه Postهای عادی یا Object Storage جدید.
- انتخاب ضمنی سیاست Cache/Retention.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/advertisement_source.py`
- `src/telegram_assist_bot/application/advertisements/fetch_advertisement_source.py`
- توسعه `src/telegram_assist_bot/application/ports/advertisement_repository.py`
- توسعه `src/telegram_assist_bot/infrastructure/mongodb/advertisement_repository.py`
- توسعه Mapper دریافت URL در `src/telegram_assist_bot/infrastructure/telegram/`
- `tests/unit/application/advertisements/test_fetch_advertisement_source.py`
- `tests/integration/advertisements/test_advertisement_source_cache.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** URL، policy، refresh interval و retention باید typed و Fail-fast باشند؛ Campaign غیرفعال fetch نمی‌شود.
- **Migration:** Snapshot/version/indexهای campaign+source باید سازگار و idempotent باشند؛ Media قبلی تا تصمیم cleanup معتبر بی‌صدا حذف نشود.
- **Compatibility:** نوع SDK، edit marker و file handle فقط در Adapter بمانند؛ تغییر policy Config نباید Snapshot قابل انتشار را بی‌مهاجرت خراب کند.
- **Concurrency:** دو refresh هم‌زمان با compare-and-set/hash/version فقط یک نسخه جاری معتبر بسازند و شکست دیررس نسخه جدیدتر را overwrite نکند.
- **Security:** فقط hostname/URL تلگرام مصوب پذیرفته شود، redirect/SSRF و Path traversal رد، Session و URL حساس Log نشوند.

## معیارهای پذیرش عینی

1. URL معتبر به Post/Album درست Resolve و URL نامعتبر یا host غیرمجاز پیش از تماس رد می‌شود.
2. متن، Caption، Entity، Premium Emoji، Media و ترتیب Album در Snapshot حفظ می‌شوند.
3. fetch/refresh تکراری با محتوای یکسان Snapshot یا Media duplicate نمی‌سازد.
4. Edit/Delete و شکست refresh دقیقاً مطابق Decision ثبت‌شده رفتار می‌کنند.
5. شکست refresh Snapshot معتبر قبلی را خراب یا حذف نمی‌کند.
6. هیچ Slot یا Publication در این Task ساخته نمی‌شود.

## تست‌های واحد الزامی

- Parse URLهای معتبر/نامعتبر، redirect/host ممنوع و شناسه پیام.
- cache hit، refresh due/not-due و تغییر content hash مطابق Decision.
- Post متنی، Media و Album مرتب با فارسی/Custom Emoji.
- خطای موقت، حذف منبع و حفظ Snapshot قبلی.

## تست‌های یکپارچه‌سازی الزامی

- Telegram gateway جعلی + MediaStorage + MongoDB برای Post و Album.
- دو refresh هم‌زمان و اثبات یک نسخه جاری/عدم Media duplicate.
- Restart با Snapshot موجود و اجرای policy مصوب بدون fetch اضافی.
- تست زنده Telegram خارج از Suite پیش‌فرض و N/A برای Done است.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/application/advertisements/test_fetch_advertisement_source.py
uv run pytest tests/integration/advertisements/test_advertisement_source_cache.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision Cache/Edit/Delete/Retention در `docs/DECISIONS.md`.
- ثبت Snapshot، Refresh flow، Index و Media lifecycle در `docs/ARCHITECTURE.md` و `docs/CODE_MAP.md`.
- به‌روزرسانی example Config فقط با policyهای مصوب و بدون URL خصوصی/Secret.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتایج همین فایل.

## تعریف Done

Task زمانی Done است که Decisionهای Cache ثبت، fetch/refresh و حفظ Album/Entity با تست رقابتی و Restart اثبات، SSRF/Secret safety و UTF-8 بازبینی، همه Quality Gateها موفق و Slot/Publication خارج از Scope باشد.
