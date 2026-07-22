# T046 — Stabilization کامل Pipeline AI

## وضعیت

Completed

## هدف

تثبیت سناریوهای بین‌لایه‌ای Pipeline AI تکمیل‌شده در T034–T045، به‌ویژه Restart، هم‌زمانی، Fallback، Cache و شکست نهایی؛ بدون افزودن Feature AI جدید.

## ارجاع به نیازمندی‌ها

- `docs/REQUIREMENTS.md`، بخش `11.19` «معیارهای پذیرش پایپ‌لاین AI».
- `docs/REQUIREMENTS.md`، بخش‌های `12`، `13`، `14` و `15` برای observability، retry، security و tests.
- `docs/ARCHITECTURE.md`، بخش‌های `12`، `14` و `15`.

## وابستگی‌ها

- T040 تا T045، همگی باید `Completed` باشند.

## دامنه

- ساخت Matrix پذیرش محدود و Trace هر معیار `11.19` به تست/Task مالک.
- Integration/contract test برای route چند Provider/Model با Fake server و پاسخ‌های fixtureشده.
- سناریوهای Restart پس از Claim، Lease expiry، completion تکراری و چند Worker.
- سناریوهای invalid response، bounded Repair، Retry، Fallback، 429/Cooldown، Circuit و all-failed.
- سناریوهای Cache invalidation و Prompt/Schema version، Audit/Metrics و Secret redaction.
- سناریوهای featureهای T042–T045 فقط در حد اتصال به Pipeline.
- رفع اشکال‌های کوچک و مستقیم همان Milestone که تست‌ها آشکار می‌کنند.

## خارج از دامنه

- Provider یا Model جدید، تماس زنده AI و تصمیم درباره Providerهای واقعی.
- Featureهای خلاصه‌سازی، بازنویسی، ترجمه، topic یا title.
- refactor گسترده، auto-routing، dashboard یا performance tuning عمومی.
- تغییر سیاست محصولی حل‌نشده تبلیغ/Duplicate/scoring.

## فایل‌ها و ماژول‌های مورد انتظار

- `tests/integration/ai/test_ai_pipeline_acceptance.py`
- `tests/integration/ai/test_ai_pipeline_restart.py`
- `tests/integration/ai/test_ai_pipeline_concurrency.py`
- `tests/contract/ai/` و fixtureهای Sanitized لازم
- اصلاح‌های محدود در ماژول‌های موجود زیر `src/telegram_assist_bot/application/ai/`، `src/telegram_assist_bot/infrastructure/` و `src/telegram_assist_bot/workers/`

## نکات پیاده‌سازی

- **Configuration:** fixtureها باید Provider/Model خیالی و Secret placeholder داشته باشند؛ Configuration production یا Default جدید تغییر نکند.
- **Migration:** این Task Migration جدید طراحی نمی‌کند؛ شکست Migration/Index موجود فقط با اصلاح کوچک همان قرارداد حل می‌شود، و تغییر بزرگ به Task جدا تبدیل می‌گردد.
- **Compatibility:** contract testها مدل داخلی و payloadهای fixtureشده T036/T037 را تثبیت کنند، نه جزئیات تصادفی پیاده‌سازی.
- **Concurrency:** تست چند Worker باید MongoDB واقعی آزمایشی، Lease و update اتمیک را بسنجد؛ lock process-local کافی نیست.
- **Security:** fixture/log snapshotها برای API key، token، Authorization و URL حساس اسکن شوند.
- تست زنده Provider در Suite پیش‌فرض ممنوع و تست‌ها باید deterministic و دارای timeout باشند.

## معیارهای پذیرش عینی

1. هر بند `docs/REQUIREMENTS.md` بخش `11.19` به تست پاس‌شده یا محدودیت مستند نگاشت شده است.
2. Provider disabled/unsupported فراخوانی نمی‌شود و ترتیب Config رعایت می‌شود.
3. invalid response، خطای موقت/دائمی، 429 و Circuit مسیر مورد انتظار را طی می‌کنند.
4. all-failed هیچ نتیجه جعلی تولید نمی‌کند و policy هر Task قابل مشاهده است.
5. Restart/Lease expiry باعث گم‌شدن یا اجرای هم‌زمان یک Job نمی‌شود.
6. Cache hit تماس خارجی ندارد و تغییر Prompt/Schema آن را invalid می‌کند.
7. Audit/Metrics درست و Secret-safe هستند.
8. Featureهای تبلیغ، semantic duplicate، categorization و scoring از همان Pipeline مشترک استفاده می‌کنند.
9. هیچ تست لازم skip/xfail نشده و Suite به شبکه عمومی وابسته نیست.

## Unit Testهای الزامی

- Unit Test جدید فقط برای Bug fix کوچک کشف‌شده الزامی است و باید regression دقیق آن را پوشش دهد.
- در نبود Bug در pure logic، Unit Test جدید `N/A` است؛ دلیل: هدف Task تثبیت رفتار بین‌لایه‌ای موجود است، نه افزودن منطق واحد تازه. Unit Suite کامل موجود همچنان باید اجرا و پاس شود.

## Integration Testهای الزامی

- acceptance matrix چند Provider/Model و failure taxonomy.
- Restart/lease/concurrent worker با MongoDB آزمایشی.
- Cache/audit/metrics و redaction.
- اتصال چهار Feature AI با Gatewayهای Fake.
- Contract fixtureهای Provider بدون credential واقعی.

## فرمان‌های راستی‌آزمایی

```powershell
uv run pytest tests/integration/ai tests/contract/ai
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python scripts/check_text_integrity.py --changed
git diff --check
```

Log fixtureها و Diff فارسی باید دستی بازبینی شوند.

## بروزرسانی مستندات الزامی

- ثبت Matrix و نتایج واقعی در همین Task.
- بروزرسانی `docs/ROADMAP.md`، `docs/STATUS.md` و `docs/CODE_MAP.md`.
- اصلاح `docs/ARCHITECTURE.md` فقط برای رفع تفاوت واقعی با Pipeline پیاده‌شده.
- ابهام‌های حل‌نشده باید باقی بمانند یا به Task/Decision صریح تبدیل شوند.

## تعریف Done

- تمام معیارهای `11.19` با شواهد تستی پوشش دارند و Quality Gateها پاس‌اند.
- Restart/concurrency/security بدون شبکه یا Secret واقعی اثبات شده‌اند.
- فقط اشکال‌های محدود همان Pipeline رفع شده و Feature جدیدی ساخته نشده است.

## نتایج راستی‌آزمایی و ماتریس پذیرش

### ماتریس تطابق معیارهای نیازمندی‌های ۱۱.۱۹:

| کد معیار | خلاصه معیار (REQUIREMENTS 11.19) | پیاده‌سازی مالک | فایل تست دقیق | نام تست دقیق | وضعیت | ملاحظات / محدودیت |
|---|---|---|---|---|---|---|
| 11.19.1 | تعریف چند Provider در کانفیگ | `AiConfig` / `AiProviderConfig` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_provider_routing_disabled_and_fallback` | PASS | None |
| 11.19.2 | تغییر ترتیب Providerها در کانفیگ | `select_route_candidates` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_provider_routing_disabled_and_fallback` | PASS | None |
| 11.19.3 | عدم فراخوانی Providerهای غیرفعال | `select_route_candidates` / `text_ingestion.py` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_provider_routing_disabled_and_fallback` | PASS | None |
| 11.19.4 | انتخاب فقط Providerهای پشتیبان عملیات | `select_route_candidates` | `tests/unit/infrastructure/ai/test_model_capabilities.py` | `test_deepseek_models_support_all_tasks` | PASS | None |
| 11.19.5 | Fallback به Provider بعدی در صورت خطا | `ExecuteAIWithFallback` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_provider_routing_disabled_and_fallback` | PASS | None |
| 11.19.6 | Fallback در صورت پاسخ نامعتبر | `ExecuteAIWithFallback` / `ResponseValidator` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_transient_retry_and_failures` | PASS | None |
| 11.19.7 | Retry محدود داخلی هر Provider | `execute_candidate_with_retry` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_transient_retry_and_failures` | PASS | None |
| 11.19.8 | Cooldown برای خطای 429 | `ProviderGuard` | `tests/unit/application/ai/test_provider_guard.py` | `test_guard_applies_rate_limit_cooldown` | PASS | None |
| 11.19.9 | Circuit Breaker مستقل برای هر Provider | `ProviderGuard` | `tests/unit/application/ai/test_provider_guard.py` | `test_guard_opens_circuit_on_failures` | PASS | None |
| 11.19.10 | Fallback بین مدل‌های مختلف یک Provider | `ExecuteAIWithFallback` | `tests/integration/ai/test_ai_fallback_pipeline.py` | `test_fallback_pipeline_integration_with_mongodb` | PASS | None |
| 11.19.11 | تبدیل خروجی همه به مدل یکسان | `ResponseValidator` / `ResponseNormalizer` | `tests/contract/ai/test_ai_contract.py` | `test_schema_contracts` | PASS | None |
| 11.19.12 | ثبت واضح شکست همه Providerها | `ExecuteAIWithFallback` | `tests/integration/ai/test_ai_fallback_pipeline.py` | `test_fallback_pipeline_all_providers_failed_persists_properly` | PASS | None |
| 11.19.13 | عدم تولید نتیجه جعلی به نام AI | `AIWorker` / `ExecuteAIWithFallback` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_pipeline_acceptance_flow` | PASS | None |
| 11.19.14 | بازیابی Jobها پس از Restart | `MongoAIJobRepository` | `tests/integration/ai/test_ai_pipeline_restart.py` | `test_ai_pipeline_lease_recovery_after_restart` | PASS | None |
| 11.19.15 | عدم اجرای هم‌زمان Job توسط چند Worker | `MongoAIJobRepository` atomic update | `tests/integration/ai/test_ai_pipeline_concurrency.py` | `test_concurrent_worker_claims_are_exclusive` | PASS | None |
| 11.19.16 | دریافت نتایج تکراری از Cache | `MongoAICacheRepository` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_cache_policy_and_invalidation` | PASS | None |
| 11.19.17 | نسخه‌بندی Promptها | `PromptRegistry` / `build_ai_cache_identity` | `tests/integration/ai/test_ai_pipeline_acceptance.py` | `test_ai_cache_policy_and_invalidation` | PASS | None |
| 11.19.18 | ثبت آمار موفقیت و خطای Providerها | `MongoProviderMetricsRepository` | `tests/unit/application/ai/test_provider_metrics.py` | `test_metrics_increment_atomic` | PASS | None |
| 11.19.19 | عدم قرارگیری API Keyها در لاگ و کد | `SecretReference` / `MaskedSecret` | `tests/unit/test_security_redaction.py` | `test_no_secrets_in_logs_or_fixtures` | PASS | None |
| 11.19.20 | تعیین رفتار شکست نهایی از کانفیگ | `AiTaskFailurePolicyConfig` | `tests/integration/workflows/test_advertisement_detection.py` | `test_advertisement_success_failure_concurrency_and_legacy_compatibility` | PASS | None |

### نتایج نهایی تست‌ها:
- تمام ۱۲۳۶ تست فاز ۵ (شامل ۱۲ تست متمرکز T046) با موفقیت کامل پاس شدند.
- ابزار Ruff linter و Ruff formatter کاملاً سبز هستند (`All checks passed`).
- تحلیلگر ایستا Mypy بدون خطا اجرا شد (`Success: no issues found`).
