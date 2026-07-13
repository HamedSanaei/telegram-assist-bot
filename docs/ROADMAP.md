# نقشه‌راه پیاده‌سازی

## قواعد

- منبع حقیقت نیازمندی‌ها `docs/REQUIREMENTS.md` است.
- تنها یک Task می‌تواند `Active` باشد؛ اکنون فقط T034 فعال است.
- وضعیت‌ها: `Active`، `Planned`، `Blocked`، `Completed`.
- هر Task باید در یک Session قابل پیاده‌سازی و راستی‌آزمایی باشد.
- Taskهای Stabilization رفتار جدید گسترده نمی‌سازند؛ سناریوهای بین‌لایه‌ای مشخص را تست و اشکال‌های همان Milestone را رفع می‌کنند.
- فازهای پیشنهادی ۳ تا ۵ پیش از ساخت Feature به Gate تعریف محصول می‌روند تا Requirement جدید اختراع نشود.

## Milestone 0 — پایه قابل اجرا

هدف: Package، Configuration، Domain، MongoDB و Observability قابل تست.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T001](tasks/T001-project-bootstrap.md) | Bootstrap پروژه و Quality Gateها | — | `1`، `3`، `15` | Completed |
| [T002](tasks/T002-configuration-system.md) | Configuration و Secret Validation | T001 | `4`، `14` | Completed |
| [T003](tasks/T003-post-domain-lifecycle.md) | مدل Domain و چرخه عمر Post | T001 | `3`، `5.4`، `10` | Completed |
| [T004](tasks/T004-mongodb-idempotent-posts.md) | MongoDB و Persistence یکتای Post | T002، T003 | `5.3`، `5.4` | Completed |
| [T005](tasks/T005-observability-retry-foundation.md) | Logging، خطا و Retry foundation | T001، T002 | `12`، `13`، `14` | Completed |
| [T006](tasks/T006-foundation-startup-stabilization.md) | Startup و Stabilization پایه | T002، T004، T005 | `4`، `12`، `13` | Completed |

## Milestone 1 — دریافت متن از Telegram User API — Completed

هدف: یک پیام متنی امروز و رویداد زنده، بدون تکرار، در MongoDB ذخیره شود.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T007](tasks/T007-telegram-session-authentication.md) | ورود و ذخیره Session | T006 | `5.1`، `14` | Completed |
| [T008](tasks/T008-session-validation-channel-access.md) | اعتبار Session، Premium و دسترسی کانال | T007 | `5.1`، `5.2` | Completed |
| [T009](tasks/T009-crawl-today-text-posts.md) | خزش پیام‌های متنی امروز یک کانال | T004، T008 | `5.2` | Completed |
| [T010](tasks/T010-live-text-listener.md) | Listener زنده پیام متنی | T009 | `5.2` | Completed |
| [T011](tasks/T011-concurrent-idempotent-ingestion.md) | هم‌زمانی Crawl/Listener و Idempotency | T009، T010 | `5.3`، `5.21` | Completed |
| [T012](tasks/T012-ingestion-restart-stabilization.md) | تست Restart و Stabilization دریافت | T011 | `5.1–5.4`، `16.1–16.5` | Completed |

## Milestone 2 — Media و آماده‌سازی محتوا — Completed

هدف: Post کامل تا مرحله آماده برای تأیید، با Media، Album، Duplicate و Entity درست.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T013](tasks/T013-media-download-storage.md) | دانلود و ذخیره انواع Media | T012 | `5.5` | Completed |
| [T014](tasks/T014-media-retention-cleanup.md) | انقضا و Cleanup فایل‌های Media | T004، T013 | `5.4`، `5.5` | Completed |
| [T015](tasks/T015-media-group-aggregation.md) | تجمیع Album/Media Group | T011، T013 | `5.6` | Completed |
| [T016](tasks/T016-exact-duplicate-detection.md) | Normalize و Duplicate دقیق | T004، T012 | `5.9` | Completed |
| [T017](tasks/T017-destination-text-pruning-entities.md) | پاک‌سازی مقصدی و بازسازی Entity | T003 | `5.7`، `5.10` | Completed |
| [T018](tasks/T018-baseline-categorization.md) | دسته‌بندی پایه و Override | T002، T003 | `5.11` | Completed |
| [T019](tasks/T019-content-preparation-stabilization.md) | Stabilization آماده‌سازی محتوا | T014–T018 | `5.4–5.11`، `5.21` | Completed |

## Milestone 3 — تعامل مدیران و تأیید — Completed

هدف: Post پیشنهادی فقط برای مدیر مجاز نمایش یابد و وضعیت انتخاب همه مدیران یکسان بماند.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T020](tasks/T020-bot-admin-authorization.md) | Bot API و Authorization مدیر | T019 | `5.13`، `14` | Completed |
| [T021](tasks/T021-secure-callback-tokens.md) | Callback امن و غیرقابل جعل | T004، T020 | `5.13`، `5.14`، `14` | Completed |
| [T022](tasks/T022-approval-message-delivery.md) | هدر و محتوای پیام تأیید | T013، T019، T020 | `5.12` | Completed |
| [T023](tasks/T023-destination-keyboard.md) | Keyboard دو ستونی مقصدها | T002، T021، T022 | `5.14` | Completed |
| [T024](tasks/T024-atomic-destination-toggle.md) | Toggle اتمیک حالت مقصد | T003، T004، T023 | `5.15` | Completed |
| [T025](tasks/T025-multi-admin-synchronization.md) | همگام‌سازی پیام تمام مدیران | T022، T024 | `5.16` | Completed |
| [T026](tasks/T026-approval-flow-stabilization.md) | Stabilization جریان تأیید | T021–T025 | `5.12–5.16` | Completed |

## Milestone 4 — انتشار فوری و زمان‌بندی پایدار — Completed

هدف: انتشار User API برای متن/Media و صف مستقل هر مقصد با بازیابی Restart.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T027](tasks/T027-immediate-text-publication.md) | انتشار فوری متن با User API | T008، T017، T026 | `5.17` | Completed |
| [T028](tasks/T028-immediate-media-album-publication.md) | انتشار Media/Album و Premium Emoji | T015، T027 | `5.5–5.7`، `5.17` | Completed |
| [T029](tasks/T029-publication-idempotency-retry.md) | Idempotency و Retry انتشار | T004، T005، T027، T028 | `5.17`، `13` | Completed |
| [T030](tasks/T030-destination-schedule-calculation.md) | محاسبه اتمیک صف هر مقصد | T002، T004، T024 | `5.18` | Completed |
| [T031](tasks/T031-persistent-schedule-worker.md) | Worker پایدار، Lease و بازیابی | T008، T029، T030 | `5.18` | Completed |
| [T032](tasks/T032-schedule-cancellation.md) | لغو و سیاست Recompaction | T025، T030، T031 | `5.19` | Completed |
| [T033](tasks/T033-scheduling-restart-stabilization.md) | Stabilization زمان‌بندی/Restart | T031، T032 | `5.18–5.19`، `16.18–16.21` | Completed |

## Milestone 5 — پلتفرم AI و قابلیت‌های فاز اول

هدف: AI Job پایدار، چند Provider/Model، Fallback و سه رفتار AI الزامی فاز اول.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T034](tasks/T034-ai-contracts-schemas-prompts.md) | قرارداد AI، Schema و Prompt version | T002، T003 | `11.1–11.5`، `11.11`، `11.16` | Active |
| [T035](tasks/T035-durable-ai-job-queue.md) | صف AI پایدار، اولویت و Lease | T004، T034 | `11.13`، `11.14`، `11.18` | Planned |
| [T036](tasks/T036-first-ai-provider-adapter.md) | Adapter اولین Provider منتخب | T005، T034 | `11.2–11.5` | Planned |
| [T037](tasks/T037-second-ai-provider-adapter.md) | Adapter Provider دوم و Model جایگزین | T036 | `11.1`، `11.2`، `11.10` | Planned |
| [T038](tasks/T038-ai-response-normalization.md) | Validation، Repair و Normalization | T034، T036، T037 | `11.4`، `11.5`، `11.11` | Planned |
| [T039](tasks/T039-ai-routing-retry-fallback.md) | Routing، Retry، Fallback و شکست نهایی | T035، T038 | `11.3`، `11.6`، `11.9`، `11.10`، `11.12` | Planned |
| [T040](tasks/T040-ai-rate-limit-circuit-breaker.md) | Rate limit، Cooldown و Circuit Breaker | T035، T039 | `11.7`، `11.8`، `11.18` | Planned |
| [T041](tasks/T041-ai-cache-audit-metrics.md) | Cache، Audit و آمار Provider | T004، T039، T040 | `11.15–11.17` | Planned |
| [T042](tasks/T042-advertisement-detection.md) | تشخیص تبلیغ و سیاست شکست | T019، T035، T039، T041 | `5.8` | Planned |
| [T043](tasks/T043-semantic-duplicate-detection.md) | Duplicate معنایی ۱۴روزه | T016، T035، T039، T041 | `5.9` | Planned |
| [T044](tasks/T044-ai-categorization.md) | دسته‌بندی AI با Fallback پایه | T018، T035، T039 | `5.11`، `11.12` | Planned |
| [T045](tasks/T045-delayed-ai-scoring.md) | امتیازدهی تأخیری و ویرایش هدر | T022، T025، T035، T039 | `5.20` | Planned |
| [T046](tasks/T046-ai-pipeline-stabilization.md) | Stabilization کامل Pipeline AI | T040–T045 | `11.19` | Planned |
| [T047](tasks/T047-phase-one-end-to-end.md) | پذیرش End-to-end فاز اول | T012، T019، T026، T033، T046 | `5.21`، `16` | Planned |

## Milestone 6 — تبلیغات زمان‌بندی‌شده

هدف: Campaignهای Config-driven با Slot پایدار، انتشار یکتا و گزارش مدیریتی.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T048](tasks/T048-advertisement-configuration.md) | مدل و Validation تنظیم تبلیغات | T002، T047 | `6`، `6.2` | Planned |
| [T049](tasks/T049-fetch-cache-advertisement-post.md) | دریافت/Cache پست تبلیغ از URL | T008، T013، T015، T048 | `6.1` | Planned |
| [T050](tasks/T050-advertisement-slot-scheduling.md) | ساخت Slotهای چندزمانه و پایدار | T031، T048، T049 | `6.2`، `6.3` | Planned |
| [T051](tasks/T051-idempotent-advertisement-publication.md) | انتشار یکتا، Retry و Audit تبلیغ | T029، T050 | `6.3`، `6.5` | Planned |
| [T052](tasks/T052-advertisement-queue-collision.md) | سیاست تداخل تبلیغ و صف عادی | T033، T051 | `6.4` | Planned |
| [T053](tasks/T053-advertisement-admin-reports.md) | گزارش امروز، آینده و خطاها | T020، T051 | `6.5` | Planned |
| [T054](tasks/T054-phase-two-end-to-end.md) | پذیرش End-to-end فاز دوم | T052، T053 | `17` | Planned |

## Milestone 7 — Gateهای قابلیت‌های پیشنهادی

این Milestone عمداً Feature Code ندارد. خروجی هر Gate، Requirement قابل آزمون و Taskهای کوچک جدید است؛ تا آن زمان Scope پیشنهادی وارد پیاده‌سازی نمی‌شود.

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T055](tasks/T055-refine-phase-three-scope.md) | تعریف محصولی تحلیل و اولویت‌بندی هوشمند | T054 | `7` | Planned |
| [T056](tasks/T056-refine-phase-four-scope.md) | تعریف محصولی تحلیل عملکرد | T054 | `8` | Planned |
| [T057](tasks/T057-refine-phase-five-scope.md) | تعریف محصولی پنل و اتوماسیون | T054 | `9`، `14` | Planned |

## Maintenance

| ID | Task | وابستگی | نیازمندی | وضعیت |
|---|---|---|---|---|
| [T058](tasks/T058-local-inline-secret-configuration.md) | Secret مستقیم در Local Config | T002 | `4`، `14` | Completed |
| [T059](tasks/T059-source-channel-username-only.md) | کانال مبدا فقط با Username | T008، T012 | `5.1`، `5.2`، `14` | Completed |
| [T060](tasks/T060-runtime-media-content-wiring.md) | Runtime media ingestion و content preparation | T012–T019 | `5.2`–`5.11`، `13`، `14`، `16` | Completed |
| [T061](tasks/T061-operational-approval-publication-runtime.md) | Operational approval bot and publication orchestration | T020–T033، T060 | `5.12`–`5.19`، `13`، `14`، `16` | Completed |
| [T062](tasks/T062-runtime-publication-visibility-approval-ux.md) | Runtime publication visibility and approval proposal UX | T020–T033، T060، T061 | `5.12`–`5.19`، `13`، `14`، `16` | Completed |
| [T063](tasks/T063-operational-runtime-startup-order.md) | Operational runtime startup ordering | T009–T012، T029–T033، T060–T062 | `5.2`، `5.17`، `5.18`، `13`، `14`، `16` | Completed |
| [T064](tasks/T064-operational-runtime-lifetime.md) | Operational runtime lifetime supervision | T009–T012، T029–T033، T060–T063 | `5.2`، `5.17`، `5.18`، `13`، `14`، `16` | Completed |
| [T065](tasks/T065-album-finalization-isolation.md) | Album finalization identity and failure isolation | T013–T019، T060، T063، T064 | `5.2`، `5.5`–`5.7`، `13`، `14`، `16` | Completed |

## بازبینی وابستگی‌ها

- هیچ Task به Task با شماره بزرگ‌تر وابسته نیست.
- هر Milestone یک خروجی قابل مشاهده و حداقل یک Task Stabilization دارد.
- MongoDB پیش از Ingest، User API پیش از Crawl، Bot API پیش از Approval، Publication پیش از Scheduler و AI Job قبل از Featureهای AI قرار گرفته‌اند.
- Media Group پیش از انتشار Album و دریافت تبلیغ Album تکمیل می‌شود.
- Phase 2 فقط پس از قبولی فاز اول آغاز می‌شود.
- Gateهای فازهای پیشنهادی، جزئیات محصولیِ موجودنشده را به Feature Task جعلی تبدیل نمی‌کنند.

## پوشش سطح بالا

| محدوده Requirement | Taskها |
|---|---|
| `1–4` | T001–T006 |
| `5.1–5.4` | T003، T004، T007–T012 |
| `5.5–5.11` | T013–T019، T042–T044 |
| `5.12–5.16` | T020–T026 |
| `5.17–5.19` | T027–T033 |
| `5.20–5.21` | T045، T047 |
| `6` و `17` | T048–T054 |
| `7–9` | T055–T057 |
| `10` | T003 و Taskهای Transition مرتبط |
| `11` | T034–T046 |
| `12–15` | T001، T002، T005 و معیارهای همه Taskها |
| `16` | T047 |

ابهام‌های مؤثر بر اجرا در بخش `17` فایل `docs/ARCHITECTURE.md` ثبت شده‌اند و هنگام فعال‌شدن Task مربوط باید حل شوند.
