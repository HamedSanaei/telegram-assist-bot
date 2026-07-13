# وضعیت فعلی

- **Current milestone:** Milestone 5 — پلتفرم AI و قابلیت‌های فاز اول
- **Active task:** [T034 — قرارداد AI، Schema و Prompt version](tasks/T034-ai-contracts-schemas-prompts.md)
- **Last completed task:** [T064 — Operational runtime lifetime supervision](tasks/T064-operational-runtime-lifetime.md)
- **Known blockers:** هیچ‌کدام.
- **Failing tests:** هیچ‌کدام؛ suite کامل non-live روی Python 3.12 برابر `890 passed` و `0 skipped` با Branch Coverage برابر `90.2317%` است.
- **Last verified commit:** `368b186`؛ تغییرات کامل و راستی‌آزمایی‌شدهٔ T064 هنوز Commit نشده‌اند.
- **Next recommended action:** ابتدا صف live را read-only بررسی و سپس lifetime فرمان `runtime` را دستی آزمون کنید؛ پس از آن T034 فعال را ادامه دهید.
