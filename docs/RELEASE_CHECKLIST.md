# v1.0.0 Release Checklist

- [ ] همهٔ Gateهای `quality.yml` روی Commit نهایی موفق‌اند.
- [ ] Workflow پذیرش Docker روی Linux موفق است.
- [ ] نسخه در Package، Wheel، OCI label و مستندات `1.0.0` است.
- [ ] Bundle فقط Compose، Installerها، Config نمونه و این checklist را دارد.
- [ ] Imageهای `linux/amd64` و `linux/arm64` با SBOM و provenance ساخته می‌شوند.
- [ ] Digest و `SHA256SUMS` قبل از انتشار بررسی شده‌اند.
- [ ] هیچ Config محلی، Session، Token، Password یا Private Key در Artifact نیست.
- [ ] Release notes تغییرات T078 تا T083 و محدودیت Telegram live را بیان می‌کند.
- [ ] Tag امضاشدهٔ `v1.0.0` فقط پس از تأیید دستی ساخته می‌شود.
- [ ] GitHub Release پایدار و Tagهای GHCR `1.0.0`، `1.0`، `1` و `latest` بررسی می‌شوند.

Telegram login/send در CI عمداً اجرا نمی‌شود؛ این مرزها با Fake پوشش داده شده و
smoke زنده فقط با Credential مالک Release انجام می‌شود.
