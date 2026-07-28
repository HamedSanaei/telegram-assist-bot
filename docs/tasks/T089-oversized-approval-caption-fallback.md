# T089 — Oversized Approval Caption Fallback

## وضعیت

Completed

## هدف

جلوگیری از failure دائمی Approval media هنگام عبور Caption از محدودیت Bot API.

## ارجاع به نیازمندی‌ها

- بخش‌های 5.5–5.7، 5.12–5.16 و 13.

## وابستگی‌ها

- T088.

## محدوده

- preflight limit بر مبنای representation واقعی Bot API.
- ارسال Media بدون Caption بزرگ و متن کامل به‌صورت پیام جدا.
- حفظ control card/reference و retry idempotent برای photo/video/document/album.

## خارج از محدوده

- تغییر publication مقصد مگر defect مشترک مستقلاً اثبات شود.

## فایل‌ها و ماژول‌های مورد انتظار

- application approval delivery DTO/use case.
- infrastructure Bot adapter و repository state لازم برای partial retry.
- تست‌های unit/integration Approval.

## نکات پیاده‌سازی

- متن هرگز silently truncate نمی‌شود.
- UTF-16 entity boundary و Persian/emoji حفظ می‌شود.

## معیارهای پذیرش عینی

1. limit دقیق و یک unit بیشتر رفتار درست دارند.
2. partial delivery retry duplicate مخرب نمی‌سازد.
3. control card به محتوای صحیح متصل می‌ماند.

## Unit Testهای الزامی

- boundary، Persian، emoji، photo/video/document/album.

## Integration Testهای الزامی

- retry پس از ارسال Media یا متن و پیش از control card.

## فرمان‌های راستی‌آزمایی

- تست‌های approval delivery و Gateهای task.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map و troubleshooting.

## تعریف انجام‌شدن

- Caption بزرگ دیگر permanent failure ایجاد نکند و workflow کامل بماند.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- boundary دقیق ۱۰۲۴ واحد UTF-16 و fallback بدون truncation برای Photo، Video،
  Animation، Document و preview آلبوم پیاده شد.
- partial ID در MongoDB ذخیره و پس از restart فقط متن/control card ادامه
  می‌یابد؛ publication مقصد تغییر نکرد.
- ۴۴ تست unit Approval و یک Integration واقعی MongoDB موفق؛ Ruff و mypy موفق.
