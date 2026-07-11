# T023 — Keyboard دو ستونی مقصدها

## وضعیت

`Planned`

## هدف

تولید Keyboard قطعی و دو ستونی برای Destinationهای مجاز هر Post، با Callback Data امن T021 و نمایش اولیه وضعیت، بدون تغییر State یا آغاز انتشار.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش `5.14 دکمه‌های کانال‌های مقصد`.
- `docs/REQUIREMENTS.md`، بخش `5.13 مدیران مجاز` فقط برای محدودسازی مقصد.
- `docs/ARCHITECTURE.md`، بخش‌های `3`، `4` (`DestinationSelection`)، `6`، `8`، `13` و `15`.
- `docs/ARCHITECTURE.md`، بخش `17`، ابهام‌های `2`، `6` و `13`.

## وابستگی‌ها

- `T002`، `T021` و `T022` باید کامل شده باشند.

### پیش‌نیاز تصمیم

پیش از پیاده‌سازی باید معنای دکمه «فوری» (فقط Toggle انتخاب، آغاز بی‌درنگ انتشار، یا نیازمند Confirm)، سیاست عبور از محدودیت تعداد/اندازه دکمه‌ها، متن/آیکون وضعیت‌ها و Audience هدف تصویب و در `docs/DECISIONS.md` ثبت شود. این Task فقط قراردادی را می‌سازد که آن Decision اجازه می‌دهد.

## دامنه کار

- تعریف مدل مستقل Keyboard/Row/Button در Presentation.
- فیلتر Destinationهای فعال و مجاز برای Source/Post/Admin بر اساس Config و Authorization.
- مرتب‌سازی قطعی بر اساس فیلد ترتیب مصوب Config؛ نه ترتیب تصادفی Map/Database.
- ساخت دقیقاً یک ردیف دو ستونی «زمان‌بندی» و «فوری» برای هر Destination در حالت عادی.
- صدور Callback Token از T021 و قرار دادن فقط Callback Data کوتاه در Button.
- اجرای سیاست مصوب overflow بدون ساخت رفتار انتشار یا Toggle.

## خارج از دامنه

- تغییر حالت انتخاب، Atomic update یا رقابت مدیران (`T024`).
- ویرایش Keyboard سایر مدیران (`T025`).
- انتشار فوری، ایجاد Schedule Job یا Cancellation.
- اختراع Pagination/Confirm اگر در Decision تصویب نشده باشد.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/presentation/bot/destination_keyboard.py`
- مدل‌های Keyboard در `src/telegram_assist_bot/presentation/bot/models.py`
- Wiring Renderer موجود در `src/telegram_assist_bot/presentation/bot/approval_renderer.py`
- `tests/unit/presentation/bot/test_destination_keyboard.py`
- `tests/integration/telegram/test_destination_keyboard_serialization.py`

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** ID، label/username، active، allowed sources و ترتیب Destination باید Validate شوند؛ ID تکراری Fail-fast است.
- **Migration:** Migration دیتابیس در این Task انتظار نمی‌رود؛ تغییر Schema Config باید نمونه و سازگاری نسخه را رعایت کند.
- **Compatibility:** مدل داخلی Keyboard از نوع‌های SDK جدا و Serialization محدود به Adapter باشد؛ فرمت Callback نسخه‌دار T021 حفظ شود.
- **Concurrency:** Renderer باید Snapshot وضعیت داده‌شده را نشان دهد؛ تغییر واقعی و حل stale state در T024/T025 انجام می‌شود.
- **Security:** Destination غیرمجاز حتی اگر در Config عمومی باشد Button نمی‌گیرد؛ متن Button و Callback Data نباید Secret یا مجوز قابل جعل حمل کنند.

## معیارهای پذیرش عینی

1. برای هر Destination مجاز، یک ردیف با دو Button و ترتیب قطعی تولید می‌شود.
2. Destination غیرفعال/غیرمجاز حذف و ID تکراری Config رد می‌شود.
3. Callback Data همه Buttonها از قرارداد T021 و در سقف مصوب است.
4. وضعیت اولیه/موجود با label مصوب نشان داده می‌شود و هیچ State تغییر نمی‌کند.
5. سیاست overflow با تست مرزی اثبات شده و با Decision یکسان است.
6. هیچ انتشار یا Schedule Job در اثر Render ساخته نمی‌شود.

## تست‌های واحد الزامی

- صفر، یک و چند Destination با ترتیب قطعی.
- حذف مقصد غیرفعال و غیرمجاز و رد Configuration تکراری.
- ساخت دو ستون، label فارسی/انگلیسی و Callback کوتاه.
- مرز حداکثر Button/Callback و رفتار overflow مصوب.
- اثبات بدون side effect بودن Renderer.

## تست‌های یکپارچه‌سازی الزامی

- Serialization مدل Keyboard به payload Bot Adapter با Fixture بدون Token واقعی.
- Resolve شدن Callback تولیدی توسط قرارداد T021.
- تماس زنده تلگرام N/A است؛ این Task فقط Contract مرزی را می‌آزماید.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/unit/presentation/bot/test_destination_keyboard.py
uv run pytest tests/integration/telegram/test_destination_keyboard_serialization.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت Decision رفتار فوری، overflow و labelها در `docs/DECISIONS.md`.
- ثبت Builder/Serialization Keyboard در `docs/CODE_MAP.md`.
- به‌روزرسانی مثال Config و مستندات Config در صورت افزودن ترتیب/label.
- به‌روزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و نتیجه همین فایل.

## تعریف Done

Task زمانی Done است که Decisionهای UX ثبت، Keyboard دو ستونی قطعی و محدود به مجوز ساخته، Contract Callback و مرزهای اندازه تست، Quality Gateها موفق و Scope فاقد هرگونه Toggle یا انتشار باشد.
