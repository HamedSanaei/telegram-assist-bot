# T090 — Production Operations Acceptance and Release Readiness

## وضعیت

Active

## هدف

تثبیت Milestone 9 با smokeهای واقعی/شبیه‌سازی‌شده، مستندات کامل و Gateهای Release.

## ارجاع به نیازمندی‌ها

- بخش 18 و سناریوهای A تا F Milestone 9.

## وابستگی‌ها

- T089.

## محدوده

- acceptance تازه‌نصب، mutation، rollback، دو Instance، import و update failure.
- Docker smoke nonroot Config render/read و MongoDB pin.
- مستندات Linux/Windows/operator/troubleshooting/migration.
- Quality Gate کامل و پیشنهاد نسخه بعدی.

## خارج از محدوده

- Telegram login/send واقعی بدون credential صریح مالک.
- Tag، Push، Image یا Release بدون تأیید صریح.

## فایل‌ها و ماژول‌های مورد انتظار

- `scripts/` acceptance، workflowهای CI، README و docs.

## نکات پیاده‌سازی

- live Telegram بخش دستی و صریح گزارش می‌شود.
- هیچ Gate یا threshold برای عبور ضعیف نمی‌شود.

## معیارهای پذیرش عینی

1. سناریوهای A تا F تا حد محیط خودکار اجرا و نتیجه ثبت شوند.
2. تمام Gateهای canonical موفق باشند.
3. migration v1.0.0 و commandهای import/repair دقیق مستند باشند.

## Unit Testهای الزامی

- contractهای Release و documentation examples.

## Integration Testهای الزامی

- Docker/Compose multi-instance smoke و failure injection.

## فرمان‌های راستی‌آزمایی

- تمام فرمان‌های Quality workflow و distribution/clean-wheel checks.

## به‌روزرسانی‌های مستندات

- تمام حافظه پروژه، checklist و راهنماهای نصب/عملیات.

## تعریف انجام‌شدن

- Milestone runnable، documented، secret-free و با Gate کامل سبز باشد.

## نتیجهٔ فعلی راست‌آزمایی

- سناریوهای typed configuration، rollback، registry/import، دو هویت مستقل،
  update failure و Approval fallback با تست‌های واحد و Integration موفق‌اند.
- Suite غیرزنده: `1855 passed` با Branch Coverage برابر `90.14%`.
- lock، Ruff، format، mypy، text integrity، detect-secrets، build،
  Distribution check، clean-wheel import و syntax اسکریپت‌ها موفق‌اند.
- اجرای واقعی `scripts/v1_acceptance.sh` و Docker/Compose multi-instance smoke
  روی این میزبان Windows ممکن نبود، چون Docker/Compose نصب نیست؛ این Gate باید
  در Ubuntu CI اجرا شود. بنابراین Task تا دریافت نتیجهٔ CI فعال می‌ماند.
- Release آماده‌شده `1.1.0` است و default Image نصب تازه
  `ghcr.io/hamedsanaei/telegram-assist-bot:1.1.0` است؛ import نصب قدیمی Image
  `1.0.0` را تا update صریح حفظ می‌کند.
- Job `Quality / Docker and installer acceptance` نسخهٔ Docker/Compose را ثبت،
  Image را build و acceptance واقعی دو Instance و فرمان‌های مدیریتی را اجرا
  می‌کند؛ روی failure لاگ/diagnostics جمع و در همهٔ مسیرها منابع تست پاک می‌شوند.
- تست custom-path با نام `kingofilter` و basename برابر `admin1` حفظ `.env`،
  Config، Session، Compose project و volume declarations را در repair/update/
  rollback بررسی می‌کند.
- Telegram login/send واقعی عمداً بدون credential صریح مالک اجرا نشده است.
- Run شمارهٔ `30327861603` تأیید کرد Job
  `Quality / Docker and installer acceptance` موفق است. هر سه Job ماتریس
  Python فقط در secret-detection شکست خوردند: fixture تست diagnostics یک URI
  Basic Auth جعلی برای اثبات redaction دارد و scanner روی Ubuntu آن را finding
  جدید تشخیص داد. همان literal تستی با allowlist درون‌خطی و محدود علامت‌گذاری
  شد و line number قدیمی Baseline با اسکن UTF-8 کامل refresh شد؛ هیچ detector،
  مسیر اسکن یا Gate تضعیف نشده است. T090 تا سبزشدن اجرای بعدی CI فعال می‌ماند.

## توالی upgrade مورد انتظار

```bash
tabctl instance import \
  --path /opt/telegram-assist-bot/instances/admin1 \
  --name kingofilter
tabctl --instance kingofilter repair --dry-run
tabctl --instance kingofilter repair --apply
tabctl --instance kingofilter backup create
tabctl --instance kingofilter update --check
tabctl --instance kingofilter update --version 1.1.0
tabctl --instance kingofilter status
```

Rollback صریح:

```bash
tabctl --instance kingofilter update --rollback
```
