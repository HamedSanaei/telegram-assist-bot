# T087 — Global Interactive Multi-instance `tabctl`

## وضعیت

Completed

## هدف

مدیر سراسری cross-platform برای registry، discovery و کنترل چند Instance.

## ارجاع به نیازمندی‌ها

- بخش‌های 9، 12–15 و 18.

## وابستگی‌ها

- T086.

## محدوده

- metadata نسخه‌دار و registry atomic بدون Secret.
- list/show/select/import/unregister و custom path.
- interactive menu و commandهای noninteractive پایدار.
- نصب PATH در Linux root/user و Windows.
- سازگاری `manage.sh` و `manage.ps1`.

## خارج از محدوده

- transactionهای lifecycle پیشرفته؛ T088.

## فایل‌ها و ماژول‌های مورد انتظار

- package operator/registry، entry point `tabctl` و wrapperهای platform.
- تست‌های registry، import، menu و multi-instance.

## نکات پیاده‌سازی

- unregister هیچ container/data را حذف نمی‌کند.
- basename مسیر هویت Instance نیست.

## معیارهای پذیرش عینی

1. manager تمام Instanceهای ثبت‌شده و custom path را کشف می‌کند.
2. metadata atomic، versioned و secret-free است.
3. menu و commandهای پایدار exit code قراردادی دارند.

## Unit Testهای الزامی

- duplicate name/path، metadata invalid و dispatch command.

## Integration Testهای الزامی

- import نصب موجود و کنترل دو Instance مستقل.

## فرمان‌های راستی‌آزمایی

- تست‌های tabctl/registry و Gateهای task.

## به‌روزرسانی‌های مستندات

- تمام راهنماهای manager و exampleهای الزامی.

## تعریف انجام‌شدن

- `tabctl` روی Linux/Windows نصب و registry را بدون Docker knowledge مدیریت کند.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- registry/metadata اتمیک و secret-free، import مسیر custom، list/show/select/
  unregister، menu و lifecycle/log/config commands پیاده شد.
- installerها manager را در PATH platform نصب و Instance را ثبت می‌کنند.
- Admin/Source/Destination/retention از Image به transaction تایپ‌شده dispatch
  می‌شوند.
- ۱۸ تست registry، import، dispatch و transaction موفق؛ Ruff و mypy موفق.
