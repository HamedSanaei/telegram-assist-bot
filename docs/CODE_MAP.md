# نقشه کد پیشنهادی

## وضعیت

پیاده‌سازی هنوز آغاز نشده است. این ساختار پیشنهادی است، نه گزارش فایل‌های موجود. پس از پایان **هر Task**، این سند باید با مسیرهای واقعی افزوده، حذف یا جابه‌جا‌شده به‌روزرسانی و توضیحات منسوخ حذف شود.

## ساختار سطح بالا

```text
.
├── AGENTS.md
├── REQUIREMENTS.md
├── pyproject.toml
├── config/
│   └── configuration.example.json
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   ├── STATUS.md
│   ├── CODE_MAP.md
│   ├── DECISIONS.md
│   └── tasks/
├── src/
│   └── telegram_assist_bot/
│       ├── domain/
│       ├── application/
│       ├── infrastructure/
│       ├── presentation/
│       ├── workers/
│       └── bootstrap/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── e2e/
│   └── fixtures/
├── scripts/
└── var/                    # runtime-only; ignored by Git
    ├── media/
    └── sessions/
```

## مسئولیت مسیرها

| مسیر | مسئولیت |
|---|---|
| `src/telegram_assist_bot/domain/` | Entity، Value Object، State Transition و Domain Service خالص؛ بدون SDK/DB |
| `src/telegram_assist_bot/application/` | Use Case، DTO و Portهای ورودی/خروجی |
| `src/telegram_assist_bot/infrastructure/config/` | بارگذاری/Validation تنظیمات و Secret reference |
| `src/telegram_assist_bot/infrastructure/mongodb/` | Client، Index setup، Mapper و Adapterهای Repository/Job |
| `src/telegram_assist_bot/infrastructure/telegram_user/` | Session، Crawl/Listener، دریافت Media و Publication با User API |
| `src/telegram_assist_bot/infrastructure/telegram_bot/` | ارسال/ویرایش پیام مدیریتی و تبدیل Updateهای Bot API |
| `src/telegram_assist_bot/infrastructure/media/` | Storage محلی و بعداً Object Storage |
| `src/telegram_assist_bot/infrastructure/ai/` | Provider adapter، HTTP policy، Validation mapping و telemetry |
| `src/telegram_assist_bot/infrastructure/scheduling/` | Adapterهای Clock/Job wake-up؛ MongoDB منبع حقیقت Job است |
| `src/telegram_assist_bot/infrastructure/observability/` | Logging ساختاریافته، Redaction و Correlation |
| `src/telegram_assist_bot/presentation/` | Command/Callback Handler و View model مدیریتی |
| `src/telegram_assist_bot/workers/` | Entry loopهای Collector، Processor، Scheduler، AI و Cleanup |
| `src/telegram_assist_bot/bootstrap/` | Composition Root، lifecycle و Entry Pointها |
| `tests/unit/` | تست بدون شبکه/DB واقعی برای Domain و Application |
| `tests/integration/` | MongoDB، فایل‌سیستم و Adapterها با سرویس آزمایشی |
| `tests/contract/` | Fixture/Contract تبدیل پاسخ Telegram و AI |
| `tests/e2e/` | جریان‌های چندلایه و سناریوهای Restart/Concurrency |
| `tests/fixtures/` | داده مصنوعی، بدون Token، Session یا محتوای حساس واقعی |
| `config/configuration.example.json` | نمونه بدون Secret و مستند همه کلیدهای معتبر |
| `var/` | داده Runtime خارج از Git؛ مسیر واقعی می‌تواند بیرون مخزن باشد |

## قواعد مالکیت

- Portها در `application` تعریف و در `infrastructure` پیاده می‌شوند.
- DTO خارجی در مرز Adapter به DTO داخلی تبدیل می‌شود.
- Mapperهای MongoDB داخل Infrastructure می‌مانند.
- Presentation مستقیم Repository یا SDK Telegram را صدا نمی‌زند.
- Worker فقط Use Case را Trigger می‌کند.
- هیچ Session، فایل Media، Config محلی، Log یا Secret در این نقشه به‌عنوان فایل Commitشدنی اضافه نمی‌شود.
