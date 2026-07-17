# وضعیت فعلی

- **Current milestone:** Milestone 5 — پلتفرم AI و قابلیت‌های فاز اول
- **Active task:** [T037 — Adapter Provider دوم و Model جایگزین](tasks/T037-second-ai-provider-adapter.md)
- **Last completed task:** [T036 — Adapter اولین Provider منتخب](tasks/T036-first-ai-provider-adapter.md)
- **Known blockers:** فرمان اجباری Ruff به سه خطای import ازپیش‌موجود در فایل‌های بدون تغییر T036 (`z_ai.py` و `test_first_provider_adapter.py`) برخورد می‌کند؛ دستور فعلی اجازهٔ تغییر کد نامرتبط صرفاً برای lint را نمی‌دهد.
- **Failing tests:** هیچ‌کدام؛ suite کامل non-live روی Python 3.12 برابر `1059 passed` با موفقیت اجرا شد.
- **Last verified commit:** `7abb687` (تکمیل تسک T034)
- **Next recommended action:** تعیین تکلیف blocker پایهٔ Ruff بدون گسترش دامنهٔ T037؛ پس از عبور همهٔ gateها، تکمیل T037 و سپس اجرای T038.
