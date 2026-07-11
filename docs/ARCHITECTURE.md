# معماری پیشنهادی Telegram Admin Assistant

## 1. وضعیت و دامنه سند

این سند طرح معماری پیش از شروع پیاده‌سازی است. منبع حقیقت نیازمندی‌های محصول فایل `docs/REQUIREMENTS.md` است و Taskها باید فقط به همین مسیر ارجاع دهند.

معماری برای تحویل تدریجی طراحی شده است:

- **پیاده‌سازی اولیه:** فاز اول؛ دریافت، پردازش، تأیید و انتشار محتوای کانال‌ها، به‌همراه زیرساخت AI لازم.
- **گام بعدی قطعی:** فاز دوم؛ تبلیغات زمان‌بندی‌شده.
- **قابلیت‌های آینده و نیازمند بازتعریف:** فازهای سوم تا پنجم. این بخش‌ها در `docs/REQUIREMENTS.md` پیشنهادی‌اند و پیش از پیاده‌سازی باید معیار پذیرش و اولویت محصولی پیدا کنند.

اصل راهنما، یک برنامه ماژولار و قابل استقرار به‌صورت یک واحد در شروع است. مرزها از روز نخست رعایت می‌شوند، اما سرویس‌های جدا، Message Broker، Event Sourcing یا Framework داخلی عمومی تا زمانی که نیاز عملی ایجاد نشده افزوده نمی‌شوند.

## 2. اهداف معماری

- منطق کسب‌وکار مستقل از Telegram، MongoDB، فایل‌سیستم، کتابخانه زمان‌بندی و Providerهای AI باشد.
- هر رفتار خارجی پشت یک Port قابل جایگزینی و قابل Fake شدن قرار گیرد.
- عملیات قابل تکرار، Idempotent و عملیات هم‌زمان، Atomic یا Lease-based باشند.
- Restart هیچ Session معتبر، پست ذخیره‌شده، Job زمان‌بندی یا AI Job را از بین نبرد.
- نسخه اصلی متن و Entityها دست‌نخورده بماند و خروجی هر مقصد جداگانه ساخته شود.
- مسیر سادهٔ اولیه حفظ شود و قابلیت‌های آینده بدون بازنویسی Domain/Application قابل افزودن باشند.

## 3. لایه‌ها و جهت وابستگی

```text
Domain
  ↑
Application (Use Cases + Ports)
  ↑                         ↑
Presentation / Workers      Infrastructure Adapters
              \             /
               Composition Root
```

قواعد وابستگی:

1. **Domain** فقط Python استاندارد و مدل‌های خود را می‌شناسد.
2. **Application** به Domain وابسته است و Portها را تعریف می‌کند؛ نام یا نوع کتابخانه‌های Telegram، MongoDB، HTTP و Scheduler در آن دیده نمی‌شود.
3. **Infrastructure** Portهای Application را برای MongoDB، Telegram User API، Telegram Bot API، Media Storage، AI و Logging پیاده می‌کند.
4. **Presentation** فرمان‌ها و Callbackهای مدیران را به Command/DTOهای Application تبدیل می‌کند و سیاست کسب‌وکار را در Handlerها قرار نمی‌دهد.
5. **Workers** محرک Use Caseها هستند؛ Crawl، Listener، Due Job و AI Job را اجرا می‌کنند اما تصمیم دامنه‌ای نمی‌گیرند.
6. **Composition Root** تنها محل ساخت Adapterها، اتصال آن‌ها به Use Caseها و مدیریت چرخه عمر Process است.

هیچ Import از `domain` یا `application` به `infrastructure`، `presentation` یا SDKهای خارجی مجاز نیست.

## 4. مدل Domain

مدل‌ها باید مستقل از شکل Documentهای MongoDB و Objectهای SDK تلگرام باشند. فهرست اولیه:

| مدل | مسئولیت |
|---|---|
| `Post` | هویت منبع، متن/Caption اصلی، Entityها، دسته‌بندی، نتیجه پردازش، انقضا و وضعیت کلی |
| `Media` | نوع، ترتیب، مشخصات فایل، Hash، مکان ذخیره، وضعیت دانلود و انقضا |
| `SourceChannel` | شناسه پایدار، Username، فعال‌بودن و سیاست‌های پردازش/مقصدهای مجاز |
| `DestinationChannel` | شناسه پایدار، Username و سیاست انتشار/فاصله زمانی |
| `Admin` | شناسه عددی، فعال‌بودن، نقش و مجوزها |
| `ApprovalReference` | محل پیام تأیید هر مدیر/گروه برای همگام‌سازی |
| `DestinationSelection` | وضعیت مستقل هر `Post × Destination` و نسخه هم‌زمانی |
| `Publication` | درخواست انتشار، کلید Idempotency، نتیجه و شناسه پیام مقصد |
| `ScheduledPublication` | زمان برنامه‌ریزی، وضعیت Job، Lease، تلاش‌ها و نتیجه |
| `AIJob` / `AIAnalysis` | عملیات AI پایدار، نسخه Prompt/Schema، تلاش‌ها و نتیجه استاندارد |
| `AdvertisementCheckResult` | نتیجه، اطمینان، دلیل، مدل و نسخه Prompt |
| `DuplicateCheckResult` | روش، شباهت، پست مشابه و تصمیم |
| `AdvertisementCampaign` / `AdvertisementSlot` | تعریف تبلیغ و یک اجرای یکتای آن در مقصد و زمان |
| `StatusTransition` | وضعیت قبلی/جدید، زمان، Actor، دلیل و Correlation ID |

قواعد کلیدی Domain:

- هویت پیام منبع برابر `(source_channel_id, source_message_id)` است.
- متن اصلی و Entityهای اصلی Immutable هستند؛ نسخه پردازش‌شده برای هر مقصد مشتق می‌شود.
- یک مقصد نمی‌تواند هم‌زمان در دو حالت فوری و زمان‌بندی‌شده باشد.
- انتشار موفق Terminal است و درخواست تکراری نباید انتشار دوم بسازد.
- همه زمان‌ها در Domain به‌صورت UTC آگاه از منطقه زمانی نگهداری و در مرزها به منطقه تنظیم‌شده تبدیل می‌شوند.
- انقضای پست از `received_at + 14 days` محاسبه می‌شود.

وضعیت‌های بخش `10` در `docs/REQUIREMENTS.md` نقطه شروع‌اند، اما پیش از پیاده‌سازی در T003 به Transitionهای مجاز و وضعیت مستقل هر مقصد تبدیل می‌شوند؛ یک Enum کلی به‌تنهایی برای انتشار جزئی چند مقصد کافی نیست.

## 5. Use Caseهای Application

### دریافت و پردازش

- `AuthenticateTelegramSession` و `ValidateTelegramSession`
- `CrawlTodayTextPosts` و `HandleLiveMessage`
- `IngestPostIdempotently`
- `DownloadPostMedia` و `AssembleMediaGroup`
- `DetectExactDuplicate` و `PreparePostContent`
- `CategorizePost`

### تأیید و انتشار

- `AuthorizeAdminAction`
- `SendPostForApproval`
- `ToggleDestinationSelection`
- `SynchronizeApprovalMessages`
- `PublishPostImmediately`
- `SchedulePost`، `CancelScheduledPost` و `RunDuePublication`

### AI

- `EnqueueAIJob` و `ClaimAIJob`
- `ExecuteAIWithFallback`
- `DetectAdvertisement`
- `DetectSemanticDuplicate`
- `CategorizeWithAI`
- `UpdatePostScore`

### تبلیغات

- `FetchAdvertisementSource`
- `ExpandAdvertisementSlots`
- `PublishAdvertisementSlot`
- `ResolvePublicationCollision`
- `ReportAdvertisementRuns`

Use Caseها تراکنش یا Atomic Operation لازم را از Port اختصاصی درخواست می‌کنند؛ Transaction syntax و Queryهای MongoDB وارد Application نمی‌شوند.

## 6. Portها و Interfaceها

| Port | قرارداد مورد انتظار |
|---|---|
| `PostRepository` | Upsert یکتا، دریافت بازه ۱۴روزه، Transition اتمیک و ثبت خطا |
| `ApprovalRepository` | نگهداری Referenceها و وضعیت همگام‌سازی |
| `PublicationRepository` | ایجاد/Claim/تکمیل Idempotent انتشار |
| `ScheduleRepository` | محاسبه Slot اتمیک، Claim با Lease، لغو و بازیابی |
| `AIJobRepository` | Enqueue یکتا، اولویت، Lease، Retry و نتیجه |
| `AdvertisementRepository` | Campaign، Slot یکتا و گزارش اجرا |
| `TelegramSourceGateway` | احراز Session، Resolve کانال، History، Listener و دریافت URL |
| `TelegramPublisherGateway` | انتشار متن/مدیا/آلبوم با User API و Entityهای اصلی |
| `AdminMessagingGateway` | ارسال/ویرایش پیام تأیید و پاسخ Callback با Bot API |
| `MediaStorage` | ذخیره Stream، خواندن، Hash، حذف و مسیر خصوصی |
| `AIProvider` | اجرای یک Task روی یک Provider/Model و بازگرداندن پاسخ خام استانداردشده |
| `Clock` | زمان UTC و تبدیل مبتنی بر ZoneInfo برای تست قطعی |
| `IdGenerator` | شناسه‌های غیرقابل حدس برای Callback/Correlation |
| `UnitOfWork/Atomic Ports` | فقط در Use Caseهایی که چند تغییر باید یکپارچه باشند |

Port عمومی و مبهم مانند `Repository[T]` ایجاد نمی‌شود؛ قراردادها بر اساس عملیات واقعی هر Use Case شکل می‌گیرند.

## 7. مسئولیت Telegram User API

User API با حساب دارای Premium تنها مسئول این موارد است:

- ورود تعاملی اولیه، ذخیره Session خارج از Git و استفاده مجدد از آن؛
- اعتبارسنجی Session، Premium بودن حساب و دسترسی کانال‌ها؛
- Resolve کانال‌های عمومی، خزش History از ابتدای روز محلی تا اکنون و Listener زنده؛
- دریافت متن، Caption، همه Entityها، Media و Media Group؛
- دریافت پست تبلیغاتی از URL؛
- انتشار نهایی متن، Media و Album در کانال مقصد با حفظ Custom Emoji.

Adapter باید Objectهای SDK را فوراً به DTO داخلی تبدیل کند، Timeout داشته باشد، Flood Wait را طبق زمان اعلام‌شده مدیریت کند و Session را هنگام خطای شبکه حذف یا بازنویسی نکند. انتخاب SDK (برای مثال Telethon یا Pyrogram) هنوز تصمیم نشده است.

## 8. مسئولیت Telegram Bot API

Bot API فقط کانال مدیریتی است:

- دریافت Command و Callback؛
- احراز Admin و مجوز مقصد پیش از هر عملیات؛
- تولید هدر، محتوای پیشنهادی و Keyboard؛
- ارسال پیام تأیید و ذخیره شناسه هر نسخه؛
- ویرایش هدر/Keyboard تمام مدیران به‌صورت best-effort و ثبت Retry مستقل؛
- گزارش نتیجه عملیات و گزارش تبلیغات.

Bot API برای انتشار نهایی به کانال مقصد استفاده نمی‌شود. Callback Data باید کوتاه و غیرقابل سوءاستفاده باشد؛ طرح اولیه، Token تصادفی opaque و رکورد server-side با انقضا است. HMAC کوتاه فقط در صورت اثبات کفایت محدودیت اندازه جایگزین می‌شود.

## 9. MongoDB و مدل ماندگاری

MongoDB منبع حقیقت پردازش است. Collectionهای پیشنهادی اولیه:

- `posts`: Unique Index روی `source_channel_id + source_message_id` و TTL روی `expires_at`؛
- `approvals`: Reference پیام‌های تأیید و وضعیت آخرین Sync؛
- `publications`: Unique Idempotency Key برای هر تصمیم انتشار؛
- `scheduled_publications`: Index روی `status + due_at`، Unique Key و فیلدهای Lease؛
- `ai_jobs` و `ai_results`: Job پایدار، نتیجه، Prompt/Schema version و Claim اتمیک؛
- `provider_state` و `provider_metrics`: Cooldown، Circuit، سهمیه و آمار؛
- `advertisement_campaigns` و `advertisement_slots`: تعریف و اجرای یکتای Slot؛
- `callback_tokens`: Token کوتاه، Actor/Action/Post/Destination و زمان انقضا.

قواعد ماندگاری:

- ایجاد Indexها بخشی از Startup/Migration صریح است و خطای آن Fail-fast می‌شود.
- Upsert/`findOneAndUpdate` با شرط نسخه برای Idempotency و Optimistic Concurrency به‌کار می‌رود.
- TTL MongoDB حذف آنی را تضمین نمی‌کند؛ `expires_at` در Queryهای Application نیز اعمال می‌شود.
- حذف TTL سند، فایل محلی را حذف نمی‌کند؛ Cleanup Worker مستقل Mediaهای منقضی و Orphan را پاک می‌کند.
- Migrationهای سازگار با عقب و ثبت نسخه Schema لازم‌اند؛ راهکار دقیق در زمان Bootstrap انتخاب می‌شود.

## 10. ذخیره Media

نسخه اولیه از Storage محلی خصوصی پشت `MediaStorage` استفاده می‌کند، مگر محیط استقرار Object Storage را الزامی کند. فایل‌ها خارج از مسیرهای قابل Commit و بدون URL عمومی ذخیره می‌شوند. مسیر نهایی از Content Hash/شناسه داخلی ساخته می‌شود و نام ورودی مستقیماً به مسیر تبدیل نمی‌شود.

فرایند:

1. Metadata اولیه ذخیره شود.
2. Stream با Timeout و Retry محدود دانلود شود.
3. Hash هنگام Streaming محاسبه و فایل با Write اتمیک نهایی شود.
4. وضعیت `Ready` و `expires_at` ثبت شود.
5. Cleanup Worker فایل منقضی یا Orphan را حذف کند.

Adapter بعدی Object Storage همان Port را پیاده می‌کند و Domain/Application تغییر نمی‌کنند.

## 11. زمان‌بندی پایدار

Scheduler کتابخانه‌ای درون حافظه منبع حقیقت نیست. هر Job ابتدا در MongoDB ثبت می‌شود:

- زمان Slot هر مقصد با یک عملیات اتمیک و فاصله تنظیم‌شده محاسبه می‌شود.
- Worker، Jobهای Due را با `findOneAndUpdate` و Lease محدود Claim می‌کند.
- Unique Idempotency Key مانع اجرای دوباره پس از Restart/Retry می‌شود.
- شکست موقت به `WaitingForRetry` با Backoff محدود می‌رود؛ شکست نهایی به وضعیت بررسی دستی/DLQ.
- لغو با شرط وضعیت انجام می‌شود تا Job در حال/تمام‌شده دوباره تغییر نکند.
- بازیابی Restart یعنی Claim دوباره Leaseهای منقضی، نه بازسازی Job از حافظه.

همین الگو برای Scheduled Publication، AI Job و Advertisement Slot استفاده می‌شود، اما Collection و سیاست هرکدام مستقل است.

## 12. Pipeline و Fallback هوش مصنوعی

Pipeline AI بخشی از فاز اول است، زیرا تشخیص تبلیغ، تکرار معنایی و امتیازدهی به آن وابسته‌اند. ترتیب اجرا:

1. درخواست به‌صورت `AIJob` یکتا و پایدار ثبت شود.
2. Worker با Lease و اولویت Claim کند.
3. Cache بر اساس Hash محتوا، Task، Prompt version، Schema version و زبان بررسی شود.
4. Provider/Modelهای فعال و پشتیبان Task از Configuration انتخاب و مرتب شوند.
5. سلامت، Cooldown، Circuit و سهمیه به‌صورت اتمیک Reserve شود.
6. درخواست با Timeout و Retry داخلی محدود اجرا شود.
7. پاسخ با Schema مخصوص Task اعتبارسنجی و حداکثر یک‌بار Repair شود.
8. پاسخ نامعتبر یا شکست غیرقابل Retry به Model/Provider بعدی Fallback کند.
9. نتیجه به `AIResult` داخلی تبدیل و Attempt/Audit/Metrics ثبت شود.
10. شکست همه Providerها بدون نتیجه جعلی و طبق سیاست Task به Retry آینده، توقف، ادامه یا بررسی دستی منتهی شود.

اولین Adapterها پس از تعیین Providerهای واقعی ساخته می‌شوند. Application فقط `AIProvider` و نتیجه استاندارد را می‌بیند. Rate Limit و Circuit برای هر `Provider × Model` مستقل‌اند.

## 13. Configuration و Secret

- قرارداد Commitشدنی فقط `config/configuration.example.json` با
  `configuration_schema_version = 1` است و هیچ مقدار حساس ندارد. نسخهٔ عددی
  ناشناخته بدون Migration خودکار Fail-fast می‌شود.
- مدل‌های `ApplicationConfig` و زیرمدل‌ها در `shared.config` با Pydantic v2،
  `frozen=True`، scalarهای strict، tupleهای immutable و `extra="forbid"`
  ساخته می‌شوند. `tzdata` پذیرش پایدار IANA ZoneInfo از جمله `Asia/Tehran` را
  روی Windows و محیط‌های فاقد tzdb سیستم تضمین می‌کند.
- Config شامل MongoDB، Session path و ورودی Login، Bot/approval chat، Adminها،
  Source/Destinationها، Feature Flagها، Timezone، Logging، اسکلت AI routing و
  Advertisement routing است.
- `load_configuration(Path, environ=...)` تنها API خواندن برای Composition
  Root است و `LoadedConfiguration(settings, secrets)` برمی‌گرداند. هیچ Domain،
  Application Use Case یا Adapter فایل JSON یا Environment را مستقیم نمی‌خواند.
- Loader فایل را با `encoding="utf-8"` و JSON سخت‌گیرانه می‌خواند؛ duplicate
  key، عدد غیراستاندارد، Root نامعتبر، Enum/Range/URL/ZoneInfo نامعتبر، هویت
  تکراری و reference ناشناخته رد می‌شوند. خطاهای structural، semantic و Secret
  مستقل تا سطح field/item تجمیع و با مسیر پایدار گزارش می‌شوند.
- JSON فقط `SecretReference.environment_variable` را می‌پذیرد. مقدارها فقط از
  Mapping محیطی resolve و در `ResolvedSecrets` مبتنی بر `SecretStr` نگهداری
  می‌شوند؛ `repr` و Exceptionها redacted هستند و context خام parser/validator
  به Exception عمومی متصل نمی‌ماند.
- Load هیچ Session path را باز یا canonicalize نمی‌کند و هیچ اتصال شبکه،
  MongoDB، Telegram یا AI ندارد. در نتیجه Timeout/Retry عملیاتی در T002 مصداق
  ندارد و فراخوانی تکراری با ورودی یکسان بدون side effect و idempotent است.

Dynamic reload فقط برای فاز پنجم مطرح است؛ نسخه اولیه برای تغییر Config نیاز به Restart کنترل‌شده دارد.

## 14. Logging، Retry، Idempotency و هم‌زمانی

### Logging

Log ساختاریافته حداقل `timestamp`، `level`، `event_name`، `correlation_id` و شناسه‌های موجود Post/Channel/Destination/Admin/Job را دارد. Redaction پیش از Formatter اعمال می‌شود. متن Session، Secret و Credential هرگز Log نمی‌شود.

### Retry

- هر تماس خارجی Timeout صریح دارد.
- خطاها به موقت، دائمی، Rate-limit/Flood-wait و Configuration تقسیم می‌شوند.
- Retry فقط برای خطای موقت، محدود و با Exponential Backoff و Jitter است.
- Retry Provider با Fallback بین Providerها جداست.
- شکست نهایی در Document عملیات ثبت و در صورت لازم به بررسی دستی/DLQ منتقل می‌شود.

### Idempotency

- ingest: `source_channel_id + source_message_id`؛
- انتشار عادی: کلید درخواست پایدار برای `post + destination + action`؛
- Job AI: `post + task + prompt/schema version`؛
- تبلیغ: `campaign + destination + scheduled slot`.

### Concurrency

- Atomic update و Unique Index خط دفاع اول‌اند.
- Transitionها شرط `expected_version/current_status` دارند.
- Workerها Lease دارای انقضا می‌گیرند.
- ویرایش پیام مدیران fan-out و best-effort است؛ شکست یک پیام مانع بقیه نیست.
- قفل Process-local برای صحت توزیع‌شده کافی محسوب نمی‌شود.

## 15. راهبرد تست

1. **Unit:** Domain rules، Transitionها، Normalize/Hash، Entity rebasing، Toggle، Slot calculation، Retry classification، AI schema/fallback و Permission.
2. **Integration:** Adapter MongoDB با Index/Atomicity/TTL semantics، Media Storage، HTTP AI روی Fake server و تبدیل DTOهای Telegram.
3. **Contract:** Fixtureهای ثبت‌شده برای User API/Bot API/Providerها بدون Secret واقعی.
4. **End-to-end کنترل‌شده:** MongoDB واقعی آزمایشی و Gatewayهای Fake برای Crawl → Approval → Publish/Schedule؛ تست Sandbox تلگرام فقط با Configuration صریح و خارج از اجرای پیش‌فرض.
5. **Restart/Concurrency:** خاموش‌کردن Worker پس از Claim، انقضای Lease، رویداد تکراری و چند Worker.
6. **Security:** Callback جعلی، Admin غیرمجاز، Secret redaction، Path traversal Media و Config نامعتبر.

هر Task تست‌های متمرکز خود را دارد و Taskهای Stabilization سناریوهای بین‌لایه‌ای همان Milestone را تثبیت می‌کنند. تست زنده Provider/Telegram نباید شرط اجرای Unit Suite باشد.

## 16. مرز اولیه و توسعه آینده

| موضوع | نسخه اولیه | آینده |
|---|---|---|
| استقرار | یک Package و چند Entry Point/Worker قابل اجرای مشترک | تفکیک Process/Service فقط با نیاز مقیاس |
| Media | Storage محلی خصوصی | Object Storage |
| حساب Telegram | یک حساب Premium | چند حساب |
| Config | فایل + Environment، Restart برای تغییر | پنل و Reload پویا |
| مدیریت | Bot API | Web Panel/API |
| تحلیل | نیازهای AI فاز اول و دوم | غنی‌سازی، ترجمه، تحلیل عملکرد |
| Messaging داخلی | Mongo durable jobs | Broker فقط در صورت اثبات نیاز |

## 17. ابهام‌های باز

این موارد نباید هنگام پیاده‌سازی بی‌صدا تصمیم‌گیری شوند:

1. SDKهای Telegram User API و Bot API تعیین نشده‌اند؛ انتخاب آن‌ها در Task مرتبط و پس از بررسی سازگاری با سیاست Python در T001 انجام می‌شود.
2. رفتار دکمه «فوری» هم Toggle انتخاب و هم آغاز فوری انتشار توصیف شده؛ وجود یا نبود مرحله Confirm باید روشن شود.
3. `minimum AI score` برای پیشنهاد کانال مبدا با امتیازدهی حداقل ۲۰ دقیقه بعد و امکان ارسال زودتر برای تأیید تعارض زمانی دارد.
4. Providerها، Modelها، روش Auth، Quota و Schema واقعی AI مشخص نشده‌اند.
5. بخش ۷ Fallback چندمدلی را آینده می‌نامد، اما بخش ۱۱ آن را با معیار پذیرش الزام‌آور می‌کند؛ این نقشه‌راه بخش ۱۱ را برای فاز اول ملاک گرفته است.
6. مقصد پیام تأیید می‌تواند گروه/کانال مشترک یا گفت‌وگوی جداگانه هر مدیر باشد؛ حالت‌های لازم و UX نهایی مشخص نیست.
7. سیاست پیش‌فرض شکست AI برای هر Task و مسیر دقیق «بررسی دستی» مشخص نشده است.
8. آستانه «بسیار نزدیک»، آستانه معنایی اولیه و رفتار Duplicate (رد یا دستی) مقدار قطعی ندارند.
9. Storage اولیه محیط Production (دیسک پایدار یا Object Storage)، سقف حجم Media و ظرفیت ۱۴روزه معلوم نیست.
10. رفتار با Edit/Delete پیام منبع و پیام‌های Forwardشده تعریف نشده است.
11. سیاست Collision تبلیغ و پست عادی گزینه‌ها را نام می‌برد ولی Default قطعی ندارد.
12. رفتار Cache تبلیغ پس از Edit منبع قابل تنظیم است، اما Default و بازه Refresh تعیین نشده‌اند.
13. سطح دسترسی/Roleهای Admin، Commandهای گزارش و عملیات Reject صریح تعریف نشده‌اند.
14. فازهای سوم تا پنجم پیشنهادی‌اند و معیار پذیرش، UX، داده و اولویت قطعی ندارند؛ T055 تا T057 ابتدا آن‌ها را قابل برنامه‌ریزی می‌کنند.

ابهام‌های 1 تا 14 در زمان فعال‌شدن Task مرتبط باید حل و در `docs/DECISIONS.md` یا Requirement اصلاح‌شده ثبت شوند؛ هیچ‌کدام مانع T001 نیست.
