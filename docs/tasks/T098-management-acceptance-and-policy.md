# T098 — Management Acceptance Coverage, CI and Attribution Policy

## وضعیت

Completed

## هدف

پوشش acceptance مسیر مدیریت (منو، backup/restore، destroy→restore)، گنجاندن
فایل‌های Shell جدید در syntax-check CI و تکمیل سیاست ماندگار ناشناس‌بودن
عامل‌های AI در `AGENTS.md`.

## ارجاع به نیازمندی‌ها

- Quality Gates و سیاست هویت (بخش `16` تا `20` قواعد مخزن).

## وابستگی‌ها

- T095، T096، T097.

## محدوده

- `scripts/v1_acceptance.sh`: actionهای یک‌بارمصرف منو، `session/env/config set`،
  backup رمزنگاری‌شده roundtrip و سناریوی full backup → destroy → restore.
- `.github/workflows/quality.yml`: افزودن `deploy/menu.sh` و `deploy/tabctl.sh`
  به `bash -n`.
- `AGENTS.md` بند ۲۰: عامل‌ها ابزار پیاده‌سازی‌اند نه نویسنده؛ منع هرگونه
  attribution در commit، trailer، badge، metadata و لیست مشارکت‌کنندگان؛
  منع commit صرفاً برای ثبت فعالیت عامل؛ تاریخچهٔ انسانی هرگز عامل AI را
  مشارکت‌کنندهٔ انسانی نشان ندهد.

## خارج از محدوده

- تغییر رفتار CI موجود و کاهش پوشش/قواعد Secret.

## فایل‌ها و ماژول‌های مورد انتظار

- `scripts/v1_acceptance.sh`، `.github/workflows/quality.yml`، `AGENTS.md`.

## نکات پیاده‌سازی

- acceptance بدون Telegram واقعی؛ fixtureهای test-only؛ بدون prune سراسری.
- بررسی عدم درز credential در خروجی منو در acceptance.

## معیارهای پذیرش عینی

1. هر action منو در acceptance خروجی مورد انتظار را بدهد.
2. restore پس از destroy شامل health check و تطابق Config باشد.
3. `bash -n` شامل هر دو فایل جدید باشد.
4. بند ۲۰ شامل هر هشت بند سیاست الزامی باشد.

## Unit Testهای الزامی

- وجود عبارات قرارداد acceptance/سیاست در تست‌های موجود Installer/Menu.

## Integration Testهای الزامی

- کل سناریوی مدیریت در `v1_acceptance.sh` (اجرا در CI).

## فرمان‌های راستی‌آزمایی

```bash
bash -n scripts/v1_acceptance.sh deploy/menu.sh deploy/tabctl.sh
uv run pytest tests/unit/deployment -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --all
git diff --check
```

## به‌روزرسانی‌های مستندات

- `AGENTS.md`، ROADMAP و STATUS.

## تعریف انجام‌شدن

acceptance مسیر مدیریت کامل باشد، CI فایل‌های Shell جدید را بررسی کند و
سیاست هویت در `AGENTS.md` ماندگار باشد.

## نتیجهٔ راستی‌آزمایی

- `bash -n` روی همهٔ فایل‌های Shell موفق شد؛ تست‌های deployment موفق شدند.
- Docker acceptance کامل در helper لینوکس ایزوله با daemon disposable و
  `vfs` storage driver موفق شد؛ خروجی نهایی `v1.1 acceptance checks passed`.
- PowerShell acceptance داخل helper لینوکس اجرا و موفق شد؛ `shellcheck` روی
  میزبان Windows موجود نبود و اجرا نشد.