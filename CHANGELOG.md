# Changelog

Postmaster MCP follows Semantic Versioning for stable releases. Every stable release should update `VERSION`, this changelog, and publish an immutable Git tag/release named `vX.Y.Z`.

## 9.4.5 - 2026-08-21

### Added
- Query-time `multi-link burst` evidence for link telemetry: two or more distinct tracked links fetched with the same delivery/fingerprint within two seconds are now strong scanner/provider evidence, with sub-100 ms and sub-250 ms bursts weighted most strongly.
- WebGUI task editing for the fields already supported by `SchedulerEngine.update_job`: title, description, payload, schedule type/value, timezone and approval mode. Owner/project/action/profile identity remains read-only and completed tasks remain immutable.
- Read-only Knowledge `View` for memories and skills with sanitized Markdown rendering for headings, lists, emphasis, fenced code, links and tables. Raw source remains available through the existing Edit workflow.
- Project filters for Tasks, Knowledge and Files plus a Projects overview that combines one project's tasks, project-scoped knowledge and project-scoped files with counts and direct View/Edit/Open actions.
- Regression coverage for AMD/QNAP-style millisecond multi-link bursts, Libero-style separated manual clicks, Markdown sanitization, runtime-derived version titles, task editing and project-centric WebGUI filtering.

### Changed
- The WebGUI browser title and visible heading derive from the runtime `VERSION`/build identity instead of the historical static `v9.1` literal.
- Same-fingerprint cross-link consistency still lowers provider likelihood for separated/manual-looking clicks, but a cross-link burst within two seconds overrides that mitigation. The unique-click key remains `delivery_id + link_id + client_fingerprint`, raw rows remain unchanged and classification remains query-time.
- The v9.4.5 WebGUI layer is split into dedicated helper/task/knowledge/project modules so the MCP runtime composition stays isolated from presentation-only behavior.
- Safe Markdown rendering uses Mistune plus Bleach; this changes `requirements.txt` and therefore triggers the existing hash-based persistent-venv refresh on the next release start.

### Compatibility / deployment
- Public MCP tool names, registrations and input signatures are unchanged from v9.4.4. In particular the v9.4.2-compatible task MCP contract remains `list_jobs(owner_id=None, project_id=None, status=None, limit=200)` plus the existing single `get_job(job_id)` registration.
- Existing tracking MCP commands are unchanged; only the qualitative provider classification values/reasons returned by those existing read paths can differ because the query-time heuristic was refined.
- The new `/dashboard/job/update` route is authenticated WebGUI plumbing, not an MCP command or public callback endpoint.
- No database migration, environment variable, port, volume or Cloudflare rule is introduced. `postmaster-mcp.yml` remains unchanged.

## 9.4.4 - 2026-08-20

### Fixed
- Restored the public task MCP/API contract to v9.4.2 compatibility. `list_jobs(owner_id=None, project_id=None, status=None, limit=200)` again has no `include_completed` parameter, includes `status=completed` records by default, and uses the pre-v9.4.3 MCP serialization without the `{ok, count, jobs}` wrapper.
- Restored `SchedulerEngine.list_jobs` to the v9.4.2 signature and semantics. Completed-task visibility is no longer a scheduler/backend default and explicit owner/project/status filters behave as they did in v9.4.2.
- Removed the duplicate runtime `get_job` registration that could log `Tool already exists: get_job`. The original read-only `get_job(job_id)` tool from v9.4.2 remains the single canonical registration.
- Removed the v9.4.3 `build_status.task_detail_view` and `build_status.completed_tasks_hidden_by_default` capability flags, because completed visibility/detail is now strictly a WebGUI presentation concern.

### Added
- WebGUI-only Tasks filtering: completed tasks remain in the database and MCP results, but the Tasks page hides them by default and offers `Show completed (N)` / `Hide completed` controls.
- A `View` action for every displayed task and a read-only task-detail panel showing id, owner/project, title/description, action type, execution profile, schedule, timezone, approval mode, status, timestamps, last error and safely rendered payload.
- Dashboard-local `show_completed=1` and `view_job=<id>` navigation that preserves the Tasks tab. A completed task can be opened directly by ID even while completed rows remain hidden from the default list.
- Regression coverage for the v9.4.2 MCP schema/output contract, completed visibility in MCP versus WebGUI, single `get_job` registration, safe detail rendering, counts, Pause/Resume, scheduler status, due jobs, completion and recurring advancement.

### Compatibility / deployment
- `create_job`, `update_job`, `pause_job`, `resume_job`, `complete_job`, `delete_job`, `get_job_history`, `list_due_jobs`, `scheduler_status`, recurrence advancement, approval/security and persistent task storage are unchanged.
- No scheduler/database migration, environment variable, port, volume or Cloudflare rule is introduced.
- `postmaster-mcp.yml` remains byte-for-byte unchanged. Existing `POSTMASTER_VERSION=latest` deployments with update checks enabled can select v9.4.4 with the normal restart/redeploy after the stable release is published.

## 9.4.3 - 2026-08-20

### Added
- New read-only MCP tool `get_job(job_id)` as the task-detail equivalent of `get_memory` / `get_skill`, returning the complete stored task record including schedule, status, timestamps, errors, execution-profile reference and decoded payload.
- `list_jobs(..., include_completed=False)` visibility control. Completed tasks are hidden by default, while `include_completed=true` restores the combined active + completed view and an explicit `status="completed"` filter always returns completed tasks.
- Structured MCP list serialization for tasks as one `{ok, count, jobs}` result instead of a concatenation of individual JSON objects. Existing job record fields are preserved inside `jobs` for compatibility.
- `build_status.task_detail_view=true` and `build_status.completed_tasks_hidden_by_default=true` capability reporting.
- Regression coverage for default/explicit completed visibility, status and owner/project filters, post-filter limits, full task detail, not-found handling, persistence/counting of completed records, due-task behavior, create/complete behavior, recurring advancement and MCP serialization.

### Changed
- `list_jobs()` now treats `completed` as a read-time visibility filter only. The persisted status remains `completed`; completed records are not renamed, deleted, archived or migrated.
- The task-list `limit` is applied after completed-task visibility and all explicit owner/project/status filters, so hidden completed rows cannot consume the requested result limit.
- The dashboard's normal task listing inherits the same default completed-task hiding through the shared scheduler list implementation.

### Compatibility / deployment
- `create_job`, `complete_job`, recurring schedule advancement, `list_due_jobs`, approval/security behavior, task persistence and registry-only scheduler execution semantics are unchanged. `scheduler_status` continues to count completed records.
- No scheduler database migration is required and existing task records/payloads remain readable through `get_job`.
- `postmaster-mcp.yml` remains unchanged: no new environment variables, ports, volumes, bootstrap logic or Cloudflare changes are required.
- Deployments using `POSTMASTER_VERSION=latest` with update checks enabled can select v9.4.3 through the normal restart/redeploy after the stable release is published.

## 9.4.2 - 2026-08-20

### Added
- Explicit outbound follow-up tools `follow_up_email` and `create_follow_up_draft`, mirroring the existing reply APIs while keeping inbound replies and outbound follow-ups as separate safety semantics.
- Shared thread-recipient resolution for reply/follow-up mode. Inbound replies prefer a valid `Reply-To` and otherwise use `From`; outbound follow-ups reuse the original visible `To` and preserve the original visible `Cc` by default.
- Direction guards: `reply_email` rejects messages clearly sent by the selected sender account and tells callers to use `follow_up_email`; `follow_up_email` rejects inbound messages and points callers to `reply_email`.
- Regression coverage for recipient direction, sender/identity filtering, case-insensitive deduplication, Bcc non-disclosure, zero-recipient failures, threading headers, subject normalization, recipient authorization, drafts, tracked visible headers, clean Sent copies and attachment-byte identity.

### Changed
- Sender-owned identities are filtered from resolved `To`/`Cc` before authorization or delivery. The primary sender plus account-configured email identities are compared case-insensitively, and duplicate external recipients are removed while preserving a stable order.
- Thread subjects now normalize repeated leading `Re:` prefixes to one `Re:`. `In-Reply-To` targets the selected message's `Message-ID`, while `References` are preserved and extended without duplicating that selected ID.
- Follow-ups use the same existing outbound path as replies/sends. Tracked follow-ups therefore retain v9.4 individualized recipient MIME, visible `To`/`Cc`, clean archived Sent MIME, original URLs and identical attachment bytes without introducing a second tracking pipeline.

### Fixed
- Calling `reply_email` on an outbound/Sent message can no longer select the sender's own `From` address and create a self-reply.
- Outbound follow-ups no longer authorize or validate the sender address in place of the original external recipients.
- Original Bcc recipients are never rediscovered, inferred or re-exposed by follow-up resolution.
- A follow-up with no external recipient left after sender/identity filtering fails before any SMTP delivery.

### Compatibility / deployment
- Existing `reply_email` and `create_reply_draft` signatures remain compatible; their safe direction semantics are now explicit.
- No recipient-policy rule, tracking schema, environment variable, volume, port or public callback path changes are required.
- `postmaster-mcp.yml` remains unchanged. Deployments using `POSTMASTER_VERSION=latest` with update checks enabled can select v9.4.2 on the normal restart/redeploy after the stable release is published.
- No Cloudflare Access change is required for v9.4.2.

## 9.4.1 - 2026-08-20

### Added
- Query-time qualitative classification for link-click telemetry. Every returned `link` event can now include `provider_likelihood` (0-100), `provider_classification` (`likely_human`, `uncertain`, `likely_email_provider`, `known_email_proxy`), `provider_guess` (`google`, `microsoft`, `yahoo`, `other` or null) and human-readable `classification_reasons`.
- Combined-evidence heuristic model: explicit `GoogleImageProxy`/Gmail proxy signatures are treated as known proxy evidence; near-simultaneous second requests on the same `delivery_id + link_id`, changed fingerprints, country/browser/User-Agent changes and proxy source metadata increase provider likelihood, while a fingerprint seen consistently across multiple links in the same delivery lowers it.
- Qualitative unique-click summary fields including `likely_provider_unique_clicks`, `likely_human_or_unclassified_unique_clicks`, `uncertain_unique_clicks`, provider suspects and potential-provider share while preserving the original fingerprint unique count beside them.
- Tracking dashboard qualitative summary plus per-link-event provider score, classification, provider guess and reasons.
- `build_status.provider_qualitative_classification=true` and `tracking_status.link_tracking.provider_classification_query_time=true` capability reporting.
- Ground-truth regression fixtures for the observed Gmail duplicate-fetch pattern, stable human fingerprints across multiple links, same-fingerprint Libero-style clicks and explicit `GoogleImageProxy` traffic.

### Changed
- Provider classification is recalculated from stored click evidence on every query instead of being persisted as authoritative database state, so historical events automatically benefit from future heuristic refinements.
- `get_tracking_summary`, `get_tracking_campaign` and `list_tracking_events` expose the qualitative interpretation layer without removing or renaming the v9.4 analytics fields.

### Compatibility / deployment
- The v9.4 unique-click definition remains exactly `delivery_id + link_id + client_fingerprint`; suspicious events are never deleted, hidden or rewritten in `tracking_clicks`.
- No tracking schema migration is required and the v9.3/v9.4 single-YAML Portainer bootstrap remains unchanged.
- No new public HTTP endpoint is introduced. Cloudflare Access public bypass requirements remain `/track/open/*`, `/api/amp/*` and `/t/c/*` according to the features in use. `/files/*` remains the separate pre-existing signed file-handoff concern.

## 9.4.0 - 2026-08-19

### Added
- Per-link HTTP/HTTPS click tracking with random opaque per-delivery occurrence tokens and the public `GET /t/c/<token>` redirect endpoint. Redirect destinations are resolved only from server-side records, so query parameters cannot turn the endpoint into a generic open redirect.
- Additive `tracking_links` and `tracking_clicks` analytics tables with campaign/delivery/message correlation, recipient, original and normalized URL, destination host, anchor index/label, UTC timestamps and the existing country/source/browser/OS/User-Agent/fingerprint enrichment pipeline.
- Stable unique-click definition: `delivery_id + link_id + client_fingerprint`, with the existing keyed fingerprint fallback when network/User-Agent inputs are unavailable.
- Link analytics for total/unique clicks, unique recipients, first/last click, top links, campaign/delivery/link filtering and unified pixel/AMP/link event detail.
- MCP read tools `get_tracking_summary`, `list_tracking_links` and `list_tracking_events`; existing `tracking_status` and `get_tracking_campaign` are extended without removing legacy tools.
- Tracking dashboard `Top links` and unified `Tracking events` views while preserving the existing pixel/open dashboard sections.
- Clean Sent-copy generation for individualized tracked/AMP deliveries. Recipient MIME keeps tracking instrumentation; archived Sent MIME keeps original links and omits recipient pixel/click/AMP callback instrumentation.
- `build_status.link_tracking` and `build_status.sent_copy_tracking_sanitized` capability flags.
- `docs/LINK_TRACKING.md` covering architecture, schema, unique clicks, Sent-clean behavior, Cloudflare Access and live deployment preflight.

### Changed
- Link instrumentation is applied to the same existing tracking opt-in used by `track_opens`, preserving current per-send/account-default privacy semantics rather than adding another required send parameter.
- Tracked recipient and Sent MIME variants are built independently from the same canonical body/attachment inputs. `Message-ID`/`Date` are synchronized and normal threading headers are preserved; serialized MIME is not sanitized with fragile regex replacement.
- The v9.3 single-YAML Portainer bootstrap remains unchanged. With `POSTMASTER_VERSION=latest` and `POSTMASTER_CHECK_UPDATES_ON_START=true`, a stack restart is sufficient to select v9.4.0 after the stable release is published.

### Fixed
- Viewing a newly generated tracked message in the sender's Sent mailbox no longer loads the recipient tracking pixel.
- Clicking a link in a newly generated Sent copy no longer traverses the recipient `/t/c/<token>` URL, preventing sender self-clicks from being attributed to the recipient.

### Security / deployment
- Existing public pixel and AMP callback paths remain unchanged. v9.4 requires exactly one new anonymous Cloudflare Access bypass: `/t/c/*`.
- `/mcp`, dashboard/admin/private APIs, mail/task/memory/skill/file-management routes and tracking analytics remain protected.
- `/files/*` is a pre-existing v9.3 signed file-handoff concern and is not added to the v9.4 Cloudflare bypass policy.

## 9.3.0 - 2026-08-18

### Added
- Native Postmaster-to-client file handoff through `get_stored_file_resource(file_id, transport)` returning a real MCP `ResourceLink` content block with canonical FileStore metadata.
- `postmaster://files/{file_id}` MCP resource template; `resources/read` returns the original stored bytes and lets the MCP SDK handle `BlobResourceContents` protocol encoding.
- Signed HTTPS file handoff at `GET`/`HEAD /files/{file_id}` with HMAC-bound expiry, constant-time signature verification, direct blob streaming, byte ranges, `206 Partial Content` and `416 Range Not Satisfiable` handling.
- Optional `FILE_STORE_PUBLIC_BASE_URL`, `FILE_STORE_DOWNLOAD_SECRET` and `FILE_STORE_DOWNLOAD_URL_TTL_SECONDS` runtime overrides. The existing `PUBLIC_MCP_HOST` is reused as the normal public HTTPS host, and when no explicit secret is supplied a persistent random download secret is created under `/data`.
- `docs/FILE_HANDOFF.md` documenting the preferred transfer hierarchy and no-transcode rule.
- `build_status.native_file_resource_handoff` capability reporting.

### Changed
- The authenticated WebGUI download route now streams the canonical content-addressed blob and supports `HEAD`/ranges instead of materializing the complete file before responding.
- Runtime startup composes the existing v9.2 server with the v9.3 handoff layer while preserving all existing ChatGPT upload and generic FileStore tools.
- Version-sensitive regression tests read `VERSION` rather than embedding the current release number.
- The single-YAML bootstrap remains unchanged for v9.3; existing `POSTMASTER_VERSION=latest` deployments can receive the MCP ResourceLink/resources-read handoff on restart without a private YAML rewrite.

### Security
- A FileStore UUID/file_id alone does not authorize the public file endpoint: signed URLs require both expiry and HMAC signature.
- Signed file responses use attachment disposition, `nosniff`, private/no-store caching, bounded TTLs and never expose filesystem paths or execute stored content.

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