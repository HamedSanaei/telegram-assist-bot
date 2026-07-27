# T079 — Expired Approval Message Cleanup

## وضعیت

Planned

## هدف

حذف امن پیام‌های Approval ساخته‌شده توسط Bot پس از انقضا، بدون حذف هیچ پیام
Source یا Destination.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش‌های `5.12–5.19`.
- `docs/ARCHITECTURE.md`، بخش‌های Telegram Bot API، Approval و Workerها.

## وابستگی‌ها

- T078.

## محدوده

- تعریف expiration و state پایدار cleanup برای Approval referenceها.
- حذف فقط message IDهای ثبت‌شدهٔ Bot در chat مدیریتی.
- claim/lease، retry محدود، idempotency و restart recovery.
- revalidation مجوز و هویت chat/reference پیش از حذف.

## خارج از محدوده

- حذف پیام Source یا Destination.
- تغییر Media/Post retention.
- Docker، Installer یا Release.

## فایل‌ها و ماژول‌های مورد انتظار

- قراردادهای Approval در Domain/Application.
- Adapterهای Bot API و MongoDB مربوط.
- Worker و Composition Root اختصاصی.
- تست‌های Unit/Integration با Bot fake.

## نکات پیاده‌سازی

- حذف Telegram side effect غیرقابل‌برگشت است و request boundary صریح می‌خواهد.
- نتیجهٔ مبهم نباید با retry کور باعث رفتار نادرست شود.
- raw Telegram error یا محتوای پیام در Log ذخیره نمی‌شود.

## معیارهای پذیرش عینی

1. فقط Approval messageهای ساخته‌شده توسط Bot و منقضی حذف می‌شوند.
2. Source/Destination messageها هرگز target نیستند.
3. Worker رقیب و restart حذف تکراری مخرب ایجاد نمی‌کند.
4. خطای موقت bounded retry و خطای دائمی state امن دارد.

## Unit Testهای الزامی

- expiration boundary، ownership، authorization، idempotency و outcome unknown.
- retry، cancellation و redaction.

## Integration Testهای الزامی

- MongoDB آزمایشی با Bot gateway مصنوعی، restart و دو Worker.
- assertion صریح عدم هدف‌گیری Source/Destination IDs.

## فرمان‌های راستی‌آزمایی

- focused Unit/Integration، suite غیرزنده، coverage، Ruff، mypy، text integrity،
  detect-secrets، build/distribution و `git diff --check`.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README و ADR در صورت تصمیم ماندگار.

## تعریف انجام‌شدن

- معیارها و Gateها پاس و رفتار حذف فقط به Approval Bot محدود شده باشد.
