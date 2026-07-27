# T081 — Multi-instance Linux and Windows Installers

## وضعیت

Planned

## هدف

فراهم‌کردن نصب هدایت‌شدهٔ یک‌دستوری برای چند Instance مستقل روی Linux و Windows.

## ارجاع به نیازمندی‌ها

- هدف Release `v1.0.0` برای نصب ساده و Multi-instance.
- قرارداد Docker/Compose مصوب T080.

## وابستگی‌ها

- T080.

## محدوده

- Installerهای PowerShell و POSIX با نام Instance معتبر.
- ساخت مسیر، project name، Config، Session، Media، Mongo volume و Network مستقل.
- preflight، dry-run، upgrade-safe و پیام خطای واضح.
- جلوگیری از collision یا overwrite Instance موجود.

## خارج از محدوده

- انتشار Image و Release؛ T082.
- مدیریت cluster یا orchestrator.
- ذخیره Secret در Git یا log.

## فایل‌ها و ماژول‌های مورد انتظار

- scriptهای نصب در `deploy/installers/`.
- templateها و راهنمای Linux/Windows.
- تست‌های script و smoke چندInstance.

## نکات پیاده‌سازی

- ورودی shell هرگز مستقیم interpolate نمی‌شود.
- عملیات overwrite یا حذف نیازمند انتخاب صریح و recoverable است.
- هر Instance نام و resource prefix قطعی دارد.

## معیارهای پذیرش عینی

1. نصب هدایت‌شده با یک command روی هر دو سیستم انجام می‌شود.
2. دو Instance هم‌زمان volume/network/config/session/media مستقل دارند.
3. rerun امن است و Instance موجود را silently overwrite نمی‌کند.
4. Secret در argument، log یا فایل commitشدنی نشت نمی‌کند.

## Unit Testهای الزامی

- validation نام، path، port، quoting، rerun و dry-run.

## Integration Testهای الزامی

- smoke دو Instance روی Linux و Windows runner پشتیبانی‌شده.

## فرمان‌های راستی‌آزمایی

- script lint/test، Compose validation و Quality Gate کامل مخزن.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README و installation guide.

## تعریف انجام‌شدن

- نصب و isolation واقعی روی هر دو platform verified باشد.
