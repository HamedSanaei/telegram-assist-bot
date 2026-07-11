# T004 — MongoDB و Persistence یکتای Post

## وضعیت

Planned

## هدف

پیاده‌سازی Port و Adapter متمرکز MongoDB برای ذخیره و بازیابی Post با یکتایی قطعی هویت منبع، TTL چهارده‌روزه و Transition اتمیک، به‌گونه‌ای که Restart یا رقابت Workerها رکورد دوم نسازد.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.3 جلوگیری از پردازش تکراری`.
- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`.
- `docs/REQUIREMENTS.md`، بخش `10. وضعیت‌های پیشنهادی پست`، فقط persistence تاریخچه و وضعیت.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، تشخیص پیام تکراری و انقضا.
- `docs/ARCHITECTURE.md`، بخش `6. Portها و Interfaceها`، قرارداد `PostRepository`.
- `docs/ARCHITECTURE.md`، بخش `9. MongoDB و مدل ماندگاری`.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`.
- `docs/DECISIONS.md`، `ADR-002` و `ADR-003`.

## وابستگی‌ها

- T002 — Configuration و Secret Validation؛ باید Completed باشد.
- T003 — مدل Domain و چرخه عمر Post؛ باید Completed باشد.

## محدوده

- تعریف `PostRepository` در لایهٔ Application با عملیات موردنیاز همین Task: insert/upsert idempotent، get by identity/id، query غیرمنقضی و transition با expected version.
- پیاده‌سازی Adapter async MongoDB و mapper صریح بین Domain و document؛ objectهای driver نباید از Infrastructure خارج شوند.
- تعریف schema version ابتدایی Document.
- ایجاد idempotent و Fail-fast Indexها: Unique compound روی `source_channel_id + source_message_id` و TTL تک‌فیلدی روی `expires_at` با `expireAfterSeconds: 0`.
- بازگرداندن نتیجهٔ صریح `Created` یا `AlreadyExists` برای insert تکراری، بدون تکیه بر check-then-insert.
- اعمال `expires_at > now` در queryهای Application-facing، چون TTL حذف فوری تضمین نمی‌کند.
- Transition اتمیک با شرط `_id + version/current_status` و تفکیک not-found از concurrency conflict.
- فراهم کردن fixture یا harness آزمایشی MongoDB با database یکتای تست و cleanup امن.

## خارج از محدوده

- Media binary/metadata کامل، GridFS یا Object Storage.
- Crawl/Listener و هر Telegram Adapter.
- Jobهای Schedule، AI، Publication یا Callback collectionها.
- Cleanup فایل‌های محلی؛ T014 آن را انجام می‌دهد.
- migration عمومی چندنسخه‌ای یا ابزار deployment production؛ فقط schema/index اولیه و مسیر extension تعریف می‌شود.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/ports/post_repository.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/client.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/post_mapper.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/post_repository.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/indexes.py`
- `tests/unit/infrastructure/persistence/test_post_mapper.py`
- `tests/integration/infrastructure/persistence/test_mongodb_post_repository.py`
- `tests/integration/infrastructure/persistence/conftest.py`
- Configuration/fixtureهای تست موردنیاز مطابق قرارداد T001/T002.
- اسناد پروژه طبق بخش «به‌روزرسانی‌های مستندات».

## نکات پیاده‌سازی

- انتخاب driver async و حداقل نسخهٔ MongoDB باید پیش از edit ثبت شود؛ API deprecated وارد قرارداد نشود.
- همهٔ تماس‌های MongoDB timeout محدود از Configuration داشته باشند.
- insert باید مستقیم به Unique Index تکیه کند و DuplicateKey را فقط برای همان index به `AlreadyExists` نگاشت کند؛ خطاهای دیگر مخفی نشوند.
- BSON datetime به UTC تبدیل و timezone awareness در mapper بازسازی شود.
- دادهٔ Persian/Entity بدون normalize ذخیره شود.
- **ریسک Configuration:** URI/نام DB/timeout از مدل T002 گرفته شود؛ password هرگز در خطا یا Log چاپ نشود.
- **ریسک Migration:** نام collection، index و فیلدها قرارداد پایدارند؛ bootstrap index باید تغییر ناسازگار را تشخیص دهد و Fail-fast کند، نه اینکه index production را مخفیانه drop کند.
- **ریسک Compatibility:** mapper باید document schema version را بررسی و نسخهٔ ناشناخته را با خطای روشن رد کند.
- **ریسک Concurrency:** check-before-insert ممنوع؛ Unique Index و conditional update خط دفاع اصلی‌اند و با رقابت واقعی تست می‌شوند.
- **ریسک Security:** test database جدا، URI redaction، least-privilege در مستندات و ممنوعیت استفاده از DB production در suite.

## معیارهای پذیرش عینی

1. Index یکتای دقیق دو فیلد و TTL index دقیق `expires_at` به‌صورت idempotent ساخته می‌شوند.
2. درج هم‌زمان یک هویت منبع دقیقاً یک document می‌سازد؛ همهٔ callerها نتیجهٔ deterministic می‌گیرند.
3. insert تکراری دادهٔ اصلی رکورد موجود را overwrite نمی‌کند.
4. Post شامل Persian/Entity/history با mapper رفت‌وبرگشت بدون تغییر دارد.
5. queryها document منقضی‌نشده را برمی‌گردانند و document با `expires_at <= now` را حتی پیش از sweep TTL حذف منطقی می‌کنند.
6. Transition با version صحیح موفق و با version/status کهنه به conflict مشخص تبدیل می‌شود.
7. index setup ناسازگار یا اتصال ناموفق Startup را با خطای redacted متوقف می‌کند.
8. هیچ نوع MongoDB از Port یا Domain عبور نمی‌کند.

## Unit Testهای الزامی

- mapper رفت‌وبرگشت همهٔ فیلدهای فعلی Post، Entity و history.
- تبدیل UTC و رد document ناقص/schema ناشناخته.
- نگاشت دقیق DuplicateKey مربوط به index موردنظر و عدم بلعیدن خطاهای نامرتبط.
- قرارداد result و exceptionهای Repository.
- عدم نشت URI/credential در خطاها.

## Integration Testهای الزامی

- ساخت و اجرای دوبارهٔ index initializer و assertion روی key/options واقعی.
- insert، get و round-trip Post فارسی/Emoji در MongoDB واقعی آزمایشی.
- اجرای concurrent insert برای یک identity و assertion روی یک document.
- conditional transition رقابتی با یک winner و conflict برای writer کهنه.
- درج document منقضی و اثبات فیلتر query بدون انتظار nondeterministic برای TTL monitor.
- جداسازی database تست و cleanup فقط همان database.

این تست‌ها به MongoDB آزمایشی صریح (URI تست یا container مورد تأیید T001) نیاز دارند و نباید به production متصل شوند. Skip شدن آن‌ها به دلیل نبود MongoDB برای تکمیل Task قابل قبول نیست.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/infrastructure/persistence/test_post_mapper.py
uv run pytest tests/integration/infrastructure/persistence/test_mongodb_post_repository.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

پیش از فرمان Integration باید `TEST_MONGODB_URI` فقط به instance آزمایشی اشاره کند. بازبینی `git diff --check`، فهرست indexها و redaction دستی الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status و نتایج واقعی verification در همین فایل.
- به‌روزرسانی T004 در `docs/ROADMAP.md` و وضعیت جاری در `docs/STATUS.md`.
- افزودن Port، Adapter، mapper، collection و مسیر تست به `docs/CODE_MAP.md`.
- همگام‌سازی schema، index و رفتار TTL/atomicity در `docs/ARCHITECTURE.md`.
- ثبت driver، حداقل MongoDB یا راهبرد schema evolution در `docs/DECISIONS.md` اگر تصمیم پایدار تازه‌ای گرفته شد.

## تعریف انجام‌شدن

- Unit و Integration Testهای الزامی روی MongoDB آزمایشی پاس شده‌اند و هیچ‌کدام skip نیست.
- Unique/TTL index و atomic transition عیناً inspect شده‌اند.
- Quality Gateهای کامل T001 پاس شده‌اند.
- Secret، URI واقعی یا فایل محلی وارد Git نشده و متن فارسی/Entity سالم است.
- مستندات با collection و قرارداد واقعی همگام‌اند و هیچ Feature خارج از Post persistence اضافه نشده است.
