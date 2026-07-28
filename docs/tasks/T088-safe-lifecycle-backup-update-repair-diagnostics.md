# T088 — Safe Lifecycle, Backup, Update, Repair and Diagnostics

## وضعیت

Completed

## هدف

عملیات production transaction-safe برای logs، diagnostics، backup/restore،
update/rollback و repair/import.

## ارجاع به نیازمندی‌ها

- بخش‌های 12–15 و 18 و سناریوهای عملیاتی Milestone 9.

## وابستگی‌ها

- T087.

## محدوده

- logs فیلترپذیر و non-follow پیش‌فرض.
- diagnostics و export redacted.
- backup manifest/checksum و restore تأییدشده.
- update versioned با rollback Image/Config.
- repair dry-run/apply با backup و بدون حذف volume/session.

## خارج از محدوده

- backup ناامن Secret/Session.
- Caption Approval؛ T089.

## فایل‌ها و ماژول‌های مورد انتظار

- operator lifecycle adapters/services و commandهای `tabctl`.
- تست‌های Docker fake، backup/restore/update/repair.

## نکات پیاده‌سازی

- `.env` و Session هرگز در backup/diagnostics archive وارد نمی‌شوند.
- update انتخاب MongoDB Image پشتیبانی‌شده را حفظ می‌کند.

## معیارهای پذیرش عینی

1. backup verify و restore transaction اجرا می‌شوند.
2. update failure به Image/Config قبلی rollback می‌کند.
3. repair موجود v1.0.0 را بدون reinstall/data loss adopt می‌کند.
4. diagnostics هیچ Secret یا Media خصوصی ندارد.

## Unit Testهای الزامی

- manifest/checksum، redaction، version validation و repair plan.

## Integration Testهای الزامی

- update rollback، restore rollback و existing-instance repair.

## فرمان‌های راستی‌آزمایی

- تست‌های lifecycle و Gateهای task.

## به‌روزرسانی‌های مستندات

- backup، restore، update، rollback، diagnostics و migration guide.

## تعریف انجام‌شدن

- تمام عملیات lifecycle قابل بازیابی، محدود به Instance و secret-safe باشند.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- logs bounded/filterable، diagnostics redacted/export، backup manifest/
  checksum، restore confirmed، SemVer update/rollback، self-update lock check و
  repair dry-run/apply پیاده شد.
- `.env`، Session و Media از backup/diagnostics خارج‌اند و Config با Secret
  مستقیم backup نمی‌شود.
- ۱۵ تست lifecycle/registry موفق؛ Ruff و mypy موفق.
