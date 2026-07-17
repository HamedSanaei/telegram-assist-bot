# تصمیم‌های معماری

این فایل فقط تصمیم‌های اثرگذار و پایدار را ثبت می‌کند. گزینه‌های حل‌نشده در `docs/ARCHITECTURE.md `17` می‌مانند و پس از تصمیم قطعی به اینجا منتقل می‌شوند.

## ADR-001 — Python زبان برنامه

- **Status:** Accepted
- **Context:** نیازمندی، پروژه را Python تعریف کرده و اکوسیستم مناسبی برای Telegram، MongoDB، HTTP و پردازش Async لازم است.
- **Decision:** Application با Python ساخته می‌شود. Baseline رسمی CPython 3.12 و بازهٔ پشتیبانی‌شده `>=3.12,<3.15` است؛ CI روی 3.12، 3.13 و 3.14 اجرا می‌شود. `uv 0.11.28` مدیریت و قفل dependencyها و `hatchling 1.31.0` ساخت Package را انجام می‌دهند.
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
- **Decision:** Git فقط Config نمونه بدون مقدار حساس دارد. Secretها در Production از Environment/Secret Manager و برای اجرای محلی فقط از فایل ignored با نام `configuration.local.json` یا `configuration.<profile>.local.json` خوانده می‌شوند. Session/Media از مسیر Runtime خوانده می‌شوند.
- **Reason:** الزام امنیتی صریح.
- **Consequences:** `.gitignore` و Secret scanning در Bootstrap لازم‌اند؛ Config محلی plaintext است و جای ACL یا رمزگذاری سیستم‌عامل را نمی‌گیرد؛ Log redaction اجباری است؛ Fixtureها باید مصنوعی باشند؛ Config ناقص Fail-fast می‌شود.

## ADR-009 — Configuration Schema نسخه‌دار و Snapshot immutable

- **Status:** Accepted
- **Context:** کلیدهای Configuration قرارداد عمومی Taskهای بعدی‌اند و Startup
  باید خطاهای nested، ارجاعی و Secret را پیش از اتصال خارجی، بدون coercion یا
  افشای ورودی خام، به‌صورت تجمیعی گزارش کند. ZoneInfo سیستم نیز روی همهٔ
  پلتفرم‌های پشتیبانی‌شده یکسان در دسترس نیست.
- **Decision:** Schema اولیه عدد صحیح `1` است و مدل‌های آن با Pydantic v2 به‌صورت
  frozen، strict و `extra="forbid"` ساخته می‌شوند. Config نمونه و غیرمحلی فقط نام
  Environment Variable را در `SecretReference` نگه می‌دارند؛ Local Config می‌تواند
  literalهای Secret را داشته باشد که Loader پیش از ساخت Model به binding opaque
  تبدیل می‌کند. Loader یک Snapshot شامل `ApplicationConfig` و `ResolvedSecrets`
  redacted می‌سازد. `tzdata` منبع IANA
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

## ADR-012 — Taxonomy خطا، Observability task-local و Retry صریح

- **Status:** Accepted
- **Context:** Adapterهای آینده به دسته‌بندی خطای مشترک، Log قابل‌همبستگی و Retry
  محدود نیاز دارند، اما Foundation نباید به Telegram، MongoDB، AI/HTTP یا
  Scheduler وابسته شود. Secret ممکن است در Mapping، Header، URI، Exception یا
  ساختار nested باشد و context سراسری mutable میان coroutineها نشت می‌کند.
- **Decision:** categoryهای پایدار عبارت‌اند از `validation`، `configuration`،
  `authorization`، `permission`، `permanent`، `transient`، `timeout`،
  `rate_limit`، `concurrency_conflict` و `already_completed`؛ فقط سه category
  موقت، timeout و rate-limit retryable هستند. خطاهای boundary موجود با tag رشته‌ای
  همین قرارداد، بدون dependency معکوس به Foundation متصل می‌شوند. Correlation
  context یک value object frozen در `ContextVar` است. Structured event پیش از
  Sink/JSON با marker ثابت `[REDACTED]` پاک می‌شود و JSON فارسی را با
  `ensure_ascii=False` نگه می‌دارد. Retry حداکثر ۱۰ attempt دارد، sleeper و jitter
  source تزریق می‌شوند و caller باید safe/idempotent بودن operation را صریح اعلام
  کند. Cancellation حتی در cause chain فوراً عبور می‌کند؛ exhaustion همان
  Exception نهایی را بازمی‌گرداند.
- **Reason:** قراردادهای application-owned رفتار Adapterها را بدون SDK مشترک
  می‌کنند؛ `ContextVar` isolation هم‌زمانی می‌دهد؛ redaction پیش از خروج، مسیرهای
  نشت را متمرکز می‌کند؛ و opt-in بودن retry از تکرار side effect جلوگیری می‌کند.
- **Consequences:** نام categoryها، fieldهای پایه Log، marker redaction و semantics
  attempt قرارداد سازگاری‌اند و rename آن‌ها تصمیم/migration می‌خواهد. T005 هیچ
  retry را به Adapter موجود وصل نمی‌کند و Fallback، FloodWait، Circuit Breaker،
  DLQ و persistence شکست در Taskهای مربوط باقی می‌مانند. T006 فقط wiring و Sink
  واقعی Startup را روی همین API می‌سازد.

## ADR-013 — Composition Root یک‌مرحله‌ای و قرارداد lifecycle پایه

- **Status:** Accepted
- **Context:** Foundation به Entry point قابل‌اجرا نیاز داشت، ولی پیش از T007 هیچ
  Worker محصولی وجود ندارد. Startup باید Config را پیش از I/O validate، مسیر
  MongoDB و Index موجود را بدون duplication مصرف، تمام eventهای audit را حتی با
  Log Level بالای Application ثبت و resource نیمه‌ساخته را زیر failure یا
  cancellation آزاد کند.
- **Decision:** `telegram_assist_bot.bootstrap.runtime` تنها Composition Root
  concrete است و `python -m telegram_assist_bot` یک Startup check یک‌مرحله‌ای
  اجرا می‌کند. مسیر Config با precedence ثابت CLI، سپس `TAB_CONFIG_PATH` و سپس
  `config/configuration.json` resolve می‌شود. exit codeها `0`، `2` و `3` به‌ترتیب
  success، Configuration و Infrastructure هستند. logger تنظیم‌شدهٔ Application
  از Level Config پیروی می‌کند و logger audit lifecycle جداگانه با همان Sink،
  correlation و Redactor، eventهای اجباری را بدون فیلتر Level ثبت می‌کند. Mongo
  client تنها resource مالکیت‌دار است؛ shutdown task مشترک close را دقیقاً یک‌بار
  اجرا و cancellation را فقط پس از join شدن cleanup عبور می‌دهد.
- **Reason:** این قرارداد هم Startup واقعی Milestone 0 را قابل smoke می‌کند و هم
  بدون daemon یا Worker جعلی، ترتیب، readiness، observability و cleanup را قطعی
  و تست‌پذیر نگه می‌دارد.
- **Consequences:** CLI فعلی پس از readiness فوراً shutdown می‌شود؛ T007 فقط با
  command صریح می‌تواند رفتار authentication را اضافه کند. تغییر precedence،
  exit code یا نام eventهای lifecycle تغییر قرارداد عمومی است. shutdown حین
  state `STARTING` رد می‌شود و cancellation خود Startup مسیر cleanup مالک همان
  lifecycle را اجرا می‌کند. هیچ retry تازه‌ای به MongoDB متصل نشده است.

## ADR-014 — Telethon برای Telegram User API و Session فایل‌محور محافظت‌شده

- **Status:** Accepted
- **Context:** Milestone 1 به SDK ناهمگام نگهداری‌شده‌ای نیاز دارد که Session
  پایدار، History، event زنده، Entity/Custom Emoji و توسعهٔ بعدی Media را پشتیبانی
  کند، بدون نشت typeهای SDK به Application.
- **Decision:** `Telethon 1.44.0` به‌صورت exact pin انتخاب شد. Adapterهای آن فقط در
  `infrastructure/telegram/user` هستند و SDK objectها را به DTO و خطاهای
  application-owned تبدیل می‌کنند. Session فقط زیر runtime ignored نگه‌داری و
  mutation آن با lock محدود محافظت می‌شود. login تعاملی فقط command صریح `login`
  است؛ startup و `ingest-text` هرگز prompt نمی‌زنند.
- **Reason:** API کامل asyncio، Session پایدار، mapping Entityها و event handler
  قابل حذف، نیاز Milestone را بدون framework چند-SDK برآورده می‌کند.
- **Consequences:** تغییر SDK نیازمند Adapter و migration/re-authentication صریح
  Session است. روی POSIX permissionهای `0700/0600` best-effort اعمال می‌شوند؛ روی
  Windows حفاظت محرمانگی به ACL دایرکتوری runtime حساب کاربری وابسته است.

## ADR-015 — مسیر واحد ingest، claim اتمیک و subscribe-before-crawl

- **Status:** Accepted
- **Context:** Crawl و Listener می‌توانند هم‌زمان یک identity را تحویل دهند و
  restart ممکن است بین insert و claim رخ دهد. correctness نباید به lock محلی یا
  check-then-insert وابسته باشد و gap میان crawl و listener باید محدود شود.
- **Decision:** هر دو producer فقط `IngestPostIdempotently` را صدا می‌زنند.
  unique source identity، بازگرداندن canonical Post ID و marker افزایشی claim با
  عملیات اتمیک MongoDB منبع حقیقت‌اند. Composition Root ابتدا subscription محدود
  را ایجاد، سپس crawl امروز را اجرا و بعد buffered listener را مصرف می‌کند.
- **Reason:** duplicate delivery و restart بدون outbox یا broker تازه به یک document
  و یک claim می‌رسند و eventهای حین crawl در buffer محدود باقی می‌مانند.
- **Consequences:** schema version تغییر نکرده و خواندن سندهای قدیمی فاقد هر دو
  marker claim سازگار است. claim فقط hand-off مرحلهٔ بعد را ثبت می‌کند و هیچ Media،
  AI job یا downstream worker در Milestone 1 ایجاد نمی‌شود.

## ADR-016 — Media خصوصی content-addressed و state پایدار آماده‌سازی

- **Status:** Accepted
- **Context:** Milestone 2 باید دانلود Media و مرحله‌های آماده‌سازی را زیر crash،
  restart و workerهای هم‌زمان بدون ذخیرهٔ binary در MongoDB یا اتکا به timer/lock
  درون process ایمن کند.
- **Decision:** `LocalMediaStorage` فقط زیر root خصوصی پیکربندی‌شده، با stream،
  hash/size هم‌زمان، temp یکتا و rename اتمیک می‌نویسد. MongoDB metadata، Album،
  duplicate/category، artifact مقصد و readiness را نگه می‌دارد. Album deadlineهای
  quiet/max-wait بر پایهٔ زمان observation دارد؛ observation پیش از دانلود ثبت
  می‌شود و finalization با claim/lease، retry bounded و terminal state هر گروه
  انجام می‌گیرد. anchor از Post canonical همان source/group استخراج می‌شود و عضو
  دیررس پس از finalization نادیده گرفته می‌شود. Pipeline هر نتیجهٔ پایدار را پیش از اجرای مرحله reload و readiness را
  مشروط ایجاد می‌کند.
- **Reason:** فایل committed سالم از شکست پایگاه‌داده جان سالم به در می‌برد و در
  restart بدون truncate بازیابی می‌شود؛ state پایدار و عملیات اتمیک نیز correctness
  چند worker را بدون singleton، timer منبع حقیقت یا check-then-write تأمین می‌کند؛
  malformed بودن یک گروه نیز task بحرانی Runtime را متوقف نمی‌کند.
- **Consequences:** binary Media خارج MongoDB و خارج Git است؛ backup باید MongoDB
  و root خصوصی را هماهنگ پوشش دهد. POSIX permissionها best-effort و محرمانگی Windows
  وابسته به ACL است. Object Storage و orchestration محصولی خارج از Milestone 2 است.

## ADR-017 — سیاست‌های نسخه‌دار و قطعی آماده‌سازی محتوا

- **Status:** Accepted
- **Context:** duplicate، pruning مقصد و دسته‌بندی باید restart-safe، قابل audit و
  مستقل از runtime باشند و محتوای اصلی فارسی/Entityها را تغییر ندهند.
- **Decision:** normalization و content hash نسخه `1` حداقلی است و هیچ تبدیل
  `ی/ي`، `ک/ك` یا ZWNJ انجام نمی‌دهد؛ hashهای Media مرتب وارد serialization قطعی
  می‌شوند. destination-content policy نسخه `1` و مختصات UTF-16 با edit spanهای
  non-overlap استفاده می‌کند. category policy نسخه `1` precedence ثابت manual
  override، keyword و source default دارد و tie-break از ترتیب mapping مستقل است.
- **Reason:** versionها امکان مقایسه و migration صریح را می‌دهند و artifactهای
  مشتق‌شده بدون mutation متن/Caption/Entity اصلی بازتولیدپذیر می‌مانند.
- **Consequences:** تغییر هر normalization، serialization، entity clipping، pruning
  یا precedence نیازمند version تازه و migration/recompute صریح است. semantic/fuzzy
  duplicate، AI categorization و publication در این تصمیم و milestone وجود ندارند.

## ADR-018 — aiogram و توپولوژی خصوصی مدیریت

- **Status:** Accepted
- **Decision:** Bot API با `aiogram==3.29.1` پیاده می‌شود و type/exceptionهای آن
  فقط در Infrastructure/Presentation می‌مانند. فقط private chat و مدیر عددی
  Config‌شده با role یکتای `admin` و permissionهای `approval.view` و
  `approval.toggle` پشتیبانی می‌شود. Authorization همیشه default-deny است.
- **Consequences:** هر مدیر یک کپی مستقل Approval می‌گیرد؛ group/channel/topic و
  inline mode رد می‌شوند. تغییر SDK یا topology نیازمند ADR و migration است.

## ADR-019 — Callback opaque و پیام Approval مستقل

- **Status:** Accepted
- **Decision:** Callback با `c1_` و ۱۲۸ بیت CSPRNG بدون padding ساخته، claim فقط
  server-side ذخیره و پس از ۱۴ روز صریحاً منقضی می‌شود. Token برای actor/action/
  post/destination bind و در نخستین اجرای مجاز اتمیک consume/revoke می‌شود؛
  keyboard همگام‌شده Token تازه می‌سازد. برای هر
  مدیر یک header canonical قابل‌ویرایش و content مستقل ارسال می‌شود؛ metadata
  مدیریتی هرگز وارد artifact انتشار نمی‌شود.
- **Consequences:** MongoDB unique/TTL index منبع حقیقت است؛ HMAC/JWT و claim در
  callback ممنوع‌اند. شکست delivery فقط پس از دریافت شناسه‌های واقعی reference
  موفق می‌سازد و recovery باید idempotent باشد.

## ADR-020 — Keyboard، Toggle و Sync چندمدیره

- **Status:** Accepted
- **Decision:** هر مقصد مجاز دقیقاً یک ردیف scheduled/immediate با Token opaque
  دارد؛ حداکثر ۲۰ مقصد و بدون truncation/pagination. Toggle مستقل مقصد فقط میان
  `none`، `immediate` و `scheduled` با CAS نسخه‌دار است و انتشار/Job نمی‌سازد.
  Sync از آخرین state، best-effort و version-aware است؛ message-not-modified موفق،
  deleted دائمی و خطای موقت حداکثر سه attempt با claim اتمیک دارد.
- **Consequences:** stale sync نمی‌تواند UI جدید را overwrite کند، شکست یک مدیر
  بقیه را rollback نمی‌کند و Milestone 3 هیچ publication یا scheduling ندارد.

## ADR-021 — Trigger پس از Selection و هویت پایدار Publication/Schedule

- **Status:** Accepted
- **Decision:** Transition موفق به `immediate` فقط پس از commit Selection،
  Publication را dispatch می‌کند؛ Transition به `scheduled` نیز پس از commit یک
  Schedule Job می‌سازد و بازگشت به `none` آن را لغو می‌کند. Confirm جدا وجود
  ندارد. هویت‌های نسخه‌دار به‌ترتیب `post + destination + immediate + v1` و
  `post + destination + scheduled + v1` هستند و payload، actor، Token و زمان در
  هویت وارد نمی‌شوند.
- **Consequences:** Handler منتشر یا Query MongoDB اجرا نمی‌کند. تاریخچه Publication
  با تغییر Selection پاک نمی‌شود و Bot API هرگز مقصد را منتشر نمی‌کند.

## ADR-022 — Publication claim، Retry پیش‌ارسال و OutcomeUnknown

- **Status:** Accepted
- **Decision:** Publication با unique key و claim/lease اتمیک اجرا می‌شود. فقط
  خطای transient اثبات‌شدهٔ پیش از send retry می‌شود. خطایی که پس از امکان رسیدن
  request به Telegram رخ دهد `OutcomeUnknown` پایدار و terminal است و خودکار
  reopen یا resend نمی‌شود. reconciliation خارج Milestone 4 است.
- **Consequences:** raw payload، Session، credential، مسیر خصوصی و exception SDK
  ذخیره نمی‌شوند. Success و OutcomeUnknown با lease expiry دوباره claim نمی‌شوند.

## ADR-023 — صف مستقل مقصد، lease Worker و سیاست Cancellation

- **Status:** Accepted
- **Decision:** فاصله پیش‌فرض `300` ثانیه و Configurable/positive/bounded است.
  Slot خالی `now + interval` و Slot بعدی `last_due + interval` است و reservation
  اتمیک per Destination در MongoDB انجام می‌شود. Worker قدیمی‌ترین Job due را با
  lease claim می‌کند. Cancellation پیش‌فرض `preserve` است؛ `recompact` فقط Jobهای
  later و eligible همان Destination را atomically جابه‌جا و old/new due را audit
  می‌کند. Job claimed/running/terminal جابه‌جا یا دزدیده نمی‌شود.
- **Consequences:** MongoDB منبع حقیقت restart است؛ timer درون‌حافظه‌ای وجود ندارد.
  UI فقط پس از commit موفق sync می‌شود و conflict/rollback هیچ sync ندارد.

## ADR-024 — یک Session برای دریافت متن/Media و orchestration پایدار آماده‌سازی

- **Status:** Accepted
- **Decision:** فرمان `ingest` و alias `ingest-text` یک Composition Root مشترک دارند. همان client قفل‌شدهٔ Telethon برای validation، History، Listener و stream Media استفاده می‌شود. Crawl و Listener فقط `RuntimeMessageIngestor` را صدا می‌زنند؛ این مسیر قراردادهای موجود دانلود، Album و preparation را روی state پایدار MongoDB ادامه می‌دهد. finalizer آلبوم یک task محدود است و deadline پایدار collection منبع حقیقت باقی می‌ماند. cleanup به‌صورت command یک‌مرحله‌ای `media-cleanup` اجرا می‌شود.
- **Consequences:** session رقیب، write path موازی و task نامحدود ایجاد نمی‌شود. restart فایل و metadata سالم را reuse می‌کند، اما اجرای زنده همچنان به session معتبر، Premium account، دسترسی کانال و storage پایدار نیاز دارد.

## ADR-025 — مالک واحد User API و تفکیک Approval Bot

- **Status:** Accepted
- **Decision:** فرمان `runtime` ingestion، Media/Album finalization و اجرای commandهای
  انتشار فوری و زمان‌بندی‌شده را با یک `TelethonTextIngestionGateway` و یک client
  مشترک اجرا می‌کند. callback فقط command پایدار MongoDB می‌سازد. فرمان
  `approval-bot` تنها Aiogram Bot API و MongoDB را مالک است و Session کاربر را باز
  نمی‌کند. `ingest` سازگار می‌ماند و lock همان Session اجازه رقابت آن با `runtime`
  را نمی‌دهد؛ `schedule-worker` legacy پیش از Session fail-closed است.
- **Consequences:** دو client رقیب برای یک فایل Session وجود ندارد؛ restart کارهای
  approval/publication را از outbox/lease ادامه می‌دهد. production به دو Process
  `runtime` و `approval-bot` نیاز دارد و `schedule-worker` نباید هم‌زمان با runtime
  روی همان Session اجرا شود. عمر process با await کردن signal قطع همان client و
  stop event صریح نگه داشته می‌شود؛ پایان crawl یا registration دلیل shutdown نیست.
  polling فوری از native scheduling/reconciliation مستقل است و هر دو از serializer
  رسانهٔ مشترک روی همان client استفاده می‌کنند.

## ADR-026 — زمان‌بندی بومی Telegram با Outbox مستقل

- **Status:** Accepted
- **Decision:** انتخاب scheduled جدید دیگر `scheduled_publications` داخلی نمی‌سازد.
  Approval Bot یک command نسخه‌دار در `native_schedule_commands` ثبت می‌کند و
  Runtime با همان Telethon client مالک Session، تمام Scheduled Messages مقصد را
  می‌خواند. Slot برابر پنج دقیقه پس از بیشینهٔ `now` و آخرین زمان Telegram است و
  با lease مستقل مقصد serialize می‌شود. Telegram message IDها و UTC due پایدارند؛
  لغو فقط همان IDها را حذف می‌کند. مرز `request_started` مانع resend نتیجهٔ مبهم
  است و reconciliation نتیجهٔ ناپدیدشدن را قطعیِ «منتشر شد» فرض نمی‌کند.
- **Consequences:** زمان‌بندی‌های ساخته‌شده خارج برنامه در Slot اثر دارند و مدیر
  فوراً Scheduled Message را در Telegram می‌بیند. jobهای scheduled داخلی قدیمی
  بدون migration، execution یا cancellation خودکار inert می‌مانند. تغییر scheduled
  به immediate تا حذف بومی موفق صبر می‌کند؛ Bot API هرگز Session کاربر را باز
  نمی‌کند.

## ADR-027 — آداپتور اول ارائه‌دهنده هوش مصنوعی (z-ai)

- **Status:** Accepted
- **Context:** پیاده‌سازی اولین مدل هوش مصنوعی (z-ai) برای تشخیص تبلیغات، تشابه معنایی، دسته‌بندی و امتیازدهی پست‌های تلگرام به صورت ناهمگام (Async) بدون ایجاد وابستگی در لایه Application.
- **Decision:**
  - نام ارائه‌دهنده: `z-ai`
  - مدل مورد استفاده: `glm-4.7-flash`
  - آدرس رسمی پایگاه: `https://api.z.ai/api/paas/v4/chat/completions`
  - روش احراز هویت: HTTP Bearer Token
  - متغیر محیطی Secret: `TAB_ZAI_API_KEY`
  - بدون قابلیت Streaming
  - تسک‌های پشتیبانی شده: `advertisement_detection` ،`semantic_duplicate` ،`categorization` ،`scoring`
  - سیاست Timeout: پیش‌فرض ۳۰ ثانیه
  - سیاست اندازه پاسخ (Response Size Protection): محدودیت حداکثر پاسخ به ۱ مگابایت (1,048,576 بایت)
  - فرمت درخواست/پاسخ: مطابق استاندارد Chat Completions REST API ارسالی با فیلد `model` و ساختار `messages` و خروجی فیلد `choices[0].message.content`
  - مدیریت استثناءها: دسته‌بندی و نگاشت دقیق تمام حالت‌های شکست به خطاهای بومی (transient, timeout, rate_limit, permanent, authorization) بدون اعمال هرگونه تلاش مجدد (Retry) یا Fallback داخلی در آداپتور.
- **Reason:** ارائه‌دهنده سریع با مدل glm-4.7-flash برای تسک‌های فاز اول با سرعت و پاسخ‌دهی بالا بدون نیاز به منابع پردازشی سنگین.
- **Consequences:** آداپتور مستقل از بقیه اجزاء و به صورت مجزا تعریف می‌شود و هیچ تغییری در موتور اصلی سیستم یا کلاس‌های اصلی به غیر از تعاریف پیکربندی ایجاد نمی‌کند. کلیدهای احراز هویت و پاسخ‌های خام به هیچ‌وجه در لاگ‌ها، خطاها و خروجی‌ها نشت نخواهند کرد.

## ADR-028 — آداپتور دوم ارائه‌دهنده هوش مصنوعی (deepseek)

- **Status:** Accepted
- **Context:** پیاده‌سازی دومین ارائه‌دهنده هوش مصنوعی (deepseek) برای هم‌زیستی با ارائه‌دهنده اول و ارائه مدل‌های جایگزین برای تسک‌های تشخیص تبلیغات، تشابه معنایی، دسته‌بندی و امتیازدهی به صورت کاملاً مستقل و ایزوله.
- **Decision:**
  - نام ارائه‌دهنده: `deepseek`
  - مدل‌های مورد استفاده: `deepseek-v4-flash` (مدل پیش‌فرض) و `deepseek-v4-pro` (مدل جایگزین)
  - آدرس رسمی پایگاه: `https://api.deepseek.com`
  - قرارداد رسمی API: درخواست `POST /chat/completions` مطابق مستند رسمی Chat Completions؛ redirect و هر میزبان production دیگر رد می‌شود.
  - روش احراز هویت: HTTP Bearer Token
  - متغیر محیطی Secret: `TAB_DEEPSEEK_API_KEY`
  - بدون قابلیت Streaming
  - غیرفعال‌سازی صریح حالت تفکر (Thinking mode) با ارسال فیلد `"thinking": {"type": "disabled"}` در ریشه درخواست
  - فرمت خروجی صریح با ارسال `"response_format": {"type": "json_object"}`
  - تسک‌های پشتیبانی شده برای هر دو مدل: `advertisement_detection` ،`semantic_duplicate` ،`categorization` ،`scoring`
  - سیاست Timeout: پیش‌فرض ۳۰ ثانیه
  - سیاست Quota: مقدار ثابت محلی حدس زده نمی‌شود؛ پاسخ `429` و مقدار عددی امن `Retry-After` فقط طبقه‌بندی و به policy آینده T039 تحویل می‌شود و Adapter هیچ Retry انجام نمی‌دهد.
  - سیاست اندازه پاسخ (Response Size Protection): محدودیت حداکثر پاسخ به ۱ مگابایت (1,048,576 بایت)
  - Fixture قرارداد: پاسخ‌های sanitized محلی برای هر دو Model و حالت‌های success/error؛ تماس live بخشی از Suite یا شرط Done نیست.
  - مدیریت استثناءها: دسته‌بندی و نگاشت دقیق تمام حالت‌های شکست به خطاهای بومی (transient, timeout, rate_limit, permanent, authorization) بدون اعمال هرگونه تلاش مجدد (Retry) یا Fallback داخلی در آداپتور.
- **Reason:** ارائه دهنده قدرتمند و اقتصادی DeepSeek با مدل‌های flash و pro برای ارائه جایگزین با کارایی بالا در پایپ‌لاین تحلیل هوشمند.
- **Consequences:** آداپتور به صورت مستقل تحت ماژول جدا پیاده‌سازی می‌شود، کلاینت و ساختار درخواست‌های آن تفکیک شده است و هیچ وابستگی متقابلی بین آداپتور z-ai و deepseek وجود ندارد. اطلاعات حساس و احراز هویت به صورت کامل در خروجی‌ها و استثناها فیلتر و ردکت می‌شوند.

## ADR-029 — عدم نگهداری Prompt و پاسخ خام در Cache و Audit AI

- **Status:** Accepted
- **Decision:** T041 فقط `AIResult` استاندارد و metadata امن را در Cache، Audit و
  Metrics ذخیره می‌کند. نگهداری Prompt، ورودی یا پاسخ خام غیرفعال است و Configuration
  فعلی فقط مقدار `false` را می‌پذیرد. فعال‌سازی آینده به ADR، قرارداد Sanitization و
  Retention مستقل نیاز دارد.
- **Consequences:** Cache و Audit برای کاهش مصرف سهمیه و مشاهده‌پذیری قابل استفاده‌اند،
  اما برای بازسازی payload خام Provider طراحی نشده‌اند. hash ورودی مجوز ذخیره متن
  اصلی نیست و failureهای persistence نیز فقط با reason code امن گزارش می‌شوند.

## ADR-030 — سیاست صریح شکست تشخیص تبلیغ و handoff بررسی دستی

- **Status:** Accepted
- **Decision:** تشخیص تبلیغ فقط چهار policy صریح `continue_processing`،
  `stop_processing`، `retry_later` و `manual_review` را می‌پذیرد و هیچ default ضمنی
  ندارد. وقتی قابلیت با flag سراسری و per-source مؤثر است، policy معتبر باید در
  Configuration وجود داشته باشد؛ در حالت غیرفعال configuration قدیمی بدون آن معتبر
  می‌ماند. `continue_processing` شکست را نگه می‌دارد و مرحلهٔ بعد را مجاز می‌کند؛
  `stop_processing` پردازش خودکار را متوقف می‌کند؛ `retry_later` فقط زمان‌بندی موجود
  AIJob را مصرف می‌کند؛ و `manual_review` حالت
  `AdvertisementManualReviewRequired` با reason ثابت
  `advertisement_check_failed` را برای handoff Application تأیید ثبت می‌کند.
- **Consequences:** شکست همه Providerها هرگز به نتیجهٔ «غیرتبلیغاتی» یا AIResult
  ساختگی تبدیل نمی‌شود. مسیر دستی در T042 فقط state/contract است و Telegram UX یا
  Runtime wiring ندارد. تغییر این چهار policy یا افزودن threshold confidence به
  تصمیم مستقل آینده نیاز دارد.
