# T013 — دانلود و ذخیرهٔ انواع Media

## وضعیت

Completed

## هدف

افزودن vertical slice دانلود امن و قابل‌بازیابی هر Media item از Telegram و ذخیرهٔ خصوصی محلی پشت Port مستقل، همراه metadata، hash، timeout و retry محدود، بدون تجمیع Album یا cleanup انقضا.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.5 مدیریت مدیا`.
- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`، فیلدهای Media و انقضا.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`، دانلود فایل.
- `docs/REQUIREMENTS.md`، بخش `14. امنیت`، خصوصی بودن فایل Media.
- `docs/ARCHITECTURE.md`، بخش `6. Portها و Interfaceها`، `MediaStorage`.
- `docs/ARCHITECTURE.md`، بخش `10. ذخیره Media`.
- `docs/DECISIONS.md`، `ADR-004` و `ADR-008`.

## وابستگی‌ها

- T012 — تست Restart و Stabilization دریافت؛ باید Completed باشد.

## محدوده

- تکمیل مدل/DTO Media برای photo، video، document، audio، voice، animation/GIF، sticker و video note با order و Telegram reference.
- تعریف `MediaStorage` Port برای write stream اتمیک، open/read، existence و metadata لازم؛ implementation اولیه filesystem خصوصی.
- Use Case دانلود یک Media item در هر invocation و ثبت stateهای Pending/Downloading/Ready/Failed و attempt/error.
- stream کردن داده با محاسبهٔ content hash؛ فایل کامل در memory بار نشود.
- ساخت مسیر نهایی از شناسه داخلی/content hash، نه filename ورودی؛ filename فقط metadata sanitizeشده باشد.
- write به فایل موقت و rename اتمیک پس از موفقیت؛ partial file در failure پاک شود.
- timeout، size limit و retry محدود برای خطاهای transient؛ failure دائم ثبت شود.
- نگهداری metadata کافی برای بازخوانی تا ۱۴ روز، بدون binary در MongoDB.

## خارج از محدوده

- تجمیع Media Group/Album؛ T015.
- حذف فایل منقضی/Orphan؛ T014.
- Object Storage production، CDN یا URL عمومی.
- ارسال Media به مدیر/مقصد، thumbnail/transcode و virus scanning عمومی.
- publication و Premium Emoji.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/domain/media/models.py`
- `src/telegram_assist_bot/application/ports/media_storage.py`
- `src/telegram_assist_bot/application/download_post_media.py`
- `src/telegram_assist_bot/infrastructure/media/local_storage.py`
- توسعهٔ محدود Telegram media adapter و Post persistence mapper/index در صورت نیاز.
- `tests/unit/application/test_download_post_media.py`
- `tests/unit/infrastructure/media/test_local_storage.py`
- `tests/integration/test_media_download_storage.py`

## نکات پیاده‌سازی

- فایل در root Runtime پیکربندی‌شده محصور شود؛ `resolve()` و بررسی ancestry برای جلوگیری از traversal/symlink escape لازم است.
- hash در جریان write محاسبه و success DB فقط پس از commit فایل ثبت شود؛ failure DB پس از commit باید recovery قابل‌تشخیص داشته باشد.
- **ریسک Configuration:** storage root، timeout، size limit و retry bounded؛ root داخل repository یا web root رد شود.
- **ریسک Migration:** metadata/schema/index تازه باید نسخه‌شده و mapper T004 به‌روز شود؛ binary هرگز وارد MongoDB نشود.
- **ریسک Compatibility:** Telegram file reference ممکن است منقضی شود؛ DTO provider-specific خارج از Infrastructure نرود و metadata بازیابی لازم حفظ شود.
- **ریسک Concurrency:** دانلود هم‌زمان همان item باید یک فایل canonical بسازد یا نتیجهٔ idempotent بگیرد؛ rename اتمیک و conditional state لازم است.
- **ریسک Security:** filename کنترل مسیر نکند، permission خصوصی باشد، payload Log نشود و testهای traversal/symlink الزامی‌اند.

## معیارهای پذیرش عینی

1. همهٔ نوع‌های Media فهرست‌شده به metadata داخلی درست map می‌شوند.
2. دانلود stream شده و hash/size/MIME/name/path/status/expires_at ثبت می‌شوند.
3. success فقط فایل کامل canonical می‌گذارد و failure هیچ partial file قابل‌استفاده باقی نمی‌گذارد.
4. path traversal، absolute path و symlink escape رد می‌شوند.
5. دانلود هم‌زمان/تکراری یک item فایل دوم یا metadata متناقض نمی‌سازد.
6. timeout/size limit/permanent failure به category درست و retry bounded منتهی می‌شوند.
7. Persian filename metadata سالم است، اما مسیر storage از آن ساخته نمی‌شود.

## Unit Testهای الزامی

- mapping هر Media type و metadata ناقص/unsupported.
- state transition دانلود، hash/size و retry classification.
- local storage write/read، atomic rename و cleanup partial.
- traversal، absolute path، symlink escape و permission تا حد قابل‌حمل.
- concurrent same-content write و failure DB/storage شبیه‌سازی‌شده.
- نام فارسی/Emoji و UTF-8 metadata.

## Integration Testهای الزامی

- fake Telegram byte stream + filesystem موقت + MongoDB آزمایشی برای دانلود کامل و round-trip metadata.
- timeout/disconnect میانه، retry و نبود partial file.
- دو downloader هم‌زمان برای یک Media identity.
- restart پس از فایل commitشده و پیش از state update و recovery تعیین‌شده.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_download_post_media.py tests/unit/infrastructure/media/test_local_storage.py
uv run pytest tests/integration/test_media_download_storage.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

MongoDB و storage باید test-only باشند؛ بازبینی permission/path و Persian diff و `git diff --check` الزامی است.

## نتایج نهایی راستی‌آزمایی

- فرمان واقعی متمرکز: `uv run pytest tests/unit/application/test_download_post_media.py tests/unit/infrastructure/media tests/unit/infrastructure/telegram/user/test_media_adapter.py --basetemp .pytest-tmp/m2-t013-final-20260712-100830-998 -q`؛ نتیجه `9 passed` و `0 skipped` بود. Integration مشترک MongoDB نیز در اجرای `m2-focused-final-20260712-100724-136` برابر `1 passed` بود.
- streaming، timeout/cancellation، size bound، partial cleanup، traversal/absolute/symlink، atomic commit، concurrent identity و recovery پس از شکست metadata پاس شدند.
- Suite نهایی دو بار `702 passed` و `0 skipped`؛ Branch Coverage برابر `90.17%` است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T013 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن مدل، Port، Adapter، مسیر storage و data flow به `docs/CODE_MAP.md`.
- همگام‌سازی storage/error recovery واقعی در `docs/ARCHITECTURE.md`.
- ثبت تصمیم storage root/hash/SDK reference در `docs/DECISIONS.md` فقط اگر پایدار و مهم است.
- به‌روزرسانی Config نمونه برای گزینه‌های غیرحساس Media.

## تعریف انجام‌شدن

- Unit/Integration Testهای دانلود، امنیت مسیر، هم‌زمانی و recovery پاس شده‌اند.
- هیچ binary/partial/Secret داخل Git یا MongoDB ذخیره نشده است.
- Quality Gate و UTF-8 پاس شده‌اند.
- Scope به دانلود item مستقل محدود و Album/Cleanup/Publication پیاده نشده‌اند.
