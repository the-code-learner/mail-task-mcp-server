# Changelog

Postmaster MCP follows Semantic Versioning for stable releases. Every stable release should update `VERSION`, this changelog, and publish an immutable Git tag/release named `vX.Y.Z`.

## 9.2.1 - 2026-08-18

### Added
- `POSTMASTER_CHECK_UPDATES_ON_START` controls whether `POSTMASTER_VERSION=latest` checks GitHub Releases on each container start.
- `build_status` reports the update-check and force-refresh policy alongside the requested/resolved version.

### Changed
- With `POSTMASTER_CHECK_UPDATES_ON_START=false`, `latest` reuses the currently cached source without a remote update lookup; if no usable cached source exists yet, Postmaster resolves `latest` once so first boot can succeed.
- Explicit release/tag/commit selections remain pinned and do not require a latest-release lookup.

### Fixed
- Cached fallback after a failed GitHub release lookup preserves the cached resolved ref for build identity when available.

## 9.2.0 - 2026-08-18

### Added
- Native ChatGPT file inputs through `_meta["openai/fileParams"]`, so attached files can be transferred directly to the persistent file store without routing large Base64 strings through model context.
- Safe remote-file download path with HTTPS-only URLs, redirect limits, public-address checks, timeouts, streaming size enforcement, and existing filename/store integrity protections.
- `POSTMASTER_VERSION` bootstrap policy: `latest` follows the latest stable GitHub Release, while an explicit version/tag/commit stays pinned.
- `VERSION` and `CHANGELOG.md` as the canonical in-repository version history.

### Changed
- `build_status` reports both the application version and the requested/resolved deployment revision.
- The single Portainer YAML can remain unchanged across future stable upgrades when `POSTMASTER_VERSION=latest` is selected.

## 9.1.0 - 2026-08-17

### Added
- Persistent scoped small-file store backed by SQLite metadata and SHA-256 content-addressed blobs under `/data`.
- MCP CRUD/read tools for text and Base64 files plus WebGUI upload/download/delete support.
- File-store limits, integrity checks, deduplication, path traversal protection, and regression tests.

### Fixed
- `build_status` no longer reports `unknown` in normal pinned bootstrap deployments.
- Regression coverage for multiple memories sharing the same tag.

## 9.0.0 - 2026-08-17

### Added
- Multi-file Python source tree while preserving one-YAML Portainer deployment.
- Persistent memories, skills, project context, revisions, audit, import/export, FTS5 and compact Model2Vec semantic retrieval.
- Multilingual 128-dimensional int8 context model with verified release artifact.
- Expanded MIME parsing for forwarded mail, rich HTML bodies, links and `message/rfc822` traversal.
- CI coverage for runtime import, bootstrap, model provisioning, MIME regressions and knowledge operations.

### Changed
- Public project naming and configuration became provider-agnostic.
