# v1.1.6 Release Checklist

- [x] T104 incident traced to PyMongo transport timeout escaping native claims.
- [x] Real isolated MongoDB socket timeout reproduces and recovers on the same client.
- [x] Native transient/permanent/cancellation/publication-certainty regressions pass.
- [x] Isolated Docker crash recovery, terminal stop and administrative stop pass.
- [x] Production config/session/database backup hashes and archive integrity verified.
- [x] Final unit/integration, coverage, lint, formatting, typing and packaging pass.
- [x] Source-text and secret checks pass; only intended source/docs files committed.
- [x] GitHub Quality succeeds for the exact release source on 3.12/3.13/3.14.
- [x] Immutable v1.1.6 tag, image version/revision/digest and release assets verified.
- [x] Only Kingofilter runtime updated; every other container identity preserved.
- [x] Scoped bounded crash/boot recovery installed; persistent admin stop documented.
- [x] Production Telegram auth, Mongo heartbeat and native scheduling verified.
- [x] At least ten minutes of stable running/restart/CPU/memory samples recorded.

Owner authorized repair, release, deployment and restart. Preserve sessions,
configuration, secrets, media/database volumes, pending jobs and ambiguous outcomes.
No production failure injection or whole-daemon restart. See T104 and OPERATIONS.md
for actual evidence and stopped-code rollback.
