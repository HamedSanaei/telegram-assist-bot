# T010 — Listener زندهٔ پیام متنی

## وضعیت

Completed

## هدف

دریافت eventهای متنی جدید یک Source فعال از Telegram User API و عبور آن‌ها از همان مسیر mapping و ذخیرهٔ idempotent T009، با lifecycle و reconnect محدود و بدون Media processing.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.2 کانال‌های مبدا`، دریافت لحظه‌ای پیام جدید.
- `docs/REQUIREMENTS.md`، بخش `5.3 جلوگیری از پردازش تکراری`، رویداد تکراری.
- `docs/REQUIREMENTS.md`، بخش `13. مدیریت خطا و Retry`، شبکه/Flood Wait.
- `docs/ARCHITECTURE.md`، بخش `5. Use Caseهای Application`، `HandleLiveMessage`.
- `docs/ARCHITECTURE.md`، بخش `7. مسئولیت Telegram User API`، Listener.

## وابستگی‌ها

- T009 — خزش پیام‌های متنی امروز یک کانال؛ باید Completed باشد.

## محدوده

- قرارداد stream/subscription برای event پیام جدید و DTO داخلی مشترک با History.
- Worker lifecycle برای subscribe، consume، cancellation و unsubscribe تمیز یک Source.
- فیلتر event به Source canonical و پیام text/caption؛ service/media-only بدون پردازش Media شمارش/رد شود.
- فراخوانی Use Case ingest مشترک و ثبت نتیجهٔ created/already-existing/failed.
- reconnect محدود خطاهای transient با backoff و احترام به FloodWait bounded؛ خطای permanent Worker را متوقف کند.
- جلوگیری از رشد بی‌حد حافظه با backpressure/buffer محدود یا consume مستقیم.
- Logging ساختاریافته بدون متن کامل پیام.

## خارج از محدوده

- hardening رقابت Crawl/Listener و multi-worker؛ T011.
- replay تاریخی برای gap؛ Startup باید ابتدا T009 را اجرا کند و orchestration کامل در T012 تثبیت می‌شود.
- Edit/Delete event، Media download، Album و Bot API.
- اجرای هم‌زمان چند Source در یک Worker؛ factory/orchestration بعدی می‌تواند چند نمونه بسازد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/handle_live_message.py`
- توسعهٔ subscription port و Telegram event adapter.
- `src/telegram_assist_bot/workers/live_text_listener.py`
- `tests/unit/application/test_handle_live_message.py`
- `tests/unit/workers/test_live_text_listener.py`
- `tests/contract/telegram/test_live_message_contract.py`
- `tests/integration/test_live_text_listener.py`

## نکات پیاده‌سازی

- History و live mapping باید یک mapper application-owned مشترک داشته باشند تا Entity semantics متفاوت نشود.
- callback SDK نباید منطق کسب‌وکار یا DB call پنهان داشته باشد؛ event به queue/Use Case منتقل شود.
- **ریسک Configuration:** buffer/reconnect/timeout bounded و source از گزارش T008 گرفته شود.
- **ریسک Migration:** schema تازه لازم نیست؛ تغییر document خارج از T004 ممنوع مگر صریح مستند شود.
- **ریسک Compatibility:** event object SDK فقط در Adapter و با fixture contract پوشش داده شود.
- **ریسک Concurrency:** یک consumer مالک subscription است؛ correctness رقابت چند producer در T011.
- **ریسک Security:** payload، Session و dialogهای غیرهدف Log نشوند؛ event source پیش از پردازش validate شود.

## معیارهای پذیرش عینی

1. event متنی target به Post تبدیل و دقیقاً از Repository مشترک ذخیره می‌شود.
2. event منبع دیگر، service و media-only وارد مسیر text نمی‌شوند.
3. cancellation subscription/worker را تمیز می‌بندد و task معلق باقی نمی‌گذارد.
4. transient disconnect reconnect محدود دارد؛ permission/invalid-session بدون loop بی‌نهایت متوقف می‌شود.
5. event تکراری نتیجهٔ already-existing می‌دهد و document دوم نمی‌سازد.
6. buffer از حد پیکربندی‌شده عبور نمی‌کند و سیاست backpressure تست شده است.

## Unit Testهای الزامی

- mapping/ingest event معتبر و فیلتر eventهای نامعتبر.
- duplicate result و error propagation.
- reconnect/backoff/FloodWait با clock جعلی و سقف تلاش.
- cancellation/unsubscribe و backpressure buffer.
- عدم نشت payload در Log.

## Integration Testهای الزامی

- fake async event stream + MongoDB آزمایشی برای event جدید و تکراری.
- disconnect/reconnect میانهٔ stream و ادامهٔ event بعدی بدون duplicate.
- shutdown Worker و assertion نبود task/resource باز.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/test_handle_live_message.py tests/unit/workers/test_live_text_listener.py
uv run pytest tests/contract/telegram/test_live_message_contract.py tests/integration/test_live_text_listener.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

MongoDB باید test-only باشد؛ بازبینی lifecycle و fixture فارسی و `git diff --check` الزامی است.

## نتایج نهایی راستی‌آزمایی

- Unit/Contract متمرکز T010: `13 passed` و `0 skipped`.
- مسیرهای متمرکز با `uv run pytest tests/unit/application/test_handle_live_message.py tests/unit/workers/test_live_text_listener.py tests/contract/telegram/test_live_message_contract.py tests/integration/test_live_text_listener.py --basetemp <unique>` اجرا شدند.
- Integration واقعی MongoDB: `2 passed` و `0 skipped`؛ duplicate event، backpressure محدود، reconnect، unsubscribe و propagation لغو تست شد.
- Suite کامل non-live: `669 passed` و `0 skipped` در دو اجرا؛ Branch Coverage برابر `90.02%` و همهٔ Quality Gateها موفق‌اند.

## به‌روزرسانی‌های مستندات

- ثبت Status/نتایج و به‌روزرسانی T010 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن worker/subscription/data flow به `docs/CODE_MAP.md`.
- همگام‌سازی listener lifecycle/backpressure در `docs/ARCHITECTURE.md`.
- ثبت سیاست reconnect فقط اگر تصمیم معماری پایدار تازه است.

## تعریف انجام‌شدن

- live text slice با Unit/Contract/Integration Test پاس شده است.
- reconnect/cancellation bounded و observability redacted است.
- Quality Gateها و UTF-8 پاس شده‌اند.
- Media، چند Worker hardening و featureهای مدیریتی اضافه نشده‌اند.
