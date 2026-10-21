# Current project state

- **Current milestone:** Milestone 15 — Production Incident Hardening.
- **Active task:** None; T102 implementation is verified and awaiting review/operational authorization.
- **Last completed task:** [T102 — Ingestion CPU and history recovery](tasks/T102-ingestion-cpu-and-history-recovery.md).
- **Known blockers:** Production deployment/restart is explicitly unauthorized; runtime must remain stopped with restart policy `no`.
- **Known failing tests:** None in the final matrix (2,141 passed per Python version). Earlier intermittent unchanged-code failures and a Python 3.14 SQLite warning are documented in T102.
- **Branch:** `main`, based on `aca33c9`; T102 changes are uncommitted.
- **Last verified commit:** `aca33c9` (T101); the T102 working tree passed its recorded local verification.
- **Next recommended action:** Review T102 and authorize a scoped runtime/media-cleanup rollout only when ready; do not restart production beforehand.
