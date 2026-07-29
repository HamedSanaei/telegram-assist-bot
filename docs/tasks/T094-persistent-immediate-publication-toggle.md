# T094 — Persistent Immediate Publication Toggle

## وضعیت

Completed

## هدف

تبدیل دکمهٔ انتشار فوری Approval Bot به Toggle واقعی و restart-safe: انتخاب اول
انتشار را در مقصد enqueue می‌کند و انتخاب دوم، پس از انتشار موفق، حذف همان
پیام‌های مقصد را به‌صورت پایدار درخواست می‌کند.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بندهای `5.15` تا `5.17`.

## وابستگی‌ها

- T029 تا T032.
- T077.
- T093.

## محدوده

- نگه‌داری درخواست، lease، retry و نتیجهٔ حذف روی Publication receipt در MongoDB.
- اجرای حذف فقط توسط Runtime مالک Telegram User API.
- استفادهٔ انحصاری از destination ID و message IDهای receipt موفق همان Publication.
- همگام‌سازی state و Approval card تمام مدیران پس از درخواست و نتیجهٔ حذف.
- رفتار idempotent برای پیام ازقبل‌حذف‌شده، restart و چند Worker.
- امکان انتخاب دوباره و انتشار مجدد فقط پس از تکمیل حذف قبلی.

## خارج از محدوده

- حذف پست کانال منبع.
- حذف پست مقصدی که receipt آن متعلق به انتشار فوری این برنامه نیست.
- حذف Approval messageهای Bot یا تغییر T079.
- تغییر Toggle زمان‌بندی بومی Telegram.
- migration نامحدود، تغییر Config، تغییر نسخه یا Release.
- Push، Tag، GitHub Release یا انتشار GHCR.

## فایل‌ها و ماژول‌های مورد انتظار

- Domain Publication و Application Port/Use Case حذف انتشار فوری.
- MongoDB publication repository و indexهای claim.
- Telethon User API publisher gateway.
- Approval callback executor و Runtime composition.
- تست‌های Unit و Integration مربوط به Approval، Publication و Telegram adapter.
- `docs/REQUIREMENTS.md`، `docs/ARCHITECTURE.md`، `docs/CODE_MAP.md`،
  `docs/DECISIONS.md`، `docs/ROADMAP.md` و `docs/STATUS.md`.

## نکات پیاده‌سازی

- Approval Bot هرگز Telegram User session را باز نمی‌کند و فقط درخواست durable
  را پس از CAS موفق Selection ثبت می‌کند.
- MongoDB منبع حقیقت است؛ Runtime درخواست را با lease اتمیک claim می‌کند.
- حذف فقط با receipt موفق شامل `destination_id` و `message_ids` انجام می‌شود.
- نتیجهٔ نامعلوم یا خطای transient حذف با lease و backoff محدود retry می‌شود؛
  پس از سقف تلاش، رکورد به حالت شکست پایدار می‌رود تا حذف خودکار بی‌نهایت رخ ندهد.
- رکوردهای legacy فاقد metadata حذف بدون تغییر قابل خواندن باقی می‌مانند.

## معیارهای پذیرش عینی

1. کلیک اول فوری، Publication را مانند قبل enqueue و منتشر کند.
2. کلیک دوم پس از موفقیت، درخواست حذف پایدار بسازد و UI را به حالت درحال‌حذف ببرد.
3. Runtime فقط message IDهای receipt همان مقصد را حذف و نتیجه را persist کند.
4. Restart میان request، claim و complete باعث گم‌شدن یا حذف تکراری مخرب نشود.
5. دو Callback یا Worker هم‌زمان فقط یک state معتبر و حذف idempotent بسازند.
6. پیام ازقبل‌حذف‌شده موفق محسوب شود و Permission/ambiguous failure امن بماند.
7. منبع، مقصد دیگر، Schedule و Approval message هرگز ورودی این حذف نباشند.
8. پس از تکمیل حذف، انتخاب فوری دوباره Publication جدید همان مقصد را ممکن کند.

## Unit Testهای الزامی

- درخواست حذف فقط برای Publication موفق دارای receipt.
- رد Publication pending، failed، outcome-unknown یا فاقد message ID.
- claim/lease/retry/complete و boundaryهای Clock.
- Callback مجاز، غیرمجاز، replay، race و compensation.
- rendering حالت‌های queued/deleting/deleted/failure.
- Telethon delete mapping، timeout، permission، transient و missing-message success.

## Integration Testهای الزامی

- MongoDB واقعی برای request/claim/complete، restart و دو Worker.
- مسیر Callback persisted selection تا retraction command.
- اجرای Runtime با User gateway fake و sync پایدار Approval.
- publication → delete → republish با receiptهای مستقل و بدون Telegram واقعی.

## فرمان‌های راستی‌آزمایی

```powershell
uv lock --check
uv run pytest <focused T094 unit tests> -q
uv run pytest <focused T094 MongoDB integration tests> -q
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- Task، ROADMAP، STATUS، REQUIREMENTS، ARCHITECTURE، CODE_MAP و DECISIONS.

## تعریف انجام‌شدن

Toggle فوری از Selection تا حذف پیام مقصد و انتخاب دوباره، پایدار، مجاز،
idempotent و restart-safe باشد؛ تست‌های متمرکز و Gateهای اجباری موفق باشند و
هیچ Telegram live، Push، Tag یا Release اجرا نشده باشد.

## نتیجهٔ راستی‌آزمایی

- ۱٬۸۷۷ تست non-live با branch coverage برابر `90.01%` موفق شدند.
- تست‌های متمرکز Unit و MongoDB Integration موفق شدند.
- `ruff check src tests`، `ruff format --check src tests` و
  `mypy src tests scripts` موفق شدند.
- بررسی text integrity، secrets، build، distribution و `git diff --check`
  در پایان کار اجرا و ثبت شد.
- Telegram live test اجرا نشده است؛ هیچ Push، Tag، GitHub Release یا GHCR
  publication انجام نشده است.
