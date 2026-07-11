# T033 — Stabilization زمان‌بندی و Restart

## وضعیت

`Planned`

## هدف

تثبیت end-to-end محدود جریان زمان‌بندی T030 تا T032 با MongoDB، Fake Clock و Publisher Fake در سناریوهای چند مقصد، چند Worker، Restart و Cancellation؛ بدون افزودن قابلیت زمان‌بندی تازه.

## ارجاع‌های نیازمندی و معماری

- `docs/REQUIREMENTS.md`، بخش‌های `5.18 انتشار زمان‌بندی‌شده` و `5.19 لغو زمان‌بندی`.
- `docs/REQUIREMENTS.md`، بخش `16 معیارهای پذیرش فاز اول`، بندهای `18` تا `21`.
- `docs/ARCHITECTURE.md`، بخش‌های `9`، `11`، `14` و `15`.

## وابستگی‌ها

- `T031` و `T032` باید کامل شده باشند.

## دامنه کار

- سناریوی رزرو چند Post در دو Destination و اثبات فاصله/استقلال.
- Crash پس از Claim، Restart، lease expiry و ادامه امن.
- رقابت چند Worker و اتصال به Publication idempotency T029.
- Cancel پیش از due و رقابت Cancel/Claim با هر دو سیاست صف مصوب.
- بررسی Sync وضعیت مدیران پس از Schedule/Cancel/Complete در حد قراردادهای موجود.
- رفع فقط Regressionهای همین Milestone با تست متمرکز.

## خارج از دامنه

- Feature جدید، تقویم، زمان دستی، اولویت صف یا UI جدید.
- تبلیغات زمان‌بندی‌شده Milestone 6.
- تماس زنده Telegram و تست زمان واقعی مبتنی بر sleep.
- Refactor گسترده خارج از اشکال‌های T030 تا T032/T029.

## فایل‌ها و ماژول‌های مورد انتظار

- `tests/integration/scheduling/test_scheduling_end_to_end.py`
- `tests/integration/scheduling/test_scheduling_restart.py`
- `tests/integration/scheduling/test_scheduling_cancellation_race.py`
- Fixtureهای Clock/Publisher در `tests/fakes/`.
- فقط فایل‌های Taskهای وابسته که رفع Regression مستند لازم دارد.

## نکات پیاده‌سازی و ریسک‌ها

- **Configuration:** Config تست باید interval/lease کوتاه ولی معتبر و deterministic داشته باشد؛ Config تولیدی خوانده نشود.
- **Migration:** تست هم پایگاه تمیز و هم upgrade Schema قبلی را پوشش دهد؛ این Task Schema feature جدید نمی‌سازد.
- **Compatibility:** رفتار status/idempotency میان Worker و Repository به Contract عمومی آزموده شود، نه جزئیات query.
- **Concurrency:** از barrier و Fake Clock استفاده شود؛ sleep و assertion زمانی شکننده ممنوع است.
- **Security:** Fixtureها فاقد Session/Token واقعی باشند و payload مدیران در انتشار مقصد نشت نکند.

## معیارهای پذیرش عینی

1. Slotهای هر Destination مستقل و با فاصله Configured هستند.
2. Restart صف را از MongoDB بازیابی و هیچ Job موفقی را دوباره منتشر نمی‌کند.
3. چند Worker حداکثر یک Publication موثر برای هر Job دارند.
4. Cancel موفق از انتشار جلوگیری و سیاست صف مصوب را حفظ می‌کند.
5. Raceهای Cancel/Claim و lease expiry فقط outcomeهای تعریف‌شده می‌سازند.
6. هیچ Feature خارج از Milestone 4 افزوده نشده است.

## تست‌های واحد الزامی

- `N/A` برای رفتار جدید؛ Task از نوع Stabilization است.
- هر باگ کشف‌شده باید Regression unit test در نزدیک‌ترین ماژول T029 تا T032 داشته باشد.

## تست‌های یکپارچه‌سازی الزامی

- end-to-end رزرو → due → claim → publish → complete برای متن و یک Media fixture.
- Crash/Restart و lease expiry با Fake Clock.
- رقابت چند Worker و رقابت Cancellation در هر سیاست مصوب.

## فرمان‌های راستی‌آزمایی

پیش از اعلام Done، diff متن‌های فارسی، RTL، Emoji و پیام‌های Telegram باید به‌صورت انسانی بازبینی شود.

```powershell
uv run pytest tests/integration/scheduling/test_scheduling_end_to_end.py
uv run pytest tests/integration/scheduling/test_scheduling_restart.py tests/integration/scheduling/test_scheduling_cancellation_race.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

## به‌روزرسانی مستندات

- ثبت جریان واقعی Schedule/Worker/Cancel در `docs/CODE_MAP.md`.
- اصلاح `docs/ARCHITECTURE.md` فقط برای اختلاف اثبات‌شده طرح و اجرا.
- ثبت نتیجه معیارهای بندهای `16.18` تا `16.21` در همین فایل و وضعیت پروژه.
- به‌روزرسانی `docs/ROADMAP.md` و `docs/STATUS.md`.

## تعریف Done

Task زمانی Done است که جریان زمان‌بندی/Restart/Cancellation با تست‌های deterministic و MongoDB واقعی آزمایشی پاس، Regressionها محدود و رفع، همه Quality Gateها موفق و Milestone 4 بدون Feature اضافی تثبیت شده باشد.
