# T058 — Secret مستقیم در Local Config

## وضعیت

`Completed`

## هدف

اجازه‌دادن به اجرای محلی و تست با Secretهای مستقیم در فایل Configuration
ignore‌شده، بدون تغییر مسیر امن Environment/Secret Manager برای Production و
بدون نشت مقدار Secret در Model، Log یا خطا.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `4` و `14`.
- `docs/ARCHITECTURE.md`، بخش `13`.
- `docs/DECISIONS.md`، ADR-008 و ADR-009.

## وابستگی‌ها

- `T002` باید Completed باشد.

## دامنه کار

- پذیرش literal مستقیم برای Secretهای پشتیبانی‌شده فقط در Local Config.
- نگه‌داشتن `ApplicationConfig` فاقد plaintext و `ResolvedSecrets` redacted.
- مستندکردن workflow سادهٔ Local Config و حفاظت Git.

## خارج از دامنه

- رمزگذاری فایل محلی، Secret Manager جدید، Dynamic reload و تغییر CLI default.
- تغییر Provider، Adapter شبکه‌ای یا Schema version Configuration.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/shared/config/loader.py`
- `tests/unit/shared/config/test_secret_resolution.py`
- `README.md` و اسناد معماری/تصمیم

## نکات پیاده‌سازی

- فقط نام‌های `configuration.local.json` و `configuration.<profile>.local.json`
  literal مستقیم می‌پذیرند.
- `api_id` literal عدد صحیح است؛ دیگر Secretها رشتهٔ غیرخالی‌اند؛ `api_key` اختیاری
  می‌تواند `null` باقی بماند.
- literal پیش از Model validation به reference داخلی opaque تبدیل می‌شود.

## معیارهای پذیرش عینی

1. تمام Secretهای فعلی از Local Config بدون Environment resolve می‌شوند.
2. literal در Config غیرمحلی با `inline_secret_not_allowed` رد می‌شود.
3. هیچ plaintext در `repr`، Exception، Log یا `ApplicationConfig` دیده نمی‌شود.
4. Environment referenceهای موجود و Config نمونه سازگار باقی می‌مانند.

## تست‌های واحد الزامی

- موفقیت همهٔ literalهای پشتیبانی‌شده در Local Config.
- رد مسیر غیرمحلی، type نامعتبر و مقدار خالی بدون نشت sentinel.
- حفظ `null` اختیاری برای API key Provider و regression Bootstrap.

## تست‌های یکپارچه‌سازی الزامی

`N/A`: Loader و Bootstrap با fakeهای موجود بدون شبکه یا MongoDB پوشش داده می‌شوند.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/unit/shared/config tests/unit/test_bootstrap.py
uv run pytest tests/unit
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv lock --check
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- افزودن Task به Roadmap و همگام‌سازی Status پس از تکمیل.
- به‌روزرسانی README، Architecture، Code Map و ADRهای Secret.

## نتایج راستی‌آزمایی

- **Verified on:** 2026-07-12
- **Toolchain:** `uv 0.11.28` و CPython `3.14.5`.
- **Integration tests:** `N/A` برای این تغییر؛ Loader و Bootstrap با fakeهای
  موجود هیچ اتصال شبکه یا MongoDB ندارند.

| Command or check | Result |
|---|---|
| `uv run pytest tests/unit/shared/config tests/unit/test_bootstrap.py` | Pass؛ ۱۸۳ تست |
| `uv run pytest tests/unit` | Pass؛ ۷۵۶ تست |
| `uv run ruff check .` و `uv run ruff format --check .` | Pass |
| `uv run mypy src tests scripts` | Pass؛ ۲۰۲ فایل |
| `uv run python scripts/check_text_integrity.py --changed` | Pass |
| `uv lock --check` | Pass |
| Build و `check_distribution.py dist` | Pass روی Python 3.14 |
| scan مستقیم `detect-secrets` برای fixture جدید | Pass؛ baseline خالی ماند |
| `git diff --check` و بازبینی UTF-8/Mojibake | Pass |

## تعریف انجام‌شدن

همهٔ معیارهای پذیرش و Quality Gateها پاس شده، فایل Local Config همچنان ignore
است، مقادیر مستقیم در هیچ خروجی قابل مشاهده نشت نمی‌کنند و T034 دوباره تنها Task
فعال می‌شود.
