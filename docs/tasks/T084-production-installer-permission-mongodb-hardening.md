# T084 — Production Installer, Permission and MongoDB Compatibility Hardening

## وضعیت

Completed

## هدف

رفع شکست permission تولید Config و جلوگیری از اجرای MongoDB ناسازگار با Linux
Kernel 6.19+ بدون root کردن سرویس‌های Production.

## ارجاع به نیازمندی‌ها

- بخش‌های 4، 12–15 و 18 `docs/REQUIREMENTS.md`.
- ADR-008، ADR-009 و ADR-042.
- شکست‌های واقعی Ubuntu 26.04 در درخواست Milestone 9.

## وابستگی‌ها

- T082 و T083.

## محدوده

- قرارداد UID/GID قابل تنظیم و helper متمرکز permission/ownership.
- Config renderer قابل نوشتن و Runtime غیرroot قابل خواندن.
- permission مستند Config، `.env`، Session، Media، backup و metadata.
- `TAB_MONGODB_IMAGE` با default دقیق `mongo:7.0.32`.
- preflight نسخه Kernel/MongoDB و repair idempotent permission.
- حفظ انتخاب MongoDB Image در rerun/update.

## خارج از محدوده

- Config چند Admin/Source؛ T085.
- mutation عمومی Config و `tabctl` کامل؛ T086 و T087.
- backup/update/repair جامع؛ T088.
- تغییر Approval delivery؛ T089.

## فایل‌ها و ماژول‌های مورد انتظار

- `compose.yaml`، `deploy/compose.env.example`، `Dockerfile`.
- `install.sh`، `install.ps1` و helperهای محدود deployment.
- `src/telegram_assist_bot/bootstrap/` برای parser/preflight تایپ‌شده.
- تست‌های `tests/unit/deployment/` و `tests/unit/bootstrap/`.

## نکات پیاده‌سازی

- سرویس‌های Application با UID/GID غیرroot اجرا می‌شوند.
- `.env` فقط برای مالک Host قابل خواندن است.
- repair فقط metadata مالکیت/mode را تغییر می‌دهد و محتوای داده را لمس نمی‌کند.
- مقایسه Kernel و MongoDB pure و مستقل از Shell نگه داشته می‌شود.

## معیارهای پذیرش عینی

1. Renderer بدون patch دستی Config را می‌نویسد و Runtime آن را می‌خواند.
2. default Compose دقیقاً `mongo:7.0.32` و قابل override است.
3. Kernel 6.19+ با MongoDB 8 ناسازگار پیش از startup رد می‌شود.
4. root، sudo-user و Docker Desktop strategy مستند و تست‌پذیر است.
5. permission repair idempotent و content-preserving است.

## Unit Testهای الزامی

- comparison نسخه Kernel/MongoDB و پیام decision.
- mode/ownership plan و rerun.
- Compose variable/default و nonroot contract.

## Integration Testهای الزامی

- Config render/read با UID/GID Runtime.
- installer preflight سناریوهای 6.18/8، 6.19/8 و 6.19/7.0.32.

## فرمان‌های راستی‌آزمایی

- تست‌های متمرکز bootstrap/deployment.
- Ruff، format، mypy، text integrity و `git diff --check`.
- Docker acceptance در محیط دارای Docker.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، Decisions، README و troubleshooting.

## تعریف انجام‌شدن

- تمام معیارها و تست‌های قابل اجرا موفق باشند و هیچ permission دستی لازم نباشد.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- قرارداد non-root، helperهای permission، init محدود volume و
  `TAB_MONGODB_IMAGE=mongo:7.0.32` پیاده شد.
- preflight سناریوهای Kernel 6.18/MongoDB 8، Kernel 6.19/MongoDB 8 و
  Kernel 6.19/MongoDB 7.0.32 را پوشش می‌دهد.
- ۴۰ تست متمرکز موفق؛ Ruff، format، mypy، text integrity و diff check موفق.
- Docker acceptance به Workflow Ubuntu سپرده شده است، چون Docker روی Host
  توسعهٔ Windows در دسترس نبود.
