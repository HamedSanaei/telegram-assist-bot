# v1.1.0 Release Checklist

## شواهد پذیرش خودکار

- Quality Run:
  [`30329250841`](https://github.com/HamedSanaei/telegram-assist-bot/actions/runs/30329250841)
- Commit پذیرفته‌شده:
  `4a5d63c1d60305c1949adbf811d25c3d9fd319c1`
- Conclusion: `success`
- Jobهای موفق: Python `3.12`، `3.13`، `3.14` و
  `Docker and installer acceptance`

- [x] همهٔ Gateهای `quality.yml` روی Commit پذیرفته‌شده موفق‌اند.
- [x] Workflow پذیرش Docker روی Linux موفق است.
- [x] Job دقیق `Quality / Docker and installer acceptance` دو Instance مستقل،
      mutationهای `tabctl`، rollback و cleanup منابع را با موفقیت اجرا کرده است.
- [x] نسخه در Package، Wheel، OCI label و مستندات `1.1.0` است.
- [x] قرارداد Bundle فقط Compose، Installerها، Config نمونه و این checklist
      را می‌پذیرد.
- [ ] Imageهای `linux/amd64` و `linux/arm64` با SBOM و provenance ساخته می‌شوند.
- [ ] Digest و `SHA256SUMS` قبل از انتشار بررسی شده‌اند.
- [ ] هیچ Config محلی، Session، Token، Password یا Private Key در Artifact نیست.
- [x] `tabctl backup verify`، update rollback و repair dry-run روی Instance
      آزمایشی موفق‌اند.
- [x] مسیر import/repair/backup/update از Instance آزمایشی `1.0.0` با basename
      متفاوت، Config و Session و MongoDB volume را حفظ کرده است.
- [x] diagnostics archive فقط `diagnostics.json` redacted دارد و `.env`،
      Session یا Media در آن نیست.
- [ ] Kernel 6.19+ با `mongo:7.0.32` و permission non-root در Ubuntu acceptance
      موفق است.
- [ ] Release notes تغییرات Milestone 9 و محدودیت Telegram live را بیان می‌کند.
- [ ] Tag امضاشدهٔ `v1.1.0` فقط پس از تأیید دستی ساخته می‌شود.
- [ ] GitHub Release پایدار و Tagهای GHCR `1.1.0`، `1.1`، `1` و `latest` بررسی می‌شوند.

Telegram login/send در CI عمداً اجرا نمی‌شود؛ این مرزها با Fake پوشش داده شده و
smoke زنده فقط با Credential مالک Release انجام می‌شود.

Commit مستندات تکمیل T090 باید پیش از ساخت Tag Push شود و Quality روی همان
Commit نیز سبز بماند. موارد مربوط به Image، Digest، Asset، Tag و GitHub Release
فقط پس از اجرای Workflow انتشار علامت‌گذاری می‌شوند.
