# T097 — Installer Default Instance, Global Command and README UX

## وضعیت

Completed

## هدف

نصب عادی Linux به یک فرمان کوتاه بدون آرگومان (`bash <(curl -fsSL ...)`) با
instance پیش‌فرض `default` تبدیل شود؛ پس از نصب منوی مدیریت باز شود؛ فرمان
سراسری `tabctl` بدون آرگومان منو و با آرگومان CLI قبلی را اجرا کند؛ README به
جریان سادهٔ نصب/مدیریت خلاصه و مرجع پیشرفته به `docs/OPERATIONS.md` منتقل شود.

## ارجاع به نیازمندی‌ها

- Installation و Operations Release (بخش `18` و `19`).

## وابستگی‌ها

- T095، T096، T092.

## محدوده

- `install.sh`: پیش‌فرض `default`، نصب wrapper در `bin` و manager+menu در
  `lib/telegram-assist-bot`، پرچم `--no-menu`، بازکردن منو پس از نصب تعاملی.
- README: جریان ساده، ارجاع به `docs/OPERATIONS.md` و نقشهٔ فرمان‌ها.
- سازگاری: نصب موجود، `.env`، Config، volumeها و CLI قبلی بدون تغییر.

## خارج از محدوده

- تغییر قرارداد secrets و ساختار Instance؛ parity کامل PowerShell.

## فایل‌ها و ماژول‌های مورد انتظار

- `install.sh`، `deploy/tabctl.sh`، `deploy/menu.sh`، README،
  `docs/OPERATIONS.md` و تست‌های Installer.

## نکات پیاده‌سازی

- مسیرهای نصب: root → `/usr/local/bin` و `/usr/local/lib/telegram-assist-bot`؛
  کاربر → `$HOME/.local/bin` و `$HOME/.local/lib/telegram-assist-bot`.
- بازکردن منو فقط در حالت تعاملی و غیرupdate؛ با `--non-interactive` یا
  `--no-menu` یا بدون TTY خودداری شود.
- Dry-run مقدار `planned_default_instance` را چاپ کند.

## معیارهای پذیرش عینی

1. `install.sh` بدون `--instance` نصب `default` را برنامه‌ریزی کند.
2. `tabctl` بدون آرگومان منو و `tabctl status` همان رفتار قبلی را داشته باشد.
3. Installer پس از نصب تعاملی منو را باز کند.
4. تست Installer قرارداد مسیرهای جدید را تأیید کند.

## Unit Testهای الزامی

- قراردادهای جدید installer (default instance، wrapper، menu، `--no-menu`).

## Integration Testهای الزامی

- Dry-run پیش‌فرض و مسیر مدیریت در `v1_acceptance.sh`.

## فرمان‌های راستی‌آزمایی

```bash
bash -n install.sh deploy/tabctl.sh deploy/menu.sh
uv run pytest tests/unit/deployment -q
uv run python scripts/check_text_integrity.py --all
git diff --check
```

## به‌روزرسانی‌های مستندات

- README، `docs/OPERATIONS.md`، ROADMAP و STATUS.

## تعریف انجام‌شدن

نصب یک‌فرمانی با instance پیش‌فرض، منوی خودکار و `tabctl` سازگار کار کند؛
اسناد به‌روز باشند و Gateها موفق باشند.

## نتیجهٔ راستی‌آزمایی

- تست‌های Installer با قرارداد جدید (۴۰ تست deployment) موفق شدند.
- Dry-run پیش‌فرض در acceptance افزوده شد؛ اجرای Docker محلی ممکن نبود.