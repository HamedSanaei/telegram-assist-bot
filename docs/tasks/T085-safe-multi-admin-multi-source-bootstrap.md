# T085 — Safe Multi-admin and Multi-source Bootstrap Configuration

## وضعیت

Completed

## هدف

تولید Config مینیمال Production و پشتیبانی امن Linux/Windows از چند مدیر و چند
منبع در یک نصب.

## ارجاع به نیازمندی‌ها

- بخش‌های 4، 5.2، 5.13 و 18 `docs/REQUIREMENTS.md`.

## وابستگی‌ها

- T084.

## محدوده

- factory تایپ‌شده Config production بدون demo AI/advertisement.
- parser plural/singular Admin و Source با تقدم plural.
- normalization URL/username، validation، deduplication و UTF-8.
- interface و summary هم‌ارز در Bash و PowerShell.

## خارج از محدوده

- mutation Config نصب‌شده؛ T086.
- registry و menu سراسری؛ T087.

## فایل‌ها و ماژول‌های مورد انتظار

- `bootstrap/instance_config.py` و CLI renderer.
- `install.sh`، `install.ps1` و تست‌های Config/installer.

## نکات پیاده‌سازی

- example Config فقط نمونه Schema می‌ماند؛ production data صریح ساخته می‌شود.
- Config پیش از atomic write با `ApplicationConfig` validate می‌شود.

## معیارهای پذیرش عینی

1. Config تازه فقط Secretهای Telegram/MongoDB لازم را resolve می‌کند.
2. چند Admin/Source معتبر و canonical تولید می‌شوند.
3. ورودی نامعتبر pathدار و item-specific رد می‌شود.
4. Config موجود بدون عملیات صریح overwrite نمی‌شود.

## Unit Testهای الزامی

- parser Admin/Source، URLها، duplicateها، Persian whitespace و Config مینیمال.

## Integration Testهای الزامی

- render و load کامل Config با Environment حداقلی.

## فرمان‌های راستی‌آزمایی

- تست‌های متمرکز Config/installer و Gateهای task.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README و راهنمای نصب.

## تعریف انجام‌شدن

- نصب Linux/Windows ورودی‌های plural را یکسان و بدون demo residue تولید کند.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- parser تایپ‌شدهٔ Admin/Source، precedence plural و interface هم‌ارز دو
  Installer پیاده شد.
- Config مینیمال با فقط پنج Secret reference ضروری load شد و همهٔ demoهای
  AI/Advertisement حذف شدند.
- ۳۸ تست متمرکز اولیه و ۳۴ تست نهایی Config موفق؛ Ruff، mypy و syntax هر دو
  Installer موفق.
