# تصمیم‌های معماری

این فایل فقط تصمیم‌های اثرگذار و پایدار را ثبت می‌کند. گزینه‌های حل‌نشده در `docs/ARCHITECTURE.md `17` می‌مانند و پس از تصمیم قطعی به اینجا منتقل می‌شوند.

## ADR-001 — Python زبان برنامه

- **Status:** Accepted
- **Context:** نیازمندی، پروژه را Python تعریف کرده و اکوسیستم مناسبی برای Telegram، MongoDB، HTTP و پردازش Async لازم است.
- **Decision:** Application با Python ساخته می‌شود. Baseline رسمی CPython 3.12 و بازهٔ پشتیبانی‌شده `>=3.12,<3.14` است؛ CI روی 3.12 و 3.13 اجرا می‌شود. `uv 0.11.28` مدیریت و قفل dependencyها و `hatchling 1.31.0` ساخت Package را انجام می‌دهند.
- **Reason:** انطباق مستقیم با نیازمندی و دسترسی به SDKها و ابزارهای تست بالغ.
- **Consequences:** Type checking و مرزبندی ماژول‌ها صریح است؛ عملیات I/O نباید Event Loop را Block کند؛ نسخه‌های resolveشده در `uv.lock` قرار می‌گیرند و Build CI از backend قفل‌شده بدون build isolation استفاده می‌کند. افزودن Minor جدید Python نیازمند عبور همهٔ Gateها و به‌روزرسانی metadata، CI، Lockfile و مستندات است.

## ADR-002 — MongoDB دیتابیس اصلی

- **Status:** Accepted
- **Context:** Postها، وضعیت‌های پردازش و Jobهای پایدار ساختار Document-oriented و Atomic update می‌خواهند.
- **Decision:** MongoDB منبع حقیقت اصلی Post، Publication، Schedule، AI Job و Advertisement Job است.
- **Reason:** الزام `docs/REQUIREMENTS.md` و پشتیبانی از Unique/TTL Index و Claim اتمیک.
- **Consequences:** Index setup و Schema evolution باید صریح باشند؛ TTL حذف دقیق در لحظه را تضمین نمی‌کند؛ Query و Mapperها پشت Port می‌مانند.

## ADR-003 — مرزهای Clean Architecture

- **Status:** Accepted
- **Context:** منطق اصلی نباید به Telegram، MongoDB، AI، فایل‌سیستم یا Scheduler وابسته شود.
- **Decision:** Domain مستقل است؛ Application Use Case و Portها را دارد؛ Adapterها در Infrastructure و Handlerها در Presentation قرار می‌گیرند.
- **Reason:** تست‌پذیری و امکان جایگزینی سرویس‌های خارجی بدون بازنویسی قواعد.
- **Consequences:** SDK objectها از مرز Adapter عبور نمی‌کنند؛ Wiring فقط در Composition Root انجام می‌شود؛ برای هر رفتار خارجی تست Contract لازم است.

## ADR-004 — Telegram User API برای Crawl و انتشار نهایی

- **Status:** Accepted
- **Context:** دریافت History/Media و حفظ Premium/Custom Emoji به حساب کاربری Premium نیاز دارد.
- **Decision:** Crawl، Listener، دریافت URL تبلیغ و انتشار نهایی با یک Session حساب Telegram User API انجام می‌شود.
- **Reason:** الزام فاز اول و حفظ Entityهای Premium.
- **Consequences:** Session یک Secret Runtime است؛ ورود اولیه تعاملی و اجرای بعدی غیرتعاملی است؛ Adapter باید Flood Wait، Timeout و Session invalidation را مدیریت کند.

## ADR-005 — Telegram Bot API فقط برای تعامل مدیران

- **Status:** Accepted
- **Context:** Command، Callback و پیام تأیید مدیریتی به Bot API مناسب‌اند ولی انتشار نهایی نباید با Bot باشد.
- **Decision:** Bot API فقط Presentation مدیریتی است.
- **Reason:** جداسازی دسترسی مدیران از حساب Premium و انطباق با نیازمندی.
- **Consequences:** هر Handler و Callback باید Authorization و وضعیت فعلی را بررسی کند؛ پیام‌های تأیید Reference پایدار می‌خواهند؛ Bot credential Secret است.

## ADR-006 — Jobهای پایدار در MongoDB

- **Status:** Accepted
- **Context:** Schedule، AI و Slot تبلیغ باید پس از Restart باقی بمانند و چند Worker آن‌ها را دوبار اجرا نکنند.
- **Decision:** Job پیش از اجرا در MongoDB ذخیره و با Atomic Claim، Lease و Idempotency Key اجرا می‌شود.
- **Reason:** دوام، بازیابی و کنترل هم‌زمانی بدون افزودن Broker در نسخه اولیه.
- **Consequences:** Worker باید Lease منقضی را بازیابی کند؛ Unique Index لازم است؛ Scheduler درون حافظه فقط Wake-up mechanism است، نه منبع حقیقت.

## ADR-007 — Fallback هوش مصنوعی Config-driven

- **Status:** Accepted
- **Context:** Providerهای رایگان ناپایدار و محدودند و ترتیب آن‌ها باید بر اساس Task قابل تغییر باشد.
- **Decision:** Provider/Model routing، Retry، Fallback و رفتار شکست نهایی از Configuration خوانده و خروجی به مدل داخلی واحد تبدیل می‌شود.
- **Reason:** عدم وابستگی Application به Provider و امکان ادامه سرویس هنگام Timeout/Quota/پاسخ نامعتبر.
- **Consequences:** Providerهای واقعی باید جداگانه انتخاب شوند؛ Schema validation، Rate limit reservation، Circuit Breaker، Cache و Audit بخشی از Pipeline هستند؛ نتیجه جعلی ممنوع است.

## ADR-008 — هیچ Secret در Git

- **Status:** Accepted
- **Context:** Telegram credentials، Session، Bot token، Mongo password و AI key بسیار حساس‌اند.
- **Decision:** Git فقط Config نمونه بدون مقدار حساس دارد؛ Secretها از Environment/Secret Manager و Session/Media از مسیر Runtime خوانده می‌شوند.
- **Reason:** الزام امنیتی صریح.
- **Consequences:** `.gitignore` و Secret scanning در Bootstrap لازم‌اند؛ Log redaction اجباری است؛ Fixtureها باید مصنوعی باشند؛ Config ناقص Fail-fast می‌شود.
