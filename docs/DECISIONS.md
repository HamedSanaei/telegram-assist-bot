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

## ADR-009 — Configuration Schema نسخه‌دار و Snapshot immutable

- **Status:** Accepted
- **Context:** کلیدهای Configuration قرارداد عمومی Taskهای بعدی‌اند و Startup
  باید خطاهای nested، ارجاعی و Secret را پیش از اتصال خارجی، بدون coercion یا
  افشای ورودی خام، به‌صورت تجمیعی گزارش کند. ZoneInfo سیستم نیز روی همهٔ
  پلتفرم‌های پشتیبانی‌شده یکسان در دسترس نیست.
- **Decision:** Schema اولیه عدد صحیح `1` است و مدل‌های آن با Pydantic v2 به‌صورت
  frozen، strict و `extra="forbid"` ساخته می‌شوند. JSON فقط نام Environment
  Variable را در `SecretReference` نگه می‌دارد؛ Loader یک Snapshot شامل
  `ApplicationConfig` و `ResolvedSecrets` redacted می‌سازد. `tzdata` منبع IANA
  قابل‌قفل برای محیط‌های فاقد tzdb سیستم است. نسخهٔ ناشناخته Fail-fast است و
  Migration یا Dynamic reload در این مرحله وجود ندارد.
- **Reason:** این قرارداد type-safe، deterministic و قابل‌آزمون است، typo و
  coercion پنهان را رد می‌کند، متن Unicode را بدون normalization نگه می‌دارد و
  Domain/Application را از JSON، Environment و کتابخانه‌های Adapter مستقل
  نگه می‌دارد.
- **Consequences:** تغییر ناسازگار کلیدها یا معنا به Schema جدید و Migration
  صریح نیاز دارد؛ Pydantic و tzdata runtime dependencyهای قفل‌شده‌اند؛
  Composition Root تنها مصرف‌کنندهٔ Loader است؛ Configuration پس از Startup
  read-only است و تغییر آن Restart کنترل‌شده می‌خواهد.

## ADR-010 — چرخهٔ عمر حداقلی Post و مختصات UTF-16 Entity

- **Status:** Accepted
- **Context:** فهرست وضعیت‌های بخش ۱۰ نیازمندی‌ها پیشنهادی است و حالت‌های
  پردازش، انتخاب چند مقصد و انتشار را مخلوط می‌کند. در عین حال Domain باید پیش
  از انتخاب Telegram SDK، هویت و Entityهای متن اصلی را بدون از دست‌دادن Custom
  Emoji تثبیت کند.
- **Decision:** وضعیت کلی Post در Milestone 0 فقط مقادیر پایدار `Discovered`،
  `Stored` و `Expired` دارد. Transitionهای مجاز عبارت‌اند از
  `Discovered → Stored` پیش از انقضا و `Discovered/Stored → Expired` در یا پس از
  مرز انقضا؛ `Expired` Terminal است. هر Transition یک snapshot frozen تازه با
  history و version افزایشی می‌سازد. وضعیت آیندهٔ هر مقصد در مدل مستقل
  `Post × Destination` قرار می‌گیرد. Entity اصلی offset و length را صریحاً با
  UTF-16 code unit و شناسهٔ Custom Emoji را به‌صورت رشتهٔ opaque نگه می‌دارد.
- **Reason:** این تفکیک، وضعیت جزئی چند مقصد را به یک Enum سراسری تحمیل نمی‌کند،
  optimistic concurrency را بدون وابستگی به MongoDB قابل‌آزمون می‌کند و با
  قرارداد offset تلگرام و حفظ Premium/Custom Emoji سازگار است.
- **Consequences:** نام وضعیت‌ها و واحد Entity قرارداد persistence آینده‌اند و
  تغییر ناسازگارشان migration می‌خواهد. Adapter تلگرام مسئول mapping بدون
  normalization و Mapper MongoDB مسئول بازسازی UTC/history است. Atomic compare
  and set در T004 پیاده می‌شود و افزودن وضعیت یا workflow مقصد فقط در Task
  صریح مربوط مجاز است.

## ADR-011 — قرارداد MongoDB پست، PyMongo async و هم‌زمانی اتمیک

- **Status:** Accepted
- **Context:** T004 باید هویت منبع را زیر رقابت واقعی یکتا کند، TTL چهارده‌روزه
  را بدون حذف زودهنگام اعمال کند، timestamp و محتوای اصلی را دقیق بازسازی کند و
  optimistic concurrency دامنه را بدون نشت نوع یا خطای MongoDB به Application
  enforce کند. Driver async و حداقل نسخهٔ Server نیز پیش‌تر تثبیت نشده بودند.
- **Decision:** Adapter رسمی از `PyMongo AsyncMongoClient` با dependency
  `pymongo>=4.13,<5`، Stable API v1 strict و حداقل MongoDB 7.0 (wire version 21)
  استفاده می‌کند. read/write retry داخلی driver غیرفعال و تمام عملیات با timeout
  محدود Configuration اجرا می‌شوند. Collection پایدار `posts` فقط سند دقیق
  `schema_version = 1` را می‌پذیرد. Indexهای مالکیت‌شده `uq_posts_source_identity_v1`
  روی زوج شناسهٔ منبع با `unique: true` و `ttl_posts_expires_at_v1` روی
  `expires_at` با `expireAfterSeconds: 0` هستند. درج مستقیم و DuplicateKey دقیق
  جای check-then-insert را می‌گیرد؛ Transition با CAS روی شناسه، نسخهٔ Schema،
  version و status انجام می‌شود. Mapper timestampهای BSON را با remainder
  میکروثانیه بازسازی و `expires_at` را رو به بالا ذخیره می‌کند تا TTL پیش از مرز
  Domain حذف نکند.
- **Reason:** API async فعلی PyMongo از ورود API منسوخ به قرارداد جلوگیری
  می‌کند؛ Unique Index و CAS صحت را میان Processها تأمین می‌کنند و timeout/retry
  صریح رفتار خارجی را قابل‌کنترل نگه می‌دارد. Schema و Index نام‌دار نیز تغییر
  ناسازگار را به‌جای فساد یا migration ضمنی آشکار می‌کنند.
- **Consequences:** MongoDB قدیمی‌تر از 7.0 در startup رد می‌شود؛ Index ناسازگار
  هرگز خودکار drop یا بازسازی نمی‌شود و migration صریح می‌خواهد. تغییر Schema،
  نام collection/index، وضعیت persistence-facing یا encoding زمان نیازمند
  migration و تصمیم سازگاری است. Application فقط Port/result/exception خود را
  می‌بیند؛ PyMongo، BSON و جزئیات Query در Infrastructure باقی می‌مانند. TTL
  همچنان eventual است، پس queryهای Application-facing باید انقضا را منطقی و
  دقیق فیلتر کنند. retry قابل مشاهده و failure-aware در T005 تعریف می‌شود و
  Adapter نباید retry پنهان driver را دوباره فعال کند.
