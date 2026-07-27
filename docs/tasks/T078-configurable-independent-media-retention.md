# T078 — Configurable Independent Media Retention

## وضعیت

Completed

## هدف

جداکردن انقضای فایل Media از TTL چهارده‌روزهٔ Post و پنجره‌های چهارده‌روزهٔ
Duplicate، با retention پیش‌فرض دو روز، cleanup پایدار و Worker دوره‌ای.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش‌های `4`، `5.4`، `5.5`، `5.9`،
  `5.12–5.19` و `6`.
- `docs/ARCHITECTURE.md`، بخش‌های `3`، `9`، `10`، `11`، `13` و `14`.
- T014 — قرارداد فعلی Media cleanup.

## وابستگی‌ها

- T014، T029، T031، T049، T051، T054 و T077؛ همگی Completed هستند.

## محدوده

- افزودن `media.retention_days` و `media.cleanup_interval_seconds` به Config typed.
- محاسبهٔ UTC-aware انقضای Media با Clock تزریق‌شده.
- persistence و index صریح انقضای مستقل و رفتار deterministic برای دادهٔ legacy.
- recheck مرجعهای فعال و shared path پیش از حذف محصور در Media root.
- حفظ فرمان one-shot `media-cleanup` و افزودن Worker/CLI دوره‌ای.
- تست Unit و Integration برای retention، restart، concurrency و compatibility.

## خارج از محدوده

- تغییر TTL چهارده‌روزهٔ Post یا پنجرهٔ چهارده‌روزهٔ Duplicate.
- حذف پیام Telegram، پست Source یا پست Destination؛ این رفتار متعلق به T079 است.
- Dockerfile، Compose، Installer، GHCR، Tag یا Release.
- Object Storage یا refactor نامرتبط.

## فایل‌ها و ماژول‌های مورد انتظار

- `src/telegram_assist_bot/shared/config/models.py`
- `src/telegram_assist_bot/application/cleanup_expired_media.py`
- `src/telegram_assist_bot/application/runtime_ingestion.py`
- `src/telegram_assist_bot/application/ports/media.py`
- `src/telegram_assist_bot/infrastructure/persistence/mongodb/content_repository.py`
- `src/telegram_assist_bot/infrastructure/media/local_storage.py`
- `src/telegram_assist_bot/workers/media_cleanup.py`
- `src/telegram_assist_bot/bootstrap/media_cleanup.py`
- `src/telegram_assist_bot/bootstrap/cli.py`
- تست‌های متمرکز Unit و Integration.

## نکات پیاده‌سازی

- Schema Config نسخه ۱ فقط در صورت ناسازگاری Loader تغییر می‌کند؛ افزودن فیلد
  دارای default به‌تنهایی migration Config نیست.
- فیلد persistence جدید متعلق به Infrastructure است و Application نام آن را
  نمی‌شناسد.
- دادهٔ legacy باید با expiration قبلی خود cleanup‌پذیر بماند و هرگز هنگام
  startup به‌صورت نامحدود backfill نشود.
- index جدید نام پایدار دارد، idempotent ساخته می‌شود و index ناسازگار drop نمی‌شود.
- خطای candidate از batch isolate می‌شود؛ خطای حیاتی repository عبور می‌کند.

## معیارهای پذیرش عینی

1. Config قدیمی retention دو روز و interval یک ساعت می‌گیرد و مقادیر نامعتبر
   با مسیر دقیق رد می‌شوند.
2. Media جدید مستقل از `Post.expires_at` منقضی می‌شود.
3. Media دارای reference فعال یا shared reference حذف نمی‌شود.
4. پس از terminal شدن آخرین reference، candidate دوباره قابل حذف است.
5. legacy Media بدون فیلد جدید deterministic و restart-safe باقی می‌ماند.
6. one-shot CLI سازگار و Worker دوره‌ای cancellation-safe است.
7. Post TTL و Exact/Semantic Duplicate همچنان چهارده روز هستند.
8. هیچ Telegram deletion یا Docker implementation افزوده نمی‌شود.

## Unit Testهای الزامی

- default، custom و تمام type/rangeهای نامعتبر Config.
- محاسبهٔ دقیق expiration و boundary.
- referenceهای Publication، Native Schedule، Approval معتبر، shared path و
  terminal transition.
- missing file، retry محدود، worker رقیب و همهٔ path/symlink guardها.
- loop، interval، cancellation، bounded batch و CLI compatibility.

## Integration Testهای الزامی

- MongoDB آزمایشی و filesystem موقت برای تازه، منقضی، referenced و shared Media.
- legacy record، restart میان batch و دو Worker هم‌زمان.
- index initialization تکرارشونده و عدم تغییر Post TTL/Duplicate behavior.
- هیچ Telegram، credential یا سرویس Production.

## فرمان‌های راستی‌آزمایی

فرمان‌های واقعی از `pyproject.toml`، `.github/workflows/quality.yml` و `README.md`:

```powershell
uv lock --check
uv run pytest <focused-unit-tests>
uv run pytest <focused-integration-tests>
uv run pytest -m "not live" --cov=telegram_assist_bot --cov-branch --cov-report=term-missing --cov-fail-under=90
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run python scripts/check_text_integrity.py --changed
uv run python scripts/check_text_integrity.py --all
uv run detect-secrets-hook --no-verify --baseline .secrets.baseline <tracked-files>
uv build --no-build-isolation
uv run python scripts/check_distribution.py dist
git diff --check
```

## به‌روزرسانی‌های مستندات

- `docs/ROADMAP.md`، `docs/STATUS.md`، `docs/ARCHITECTURE.md`،
  `docs/CODE_MAP.md` و در صورت تصمیم ماندگار `docs/DECISIONS.md`.
- `README.md` و `config/configuration.example.json`.

## تعریف انجام‌شدن

- همهٔ معیارها و تست‌های لازم پاس شده‌اند.
- T078 در Roadmap و این فایل Completed و STATUS بدون تاریخچهٔ اضافی به‌روز است.
- diff نامرتبط یا تغییر T077 وجود ندارد و پیام Commit انگلیسی پیشنهاد شده است.

## وضعیت راستی‌آزمایی

- Unit متمرکز: موفق؛ `124 passed`.
- Integration متمرکز با MongoDB آزمایشی: موفق؛ `16 passed`.
- suite غیرزنده: همهٔ `1389` تست رفتاری موفق‌اند.
- Baseline تمیز `HEAD` نیز coverage برابر `84.09%` داشت؛ بدهی coverage
  repository-wide در T083 ثبت شده و regression اختصاصی T078 محسوب نمی‌شود.
- Ruff روی `src tests scripts`، format، mypy، lock، text integrity، build،
  clean-wheel import و `git diff --check`: موفق.
- Ruff رسمی Source پروژه روی `src tests scripts` موفق است. تعیین Scope نهایی
  ابزار مستقل `npvt-link-extractor` در T083 انجام می‌شود.
- Wheel و sdist با موفقیت ساخته و clean-wheel import شده‌اند. allowlist قدیمی
  Distribution Check که در Baseline نیز ناموفق است در T083 همگام می‌شود.
- کانفیگ‌های VPN زیر `npvt-link-extractor` و `pantegnos/output` داده عمومی عمدی
  مالک پروژه‌اند و Secret خصوصی پروژه محسوب نمی‌شوند.
- تست‌ها و Integrationهای اختصاصی T078 موفق‌اند و Task Completed است.
