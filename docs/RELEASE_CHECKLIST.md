# v1.1.5 Release Checklist

- [x] T100–T102 changes reviewed; T102 committed after fresh 2141-test regression.
- [x] Version, lockfile, package, image, installers and manager all agree on 1.1.5.
- [x] Local quality, release/distribution checks and source-text/secret checks pass.
- [ ] GitHub Quality succeeds for the exact release source.
- [ ] Immutable v1.1.5 tag matches pyproject.toml and release commit.
- [ ] amd64/arm64 image, SBOM/provenance and GitHub Release assets are published.
- [ ] Release asset SHA256SUMS and image version/revision/digest are verified.
- [ ] kingofilter database/config/session/media backup is present and validated.
- [ ] Scoped runtime and cleanup rollout preserves other container identities.
- [ ] Runtime readiness, bounded source progress, cleanup and idle samples verified.

The owner authorized commit, publication and the kingofilter update on 2026-10-21.
Only runtime and media-cleanup-worker are in scope. Keep runtime restart=no while
observing; preserve database/session/media volumes, config and the previous image.
Do not run whole-stack update or manual media retry. See T103 for executed
commands, immutable references, backup record and remaining limitations.
