# T009 — خزش پیام‌های متنی امروز یک کانال

## وضعیت

Planned

## هدف

پیاده‌سازی یک vertical slice محدود که History پیام‌های متنی روز جاری یک Source فعال را در بازهٔ دقیق Timezone دریافت، به DTO داخلی تبدیل و از مسیر idempotent در MongoDB ذخیره کند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.2 کانال‌های مبدا`، دریافت پیام‌های روز جاری و مرز روز.
- `docs/REQUIREMENTS.md`، بخش `5.3 جلوگیری از پردازش تکراری`، استفاده از identity موجود.
- `docs/REQUIREMENTS.md`، بخش `5.4 ذخیره اطلاعات پست`، فیلدهای متن در مرحلهٔ دریافت.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، `CrawlTodayTextPosts`.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`، History و DTO mapping.
- `docs/ARCHITECTURE.md`، بخش `14. Logging، Retry، Idempotency و هم‌زمانی`.

## وابستگی‌ها

- T004 — MongoDB و Persistence یکتای Post؛ باید Completed باشد.
- T008 — اعتبار Session، Premium و دسترسی کانال؛ باید Completed باشد.

## محدوده

- Use Case `CrawlTodayTextPosts` برای دقیقاً یک Source canonical در هر invocation.
- محاسبهٔ start-of-day برابر `00:00:00` Timezone تنظیم‌شده و تبدیل امن بازه به UTC.
- درخواست paginated History از start تا Clock اکنون با timeout و retry محدود خطاهای موقت.
- پذیرش فقط messageهای متن/Caption بدون Media download؛ پیام service/empty و Media-only برای T013 کنار گذاشته و با نتیجهٔ typed شمارش شوند.
- mapping شناسه، source metadata، متن/Caption اصلی، Entityها و timestamp به Post T003.
- ذخیره از `PostRepository` T004 و گزارش counts برای created/already-existing/skipped/failed.
- checkpoint در حافظه منبع حقیقت نیست؛ اجرای دوباره باید به Repository idempotent تکیه کند.

## خارج از محدوده

- Listener زنده؛ T010.
- سخت‌سازی رقابت Crawl/Listener و چند Worker؛ T011.
- دانلود Media/Album، edit/delete پیام و Forward policy.
- AI، پاک‌سازی متن، approval و publication.
- خزش هم‌زمان همهٔ کانال‌ها؛ orchestration بعدی می‌تواند invocationهای تک‌کاناله بسازد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/crawl_today_text_posts.py`
- توسعهٔ قرارداد History در `src/telegram_assist_bot/application/ports/telegram_source_gateway.py`
- `src/telegram_assist_bot/infrastructure/telegram/user/history_adapter.py`
- mapper DTO Telegram به ورودی Domain.
- `src/telegram_assist_bot/workers/crawl_once.py`
- `tests/unit/application/test_crawl_today_text_posts.py`
- `tests/unit/infrastructure/telegram/user/test_history_mapper.py`
- `tests/integration/test_crawl_today_text_posts.py`

## نکات پیاده‌سازی

- Clock تزریق شود؛ `datetime.now()` مستقیم در Use Case ممنوع است.
- بازهٔ query inclusive/exclusive صریح باشد تا پیام دقیقاً نیمه‌شب یا «اکنون» دوبار/جاافتاده نشود.
- Entity offsets و متن اصلی هیچ normalize نشوند.
- pagination باید bounded و cancellation-safe باشد و page token SDK وارد Application نشود.
- **ریسک Configuration:** Timezone/source active و page size/timeout از مدل معتبر T002/T008.
- **ریسک Migration:** فیلدهای تازهٔ ingest فقط با schema mapper T004 افزوده شوند؛ schema بی‌صدا تغییر نکند.
- **ریسک Compatibility:** fixtureهای SDK برای تاریخ/Entity نسخه‌بندی شوند.
- **ریسک Concurrency:** این Task idempotency پایهٔ Repository دارد؛ race گسترده در T011 تست/تثبیت می‌شود.
- **ریسک Security:** متن کامل پست و Session در Log نیاید؛ فقط counts و شناسه‌های غیرحساس لازم.

## معیارهای پذیرش عینی

1. برای `Asia/Tehran` مرز روز دقیق محاسبه و به UTC صحیح تبدیل می‌شود.
2. همهٔ پیام‌های متنی/Caption داخل بازه و هیچ پیام خارج بازه ذخیره نمی‌شود.
3. pagination کامل و هر page حداکثر یک‌بار مصرف می‌شود.
4. اجرای دوباره همان crawl document دوم نمی‌سازد و counts درست است.
5. Persian/ZWNJ/Emoji/Entity و timestamp منبع بدون تغییر ذخیره می‌شوند.
6. failure موقت طبق policy محدود retry و failure دائم واضح گزارش می‌شود.

## Unit Testهای الزامی

- مرز نیمه‌شب، DST یک Zone نمونه و تبدیل UTC با Clock ثابت.
- pagination چندصفحه‌ای، صفحهٔ خالی و cancellation.
- فیلتر text/caption/service/media-only و mapping Entity فارسی/Emoji.
- نتیجهٔ created/already-existing و retry classification.
- اثبات عدم normalize متن و عدم Log payload.

## Integration Testهای الزامی

- fake History gateway چندصفحه‌ای + MongoDB آزمایشی: crawl، ذخیره، اجرای دوباره و counts.
- Post دقیقاً روی دو طرف مرز روز و assertion روی query/ذخیره.
- failure بین pageها و اجرای مجدد بدون duplicate.

تست زنده Telegram لازم نیست؛ MongoDB Integration واقعی و fake gateway deterministic الزامی‌اند.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_crawl_today_text_posts.py tests/unit/infrastructure/telegram/user/test_history_mapper.py
uv run pytest tests/integration/test_crawl_today_text_posts.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

`TEST_MONGODB_URI` باید آزمایشی باشد؛ بازبینی دستی fixture فارسی و `git diff --check` الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T009 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن Use Case، worker، DTO mapping و data flow به `docs/CODE_MAP.md`.
- همگام‌سازی قرارداد History/مرز روز در `docs/ARCHITECTURE.md`.
- ثبت تصمیم مهم دربارهٔ بازه/offset SDK در `docs/DECISIONS.md` در صورت نیاز.

## تعریف انجام‌شدن

- slice متنی تک‌کانال و Testهای واحد/Integration کامل پاس شده‌اند.
- idempotency اجرای مجدد و مرز Timezone اثبات شده است.
- Quality Gate، UTF-8 و Secret safety پاس شده‌اند.
- Listener، Media و Featureهای بعدی وارد Scope نشده‌اند.
