# T065 — Album finalization identity and failure isolation

## Status

Completed

## Goal

اصلاح هویت canonical آلبوم و جداسازی خطاهای هر Media Group، به‌گونه‌ای که یک
گروه ناقص یا malformed نتواند heartbeat، publication یا live ingestion را متوقف کند.

## Requirement references

- `docs/REQUIREMENTS.md`: بخش‌های `5.2`، `5.5` تا `5.7`، `13`، `14` و `16`.
- T013–T019، T060، T063 و T064.

## Dependencies

- T013–T019، T060، T063 و T064: Completed.

## Scope

- استخراج identity پایدار از source post، group و media member canonical.
- settle window بر پایهٔ زمان مشاهده، نه تاریخ قدیمی پیام history.
- claim/lease، retry bounded و permanent failure برای هر گروه در MongoDB.
- بازیابی backward-compatible رکوردهای قدیمی در صورت امکان.
- isolation خطای داده/محتوا و ادامهٔ گروه‌های بعدی.
- observability امن و تست استقلال publication/heartbeat/live ingestion.

## Out of scope

- تغییر publication payload یا الگوریتم schedule/approval.
- حذف یا mutation دادهٔ live موجود در تست.
- Telegram زنده، AI، تغییر config محلی یا refactor نامرتبط.

## Expected files and modules

- `src/telegram_assist_bot/application/ports/media.py`
- `src/telegram_assist_bot/application/assemble_media_group.py`
- `src/telegram_assist_bot/application/runtime_ingestion.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/content_repository.py`
- `src/telegram_assist_bot/bootstrap/text_ingestion.py`
- تنظیمات typed و example فقط در صورت نیاز retry/lease.
- تست‌های unit و MongoDB integration مرتبط.

## Implementation notes

- MongoDB منبع durable claim/retry باقی می‌ماند.
- خطاهای expected هر گروه consume و persist می‌شوند؛ خطاهای infrastructure واقعی
  از loop خارج می‌شوند تا supervisor T064 آن‌ها را تشخیص دهد.
- هیچ متن، مسیر media، exception خام یا secret در eventها ثبت نمی‌شود.

## Acceptance criteria

1. گروه ناقص پیش از settle window finalize نمی‌شود.
2. anchor از هویت معتبر source post/member و media همان گروه انتخاب می‌شود.
3. Document، Photo، Video و Animation مستقل از نوع media هویت درست دارند.
4. failure یک گروه retry/permanent state خود را تغییر می‌دهد و loop ادامه می‌یابد.
5. claim منقضی پس از restart بازیابی و finalized content تکراری نمی‌شود.
6. رکورد قدیمی قابل‌بازیابی repair منطقی می‌شود و رکورد غیرقابل‌بازیابی فقط همان گروه را fail می‌کند.
7. heartbeat/publication/live ingestion هنگام failure آلبوم فعال می‌مانند.

## Unit tests

- sequence عضو اول Document و شروع عضو بعدی پیش از settle.
- anchor canonical، member mismatch، missing post و گروه معتبر بعدی.
- retry bounded، permanent failure و eventهای امن.
- infrastructure failure propagation و ادامهٔ runtime هنگام expected failure.

## Integration tests

- MongoDB claim/lease concurrency، retry، restart، malformed legacy record و uniqueness readiness.

## Verification commands

```powershell
$env:TEST_MONGODB_URI='mongodb://127.0.0.1:27017/?directConnection=true'
uv run --python 3.12 pytest <focused album tests>
uv run --python 3.12 ruff check .
uv run --python 3.12 ruff format --check .
uv run --python 3.12 mypy src tests scripts
uv lock --check
git diff --check
uv run --python 3.12 pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-fail-under=90 --basetemp <unique>
```

## Documentation updates

- `docs/ARCHITECTURE.md`
- `docs/CODE_MAP.md`
- `docs/ROADMAP.md`
- `docs/STATUS.md`
- همین Task.

## Definition of done

- همهٔ acceptance criteria و verificationها پاس شده‌اند.
- suite کامل non-live صفر failed/error/mandatory skip و coverage حداقل ۹۰٪ دارد.
- هیچ Telegram live، config محلی، job موجود، commit یا push استفاده نشده است.
- T065 Completed و T034 دوباره تنها Active است.

## Verification results

| Check | Result |
|---|---|
| Focused unit/runtime/MongoDB tests | Pass؛ race آلبوم ناقص، Document، anchor، retry، isolation، restart و runtime independence |
| `ruff check .` | Pass |
| `ruff format --check .` | Pass |
| `mypy src tests scripts` | Pass؛ ۲۱۹ فایل |
| `uv lock --check` | Pass؛ ۴۷ package resolved |
| Complete non-live suite | Pass؛ `896 passed`، `0 skipped`، exit code `0` |
| Coverage | Pass؛ branch instrumentation فعال و total برابر `90.0486223662885%` |
| UTF-8/Persian/mojibake | Pass؛ اسکن خودکار و بازبینی متن‌های تغییرکرده |
