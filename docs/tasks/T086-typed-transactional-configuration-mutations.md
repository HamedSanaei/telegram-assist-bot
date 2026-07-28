# T086 — Typed Transactional Configuration Mutations

## وضعیت

Completed

## هدف

مرز Python-owned برای ویرایش lockدار، atomic، validateشده و rollback-safe Config.

## ارجاع به نیازمندی‌ها

- بخش 4، 5.2، 5.13–5.16، 12–15.

## وابستگی‌ها

- T085.

## محدوده

- عملیات Admin، Source، Destination، retention، logging و validation.
- lock Instance، backup timestamped، atomic replace، mode preservation.
- restart محدود، health check و rollback failure.
- CLI پایدار و خروجی redacted.

## خارج از محدوده

- registry/menu سراسری؛ T087.
- backup MongoDB و update Image؛ T088.

## فایل‌ها و ماژول‌های مورد انتظار

- `bootstrap/operator_config.py` و CLI wiring.
- Portهای process control بدون ورود Docker به Application/Domain.
- تست‌های transaction، concurrency و UTF-8.

## نکات پیاده‌سازی

- هیچ `sed`، `jq` یا inline Python در Shell برای mutation مجاز نیست.
- حداقل یک Admin فعال و تمام referenceهای مقصد invariant هستند.

## معیارهای پذیرش عینی

1. هر mutation lock، validate، backup و atomic replace دارد.
2. restart failure Config قبلی را byte-for-byte برمی‌گرداند.
3. عملیات تکراری idempotent و concurrent corruption ناممکن است.

## Unit Testهای الزامی

- duplicate، last-admin، referenced destination، Persian و idempotency.

## Integration Testهای الزامی

- concurrent mutation، interrupted write و failed-restart rollback.

## فرمان‌های راستی‌آزمایی

- تست‌های operator Config و Gateهای task.

## به‌روزرسانی‌های مستندات

- Roadmap، Status، Architecture، Code Map، README و Decisions در صورت ADR.

## تعریف انجام‌شدن

- تمام mutationهای تعریف‌شده transactional و secret-safe باشند.

## نتیجهٔ پیاده‌سازی و راستی‌آزمایی

- mutationهای Admin، Source، Destination، retention و logging با lock،
  validation، backup و replace اتمیک پیاده شد.
- Port کنترل service failure/health را به rollback byte-for-byte متصل می‌کند.
- ۱۰ تست transaction شامل concurrency، mode/UTF-8، duplicate، last-admin،
  destination referenced، validation failure و restart rollback موفق؛ Ruff و
  mypy موفق.
