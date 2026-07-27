# T080 — Production Docker Image and Compose Stack

## وضعیت

Planned

## هدف

ساخت Image تولیدی و Compose stack قابل اجرای Linux و Windows برای یک Instance
مستقل برنامه و MongoDB.

## ارجاع به نیازمندی‌ها

- هدف Release `v1.0.0` و قواعد Configuration، Secret و Deployment پروژه.
- `docs/ARCHITECTURE.md`، Composition Root و deployment topology.

## وابستگی‌ها

- T078 و T079.

## محدوده

- Dockerfile چندمرحله‌ای، user غیرroot و image حداقلی.
- Compose با Network، MongoDB، Config، Session و Media volume مستقل.
- healthcheck، startup order و shutdown signal.
- اجرای commandهای عملیاتی موجود بدون Secret در Image.

## خارج از محدوده

- Installer چندInstance؛ T081.
- GHCR pipeline، Tag یا Release؛ T082.
- تغییر منطق Domain/Application.

## فایل‌ها و ماژول‌های مورد انتظار

- `Dockerfile`، `.dockerignore` و فایل‌های `deploy/`.
- Compose production و مستندات اجرای محلی.
- تست/smoke scriptهای بدون credential واقعی.

## نکات پیاده‌سازی

- Session، Config واقعی، Mongo data و Media داخل Image کپی نمی‌شوند.
- dependencyها از `uv.lock` و build reproducible نصب می‌شوند.
- Windows هدف Docker Desktop/Linux containers است، نه Windows container.

## معیارهای پذیرش عینی

1. Image بدون Secret ساخته و با user غیرroot اجرا می‌شود.
2. Compose volumes/network مستقل و healthcheck معتبر دارد.
3. restart دادهٔ MongoDB، Session و Media را حفظ می‌کند.
4. shutdown تمیز و commandهای runtime/worker قابل انتخاب‌اند.

## Unit Testهای الزامی

- validation فایل‌های deployment و عدم وجود Secret/path محلی.

## Integration Testهای الزامی

- build، compose config، startup/health، restart و volume persistence در محیط CI
  پشتیبانی‌شده.

## فرمان‌های راستی‌آزمایی

- Docker/Compose smokeهای واقعی به‌علاوه Quality Gate کامل Python.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README و deployment guide.

## تعریف انجام‌شدن

- Image و stack قابل بازتولید، امن و verified باشند.
