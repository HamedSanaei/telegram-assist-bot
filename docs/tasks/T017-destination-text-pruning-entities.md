# T017 — پاک‌سازی متن مقصدی و بازسازی Entity

## وضعیت

Planned

## هدف

تولید متن و Entityهای مستقل برای هر Destination با جایگزینی Username مبدا، حذف Username/لینک تلگرام نامرتبط و rebasing دقیق Entityها، در حالی که متن/Entity اصلی immutable و Premium/Custom Emoji سالم می‌ماند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `5.7 حفظ Premium Emoji`.
- `docs/REQUIREMENTS.md`، بخش `5.10 پاک‌سازی و بازنویسی متن`.
- `docs/REQUIREMENTS.md`، بخش `15. تست‌ها`، حذف Username/link، محافظت مقصد و محاسبه Entity.
- `docs/ARCHITECTURE.md`، بخش `2. اهداف معماری`، خروجی مستقل هر مقصد.
- `docs/ARCHITECTURE.md`، بخش `4. مدل Domain`، immutability متن اصلی.
- `docs/ARCHITECTURE.md`، بخش `15. راهبرد تست`، Entity rebasing.

## وابستگی‌ها

- T003 — مدل Domain و چرخه عمر Post؛ باید Completed باشد.

## محدوده

- مدل application-owned برای `DestinationPreparedContent` شامل text/caption و Entityهای بازسازی‌شده.
- اجرای دقیق ترتیب بخش ۵.۱۰: replace مبدا، protect مقصد، حذف username/linkهای دیگر، اصلاح whitespace/blank lines و rebase Entity.
- پوشش الگوهای `@username`، `t.me`/`telegram.me` با http/https، post path و query string.
- match case-insensitive برای host/username در جایی که Telegram semantics اجازه می‌دهد، با حفظ casing متن مقصدی پیکربندی‌شده.
- تولید مستقل خروجی برای هر Destination بدون cache key ناقص یا mutation مشترک.
- تعریف واحد canonical offset/length مطابق Telegram/SDK انتخاب‌شده و utility conversion صریح برای Python Unicode در صورت نیاز.
- حذف یا clip کردن Entityهای intersectشده طبق policy مستند و shift صحیح Entityهای بعدی؛ Custom Emoji کامل حفظ شود.

## خارج از محدوده

- بازنویسی AI، ترجمه، moderation یا normalization duplicate.
- انتشار با User API یا ساخت message approval.
- resolve Username شبکه‌ای.
- ویرایش متن اصلی ذخیره‌شده یا ذخیرهٔ همهٔ خروجی‌ها در MongoDB مگر قرارداد کوچک cache لازم باشد.
- Media/Album caption delivery.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/application/content/models.py`
- `src/telegram_assist_bot/application/content/telegram_links.py`
- `src/telegram_assist_bot/application/content/entity_rebaser.py`
- `src/telegram_assist_bot/application/prepare_destination_content.py`
- `tests/unit/application/content/test_telegram_links.py`
- `tests/unit/application/content/test_entity_rebaser.py`
- `tests/unit/application/content/test_prepare_destination_content.py`

## نکات پیاده‌سازی

- جایگزینی‌ها با span editهای non-overlapping انجام شوند؛ ویرایش رشته‌ای متوالی بدون map offset مستعد corruption است.
- offset Telegram غالباً بر code unit تعریف می‌شود؛ واحد واقعی SDK T007 باید با fixture شامل astral Emoji تثبیت و در مدل مستند شود.
- **ریسک Configuration:** Username مبدا/مقصد باید validate شود؛ مقصد خالی/نامعتبر خطای validation است، نه حذف گسترده.
- **ریسک Migration:** تغییر pruning policy خروجی مقصد را تغییر می‌دهد؛ `content_policy_version` لازم است.
- **ریسک Compatibility:** Custom Emoji metadata و unknown Entity type forward-compatible بماند یا با خطای صریح رد شود؛ silently drop ممنوع.
- **ریسک Concurrency:** service pure/immutable باشد تا تولید چند مقصد هم‌زمان state مشترک نداشته باشد.
- **ریسک Security:** regex باید در برابر input طولانی ReDoS-safe و URL parser محدود باشد؛ لینک غیرتلگرامی حذف نشود.

## معیارهای پذیرش عینی

1. Username مبدا با Username هر مقصد جایگزین و همان مقصد در حذف بعدی حفظ می‌شود.
2. همهٔ patternهای الزام‌شده حذف می‌شوند، ولی لینک غیرتلگرامی و متن مشابه بی‌ربط حفظ می‌شود.
3. حذف span کلمات را نمی‌چسباند و blank line غیرعادی نمی‌سازد.
4. Entityهای قبل/بعد/داخل edit طبق policy و offset canonical درست‌اند.
5. Persian، ZWNJ، RTL، BMP/astral Emoji و Custom Emoji در fixtureهای representative سالم‌اند.
6. دو Destination خروجی مستقل می‌گیرند و original Post هیچ تغییری نمی‌کند.
7. input طولانی در زمان bounded پردازش می‌شود.

## Unit Testهای الزامی

- جدول همهٔ URL/username patternها، query/path/case و false positiveها.
- source replacement + destination protection و چند مقصد.
- whitespace/line behavior در ابتدا، میان و انتهای متن فارسی.
- Entity قبل/بعد/داخل span، adjacent/overlap و unknown type.
- UTF-16/code point conversion با Emoji astral، ZWNJ و Custom Emoji.
- immutability و determinism/reentrancy service.
- input طولانی برای جلوگیری از catastrophic regex behavior.

## Integration Testهای الزامی

N/A. این Task یک transformation خالص و بدون I/O، SDK یا persistence است؛ fixtureهای contract-like Entity در Unit Test تمام semantics را deterministic پوشش می‌دهند. Round-trip واقعی Telegram در Taskهای publication انجام می‌شود.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/application/content
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
```

بازبینی دستی diff فارسی، offset چند fixture نماینده، `git diff --check` و تأیید عدم تغییر original الزامی است.

## به‌روزرسانی‌های مستندات

- ثبت Status/verification و به‌روزرسانی T017 در `docs/ROADMAP.md` و `docs/STATUS.md`.
- افزودن transformation/entity utilities به `docs/CODE_MAP.md`.
- ثبت ترتیب عملیات، offset unit و policy intersect در `docs/ARCHITECTURE.md`.
- ثبت تصمیم offset/policy version در `docs/DECISIONS.md` اگر قبلاً تثبیت نشده است.

## تعریف انجام‌شدن

- تمام الگوها و Entity/Persian/Emoji edge caseها پاس شده‌اند.
- transformation pure، version‌شده و مستقل از SDK است.
- Quality Gate و UTF-8 پاس شده‌اند.
- original content دست‌نخورده و AI/publication خارج Scope مانده است.

