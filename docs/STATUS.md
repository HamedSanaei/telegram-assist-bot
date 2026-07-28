# وضعیت فعلی

- **Current milestone:** Release v1.1.0 follow-up maintenance.
- **Active task:** [T091 — Validated GitHub Release Publication](tasks/T091-validated-github-release-publication.md)
- **Last completed task:** [T090 — v1.1.0 End-to-End Acceptance](tasks/T090-v1-1-0-end-to-end-acceptance.md)
- **Known blockers:** Push و اجرای مجدد Release workflow برای تأیید اصلاح انتشار.
- **Failing tests:** Release Run شمارهٔ
  [`30331348535`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30331348535)
  فقط در Job `Publish GitHub Release` به‌دلیل نبود Git checkout شکست خورد؛
  Validate، Package، GHCR و Release files موفق بودند.
- **Last verified commit:** `4a5d63c1d60305c1949adbf811d25c3d9fd319c1`؛
  Quality Run شمارهٔ
  [`30329250841`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30329250841)
  با Pythonهای `3.12`، `3.13`، `3.14` و Docker/Installer acceptance در
  2026-07-28 موفق شد.
- **Next recommended action:** Push کردن اصلاح T091 و اجرای
  `gh workflow run release.yml -f tag=v1.1.0` برای تکمیل Release موجود طبق
  `docs/RELEASE_CHECKLIST.md`.
