# T082 — GHCR Release Pipeline and v1 Acceptance

## وضعیت

Planned

## هدف

انتشار Image نهایی در GHCR و اثبات پذیرش Release `v1.0.0`.

## ارجاع به نیازمندی‌ها

- هدف Release، GHCR و نسخهٔ نهایی `v1.0.0`.
- خروجی‌های T078 تا T081.

## وابستگی‌ها

- T078، T079، T080 و T081.

## محدوده

- GitHub Actions با permission حداقلی، build چندمعماری در صورت اثبات نیاز و push GHCR.
- tag/version consistency، immutable digest، provenance/SBOM در حد پشتیبانی‌شده.
- acceptance matrix Linux/Windows، multi-instance، retention و عدم حذف Source/Destination.
- release checklist و rollback مستند.

## خارج از محدوده

- ایجاد Tag یا Release پیش از تأیید صریح مالک مخزن.
- credential واقعی در fixture یا workflow.
- registry غیر GHCR.

## فایل‌ها و ماژول‌های مورد انتظار

- workflowهای `.github/workflows/`.
- release/acceptance scripts و مستندات.
- metadata نسخهٔ Package/Image.

## نکات پیاده‌سازی

- workflow pull request نباید push انجام دهد.
- انتشار فقط از ref و نسخهٔ مصوب و پس از تمام Gateها مجاز است.
- digest نهایی و نتیجهٔ smokeها در evidence ثبت می‌شوند.

## معیارهای پذیرش عینی

1. workflow بدون Secret سفارشی غیرضروری و با permission حداقلی تعریف شده است.
2. Image version `v1.0.0` و digest immutable در GHCR قابل دریافت است.
3. Matrix پذیرش تمام ویژگی‌های Release را اثبات می‌کند.
4. failure هیچ Release ناقص را latest نمی‌کند.

## Unit Testهای الزامی

- workflow policy، tag/version mapping و عدم push از PR.

## Integration Testهای الزامی

- build/pull/smoke Image و acceptance چندInstance در محیط کنترل‌شده.

## فرمان‌های راستی‌آزمایی

- GitHub workflow validation، container smoke و Quality Gate کامل مخزن.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README، release checklist و changelog.

## تعریف انجام‌شدن

- پس از تأیید صریح، GHCR Image و Release `v1.0.0` با evidence کامل منتشر شده باشد.
