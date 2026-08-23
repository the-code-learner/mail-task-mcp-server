# Changelog

Postmaster MCP follows Semantic Versioning for stable releases. Every stable release should update `VERSION`, this changelog, and publish an immutable Git tag/release named `vX.Y.Z`.

## 9.6.6 - 2026-08-23

### Added / changed
- Added MCP-native Privacy Proxy secret provisioning through the existing `set_amp_account_state` command with additive `status`, `prepare_provisioning`, `provision`, `rotate`, `reconcile`, and `deprovision` actions; no new MCP command name is introduced.
- Postmaster now generates an Ed25519 keypair internally for provisioning trust. The private signing key is encrypted at rest and never returned; MCP exposes only the public key, key-id, and SHA-256 fingerprint for explicit Worker pinning.
- Added preview-first, short-lived one-time confirmation tokens for every mutating provisioning action. Tokens are bound to the exact action, Worker origin, key fingerprint/key-id, generation, and current pending state and are consumed after one attempt.
- Postmaster now generates the proxy HMAC secret internally, persists it encrypted as pending, and sends it directly to the Worker over a server-to-server `/provision` request signed with Ed25519. The provisioning signature binds method, path, Worker origin, timestamp, nonce, body digest, monotonic generation, operation, and key-id.
- The example Worker now fails closed unless the Ed25519 public key/key-id is explicitly pinned, verifies signatures/timestamps/nonces/generations, rejects replay and rollback/out-of-order rotations, and stores active/previous HMAC state in the existing SQLite-backed `NONCE_GUARD` Durable Object. The previous secret is accepted only during a bounded rotation grace period (120 seconds requested by Postmaster, 300 seconds hard maximum).
- Provisioning promotion is `pending -> verify -> active`: Postmaster verifies `/health` with the pending HMAC secret before replacing the local active secret. Interrupted provision/rotation remains recoverable with `reconcile`, which reuses the same encrypted pending secret and generation with a fresh signed request.
- Added provider-neutral Worker deployment documentation and a complete MCP-client wizard covering status inspection, prepare/public-key pinning, provision preview/confirmation, server-to-server provisioning, health verification, enablement, rotation, reconcile, deprovision, and interruption recovery.

### Compatibility / safety / deployment
- The composed MCP command-name surface remains exactly 90 names (`delta = 0`); v9.6.6 extends existing schemas/status only.
- Normal `/health` and `/fetch` authentication remains HMAC-SHA256 with timestamp/nonce anti-replay. Legacy `privacy_proxy_secret` input and Worker `POSTMASTER_PROXY_SECRET` fallback remain supported for existing/manual deployments.
- The existing Durable Object binding is reused; no new Cloudflare binding, port, dependency, or `requirements.txt` change is required. Local provisioning metadata is an additive SQLite table protected by the existing persistent Fernet key.
- Generated HMAC secrets and Ed25519 private keys are excluded from MCP responses, bounded errors, documentation examples, and the public tree. Worker configuration contains public-key placeholders only.
- `postmaster-mcp.yml` remains byte-for-byte unchanged at Git blob `f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9`.
- Stable source/release publication remains separate from production activation. Publishing v9.6.6 does not itself deploy/update the Worker, pin a production key, create a production secret, restart Postmaster, switch the production runtime, or change Cloudflare/Portainer configuration.

## 9.6.5 - 2026-08-23

### Fixed / changed
- Restored the richer pre-v9.6.2 WebGUI presentation on top of the existing v9.6.2 lazy-fragment shell: grouped Operate / Organize / Control navigation, icons, Domain controls / Recipient controls footer, project color accents and the v9.5.3 account palette are restored without replacing routes, handlers, renderers or the lazy fragment script.
- Bridged the v9.6.3 visual-restoration token names (`--surface` / `--border`) to the v9.6.2 shell tokens (`--card` / `--line`) and neutralized the obsolete fixed-sidebar `main` offset inside the grid shell while preserving responsive/mobile behavior.
- Added release-identity correction for the reused lazy shell: WebGUI branding now derives from the checked-out local `VERSION`, and responses expose `X-Postmaster-WebGUI` for the actually loaded release instead of a hard-coded v9.6.2 identity.
- Added production-integration regression coverage for the final composed runtime, including overlay install order, WebGUI groups/icons/footer/colors, all 18 lazy navigation targets, unchanged lazy lifecycle/AbortController/generation guard, active-target preservation, v9.6.3/v9.6.4 routes and final renderer composition.
- Added real `MCPServer` registry/schema regression coverage for the composed application, including the modern `get_email` inspection/content-mode contract, Privacy Proxy arguments on `set_amp_account_state`, v9.6.4 version-control arguments on `build_status`, and per-send `confirm_suppressed_recipients` on send/reply/follow-up.

### Compatibility / safety / deployment
- Corrective patch release only: the MCP command-name surface remains exactly 90 names (`delta = 0`); schema corrections are additive/compatibility-restoring and add no new MCP command name.
- `postmaster-mcp.yml` is unchanged; `requirements.txt` is unchanged; no persistent database/storage migration or schema change is introduced.
- Stable source/release publication remains separate from production activation. Publishing v9.6.5 does not itself deploy, restart, switch, or otherwise alter the live production runtime.
- The ChatGPT/client MCP discovery-cache mismatch remains classified separately. The release verifies the server-side composed registry/schema contract and does not claim that any stale client discovery cache has been physically invalidated.

## 9.6.4 - 2026-08-23

### Added / changed
- Added canonical outbound detracking before any new delivery tracking: historical Postmaster click wrappers are resolved locally from persisted tracking data to their authoritative original URLs, historical open pixels are removed, nested tracker generations are normalized with cycle/depth guards, unresolved active-origin tracker tokens fail closed, and no historical tracking URL is fetched over HTTP or recorded as a new analytics event during normalization.
- Applied the same canonical clean-input pipeline to sends, drafts, replies and follow-ups, tracked and untracked. Newly tracked recipient MIME receives at most the current generation of click/open instrumentation while archived Sent MIME is generated from the canonical clean body and remains free of current and historical recipient trackers.
- Made WebGUI manual recipient authorization channel-aware: syntactically valid manual recipients do not require the MCP/automated recipient allowlist, the bypass exists only inside the manual operation context, and the persistent allowlist is never mutated or widened.
- Made suppression confirmation channel-specific and per operation. WebGUI manual sends use a first-warning/no-send response followed by a second explicit confirmation for exactly the currently suppressed addresses; MCP/chat sends remain blocked before SMTP and require explicit user approval of the exact suppressed recipients before `confirm_suppressed_recipients` may be used for that single retry.
- Extended the existing `build_status` MCP command, without adding a command name, with read-only update checks/version listing and guarded runtime version-control requests. `update-latest`, explicit pin/switch and rollback requests are preview-only until the assistant shows current version/build, current selector, exact target and operation type and obtains explicit approval in the active chat.
- Added target/state-specific, process-local, short-lived one-time approval nonces for MCP version changes. A generic boolean is not sufficient; changing operation, target or current runtime state invalidates the confirmation, mismatched attempts consume it, and successful confirmations cannot be reused across later calls. `force_refresh` remains a separate read-only stable-release discovery action and never authorizes or triggers a version change.
- Reused the existing runtime-control intent, stable-release filtering, single-YAML bootstrap, atomic source staging and restart-policy path for approved MCP update/version requests rather than adding a second deployment system. Stable application selectors remain `latest` or verified stable `vX.Y.Z` releases through this runtime-control surface.

### Compatibility / safety / deployment
- MCP command surface remains exactly 90 names (`delta = 0`). Existing send/reply/follow-up idempotency keys, duplicate/`force_send` guards, recipient authorization, suppression persistence, delivery-uncertainty behavior and all existing mail safety boundaries remain in force; `confirm_suppressed_recipients` and version-change confirmation are distinct, operation-scoped controls and create no persistent bypass.
- Read-only `build_status`, update checks and stable-version listing require no version-change approval. A mutating version request never treats the tool call itself, a generic earlier instruction, or a target parameter alone as user approval; the tool description requires a new explicit active-chat approval before the exact one-time token may be retried.
- `postmaster-mcp.yml` remains byte-for-byte unchanged at Git blob `f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9`; `requirements.txt` is unchanged and no new dependency or deployment topology is introduced.
- Stable release publication and production runtime activation remain separate actions. Publishing v9.6.4 does not itself deploy, restart or version-switch production; any later real production update/version change must be separately approved and then verified from live `build_status` after bootstrap/restart completes.

## 9.6.3 - 2026-08-23

### Added / changed
- Added a persistent SQLite cache-first Inbox read model with read-only incremental IMAP synchronization based on UID/UIDVALIDITY, mailbox-level request coalescing, five-minute scheduling, explicit manual refresh, cached body-on-demand and persistent restart-safe state while keeping IMAP authoritative and the synchronizer outside every send boundary.
- Made Safe Email the zero-network default: message HTML/URLs are inventoried statically, remote/active content and navigable message URLs are suppressed, tracking evidence remains heuristic with observed evidence separated from inference, and the first **Visualizza HTML completo** action performs no message-target DNS/HTTP activity.
- Added the second explicit **Conferma e carica HTML completo** consent action. Only passive rendering resources are eligible, only through the configured Privacy Proxy, redirects remain inert status + `Location`, and successful genuine passive resources are served back to the browser from the durable local cache under restrictive CSP/sandbox rules. Navigation/action/unsubscribe/login/magic/reset/confirmation/approval/one-time-token URLs remain silent.
- Added an optional generic Cloudflare Privacy Proxy Worker template with HTTPS-only targets, shared-secret HMAC over timestamp/nonce/body digest, replay protection, canonical proxy User-Agent, header isolation, strict SSRF checks, public-DNS preflight, manual redirects, passive content-type allowlisting and bounded response handling. The browser never contacts message targets directly.
- Added explicit persisted **High-noise decoy traffic** configuration, semantically separate from tracking obfuscation and defaulting to Off for new and upgraded installations. The existing administrative MCP surface and WebGUI can independently configure proxy enablement, tracking obfuscation and high-noise without adding an MCP command name. High-noise requires tracking obfuscation and is never enabled by per-message Full HTML consent.
- High-noise runs only after the second Full HTML confirmation and only over structurally passive resource candidates. It is bounded to 4 decoy requests per message/load, 2 per domain, concurrency 2, 3-second client and Worker decoy timeouts, 64,000 bytes per decoy response, 256,000 aggregate decoy response bytes and a 7-second Postmaster execution budget. Decoys reuse the authenticated `/fetch` endpoint and the same SSRF/content/redirect protections; they never contact `<a href>` or navigation/action URLs and do not simulate human clicks.
- Decoy results are stored only as separate hashed audit metadata and never become durable render-cache entries or evidence that a resource was required for rendering. Genuine passive resources retain cache-first reuse so high-noise does not create an automatic refetch loop or invalidate the local privacy cache.
- Added Reply, Reply all and Forward Inbox UX that remains draft-oriented, preserves threading semantics, filters sender identities/aliases from Reply-all recipients, never rediscovers Bcc, and reuses existing attachment/draft paths rather than introducing an automatic-send route.
- Restored v9.6.0-inspired presentation hooks while preserving the v9.6.2 lazy-fragment cancellation/stale-response lifecycle, widened the Knowledge editor, and kept Projects CRUD/storage semantics unchanged.
- Added upgrade-safe onboarding semantics: any installation with at least one configured email account is established and never receives full first-run onboarding; established users can receive only an optional dismissible Privacy Proxy setup. Onboarding itself sends no mail, contacts no message URL and cannot enable high-noise without explicit configuration.

### Compatibility / safety / deployment
- MCP command surface remains exactly 90 names (`delta = 0`); Privacy Proxy/high-noise configuration extends the existing administrative command and existing status surfaces additively. The shared secret remains write-only/encrypted locally and is never returned in plaintext, committed to Git, placed in `postmaster-mcp.yml`, logged, or inserted into MCP initialize instructions.
- v9.6.3 introduces additive local storage for mailbox cache/resources plus Privacy Proxy configuration/decoy audit state. Existing Privacy Proxy databases migrate `high_noise_decoy_enabled` with `DEFAULT 0`; established installations therefore remain high-noise Off unless explicitly changed. No dependency or `requirements.txt` change is intended.
- Existing persistent idempotency-before-SMTP, duplicate fingerprint/`force_send` boundaries and conservative `delivery_uncertain` behavior remain unchanged; no v9.6.3 cache/proxy/onboarding path sends mail automatically.
- `postmaster-mcp.yml` remains do-not-touch and unchanged (expected Git blob `f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9`). The Worker template contains no personal URL, domain, account identifier, token or shared secret.
- Source/release work remains separate from production runtime state. v9.6.3 preparation/publication does not itself authorize or perform a production deploy/restart/update, Portainer change, Docker-topology change, Cloudflare Access/Tunnel/DNS/reverse-proxy change or real test email.

## 9.6.2 - 2026-08-23

### Added / changed
- Added full Projects WebGUI create/edit/delete over the existing persistent project registry. New Project accepts Name, an automatically suggested but pre-create editable validated project ID/slug, and an optional description; Edit changes only Name/Description and keeps `project_id` immutable.
- Project deletion is non-destructive to content: the deleted project scope is detached from Knowledge, remaining project scopes are preserved, sole-project Knowledge/File items become Unassigned, and no item is promoted to Global or moved to another project. Scheduler jobs or execution profiles that still reference the stable project ID block deletion before any detach occurs.
- Added a real two-step delete flow with impact counts for memories, skills, files, jobs and execution profiles, followed by exact `project_id` typing plus an explicit `Delete permanently` action and `Cancel`.
- Made occasional creation/edit/configuration/secondary forms collapsible across Accounts, AMP, Domains, Recipients, Projects, Knowledge, Files, Tasks and Suppressions while keeping search, filters, mailbox navigation, pagination and tab navigation continuously visible. Collapsible state is restored across fragment replacement where appropriate.
- Replaced eager all-tab dashboard rendering with a lightweight shell and per-tab lazy fragments: only the active tab is fetched initially, unopened tabs do not execute their data queries, fragment refreshes dispatch only their own view, and stale requests are aborted/generation-guarded before they can overwrite newer UI state.
- Added request coalescing and short-lived caching for structural project/owner/account reads with mutation invalidation, bounded source reads for large WebGUI inventories/event lists, batched Knowledge scope attachment to remove per-row scope lookups, and fragment `Server-Timing` observability without fragile CI timing thresholds.
- Preserved the v9.6.1 Inbox source-prefetch contract (26/51/76 rows for pages 1/2/3 with cap 100), active-fragment visibility fix, Safe Reader `inspection="full"` + `content_mode="safe"`, Sent `To`, mailbox logical roles, Knowledge multi-scope and deterministic project colors.

### Compatibility / safety / deployment
- Existing persistent idempotency-before-SMTP, duplicate heuristic/`force_send` boundaries, `delivery_uncertain` behavior, static zero-network inbound inspection and existing mail APIs are unchanged.
- MCP command surface remains exactly 90 names (`delta = 0`); no MCP command or public MCP schema is added/removed/renamed, no dependency or `requirements.txt` change is introduced, and no database/storage migration is required.
- `postmaster-mcp.yml` remains unchanged (Git blob `f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9`). Source/stable release state remains separate from production runtime state.
- No deploy, restart, Portainer or Cloudflare action is part of v9.6.2 release preparation or publication.

## 9.6.1 - 2026-08-23

### Fixed
- Fixed WebGUI progressive-fragment replacements so Inbox message detail, mailbox navigation (including Sent/Trash) and Knowledge project filters preserve the active dashboard panel instead of rendering fetched content hidden.
- Restored Inbox inline safe-reader detail with Reader / Privacy / Links / Headers / MIME views and preserved Sent `To` presentation.
- Unified logical mailbox-role fallback for hierarchical/provider mailbox names, including `INBOX.Trash`, so `mailbox_status`, `search_emails`, `get_email` and WebGUI agree on received/sent/spam/drafts/trash roles.
- Restored deterministic visible project chips/colors for Knowledge single-scope, multi-scope and global items and project filters.
- Reduced WebGUI mail-list enrichment from an unconditional 100-message prefetch to the current 25-row page plus one next-page sentinel, without changing the MCP/IMAP `search_emails` contract.

### Compatibility / safety / deployment
- Existing persistent idempotency, duplicate heuristic/`force_send`, static zero-network inbound inspection, unsubscribe GET/POST and legacy `get_email.body_html` semantics are unchanged.
- MCP command surface remains 90 names (`delta = 0`); no dependency, `requirements.txt`, database schema/storage migration or MCP-schema change is introduced.
- `postmaster-mcp.yml` is unchanged. Release and production deployment remain separate; no deploy, restart, Portainer or Cloudflare action is part of v9.6.1 release preparation.

## 9.6.0 - 2026-08-23

### Added
- Persistent outbound safety reservations in `outbound_operations`, created atomically before the underlying SMTP/send backend. Caller-supplied idempotency keys replay the prior result for the same payload, reject key reuse with a different payload, and preserve conservative `delivery_uncertain` behavior without automatic resend.
- A short-window duplicate fingerprint guard for equivalent visible outbound messages plus explicit `force_send` override for that heuristic guard only.
- Static inbound privacy inspection for remote images, tracking pixels, remote CSS/backgrounds, links/domains, redirectors, tracking parameters, anchor-text/href mismatches, `cid:`/`data:` resources and MIME/header indicators. Inspection performs no URL GET/HEAD/DNS lookups and provides Bleach-sanitized HTML.
- IMAP Special-Use/logical mailbox roles, Seen/Unseen listing state and IMAP timing metadata while preserving legacy mailbox/list contracts by default.
- Automatic signed delivery-scoped unsubscribe URLs, non-destructive GET confirmation, suppression-list POST handling and RFC One-Click `List-Unsubscribe-Post` support. Public unsubscribe URLs reuse the canonical `PUBLIC_EMAIL_BASE_URL` with `PUBLIC_MCP_HOST` fallback; no parallel public-URL configuration is introduced.
- Real many-to-many Knowledge scopes through `knowledge_item_scopes` plus `knowledge_scope_audit`, automatic legacy-primary backfill, primary owner/project reassignment and OR multi-project filtering. The WebGUI can edit additional owner/project scope pairs while retaining the legacy primary scope.
- Server-rendered Inbox/Sent/Spam/Drafts/Trash role UX with full-row navigation, unread emphasis, safe inline Reader / Privacy / Links / Headers / MIME panes, Sent tracking detail, Inbox-integrated Compose and stable form idempotency keys for double-submit protection.
- Mail Health presentation as `Mail Health — DNS & Deliverability`, separating account connectivity/TLS from domain authentication/transport policy without synthesizing a single good/bad score.

### Changed
- `get_email(...)` without `inspection` preserves the v9.5.x original `body_html` contract and explicitly marks it as original/unsanitized. `inspection="summary|full"` is sanitized by default; original HTML in inspection mode requires explicit `content_mode="raw"` plus `acknowledge_unsanitized_content_risk=true`.
- Existing `send_email`, `reply_email` and `follow_up_email` gain additive `idempotency_key` / `force_send` controls and existing newsletter options gain automatic unsubscribe behavior without adding a parallel send path.
- Existing Knowledge MCP tools retain their names while gaining additive `scopes` and/or `project_ids` arguments where applicable. Legacy `owner_id` / `project_id` remain the primary scope fields.
- The v9.6 runtime installs as another composition layer above the existing runtime and WebGUI layers. Scheduler behavior remains registry-only; no autonomous task or mail worker is introduced.

### Schema / compatibility
- New additive tables are `outbound_operations`, `knowledge_item_scopes` and `knowledge_scope_audit`; legacy Knowledge primary owner/project columns remain authoritative compatibility fields and existing rows are backfilled idempotently.
- The composed MCP surface remains exactly the existing 90 command names (`delta = 0`); v9.6 extends arguments/structured responses additively rather than adding or renaming commands.
- Task and execution-profile ownership are not converted to the Knowledge many-to-many scope model; their existing project ownership remains unchanged to avoid cross-owner authorization risk.
- No dependency or `requirements.txt` change is required.

### Security / deployment
- Inbound inspection is static-only and the WebGUI Reader always requests the sanitized inspection-aware representation; remote automatic resources are stripped from Reader HTML.
- Automatic unsubscribe requires a canonical HTTPS public email base derived from `PUBLIC_EMAIL_BASE_URL` or `PUBLIC_MCP_HOST`; opening the signed GET URL alone never creates suppression.
- `postmaster-mcp.yml` is intentionally unchanged. No deployment, restart, tag, release or merge is performed by this source-change tranche; those remain separate owner-controlled steps.

## 9.5.4 - 2026-08-22

### Added
- Integrated existing outbound tracking into `Sent`, `INBOX.Sent` and each account's configured Sent mailbox in the WebGUI. Sent rows now show `Non tracciata`, `Nessuna attività`, `Apertura rilevata`, `Link cliccato`, or multi-recipient aggregates such as `Aperti 2/3 · Click 1/3`.
- Added a read-only Tracking section to Sent message detail, grouped by delivery/recipient and showing recipient role, delivery/conversation state, observed open counts and timestamps, total/unique click metrics, first/last click, and clicked links with anchor text and original destination.

### Tracking semantics / compatibility
- Sent correlation is strictly `account_id + Message-ID`; no heuristic matching by subject, recipients or timestamps is introduced. Campaign expansion is used only after an exact correlated delivery is found so multi-recipient sends retain per-delivery detail.
- Open telemetry remains observed activity rather than proof of human reading. Existing query-time provider/scanner interpretation remains separate from authoritative raw events, and the unique-click definition remains `delivery_id + link_id + client_fingerprint`.
- The sender clean Sent copy remains free of active recipient tracking pixels and tracking redirects; SMTP/IMAP behavior, tracking storage/schema, DSN, retry/backoff, throttling, suppression, MIME, scheduler, Knowledge, File Store and recipient policy are unchanged.
- No MCP command names are added, removed or renamed; the mapped MCP surface remains 90 functions and `new_mail_mcp_commands` remains `0`.
- No database migration, dependency/`requirements.txt` change or `postmaster-mcp.yml` change is required.
- Release and production deployment remain separate; this source release does not itself restart, deploy or switch production.

## 9.5.3 - 2026-08-21

### Fixed
- Replaced free-text account IDs in Inbox, Mail Health and Compose with enabled-account selectors backed by the existing account store. Selectors show human-readable labels/email addresses, submit canonical account IDs, select the configured default account, and never render credentials.
- Inbox now automatically loads the configured default account when the Inbox view is opened, reuses the existing `search_emails`/`get_email` paths, offers real mailbox choices from `list_mailboxes`, and keeps account/mailbox/filter/detail/back state coherent when accounts change.
- Removed decorative/stale WebGUI release labels from the sidebar and tracking presentation while preserving semantic query-time provider/scanner descriptions and the unchanged unique-click definition `delivery_id + link_id + client_fingerprint`.
- Added deterministic, dark-theme-safe account colors plus visible account text to Tracking account cards so multiple accounts are distinguishable without using color as the only signal.
- Redesigned System around a concise runtime summary, store/runtime health, collapsed advanced diagnostics and truthful configuration status instead of presenting structural capability flags as fake toggles.

### Runtime administration
- Added authenticated POST + CSRF System controls for restarting the currently running stable release, requesting the latest stable application release, or selecting a specific stable `vX.Y.Z` release. Drafts, prereleases and unrelated/model releases are excluded from the selectable stable-release set; explicit downgrades require an acknowledgement warning.
- Added a small persistent runtime-control intent under the existing `/opt/postmaster` volume. The existing single-YAML bootstrap consumes only `latest` or stable `vX.Y.Z` selectors plus one-shot restart/update flags. It does not expose the Docker socket, Portainer/Cloudflare credentials or host-level privilege.
- `Restart current` uses a one-shot concrete stable ref so the restart cannot accidentally become an update. `Update to latest` forces one stable-release check. The app sends the HTTP response before terminating its own process; the already-existing `restart: unless-stopped` policy performs the container restart.
- The bootstrap remains the sole source/version installer and retains stable-release filtering, immutable release downloads, cached current/last-known-good fallback, atomic staging and the existing requirements-hash virtualenv behavior. `build_status` keeps its existing MCP name and now reports the effective bootstrap-requested selector truthfully.

### Compatibility / security / deployment
- No new MCP command names are added, removed or renamed; the mapped MCP surface remains 90 functions and `new_mail_mcp_commands` remains `0`.
- SMTP/IMAP protocol implementation, DSN, retry/backoff, throttling, suppression, MIME parsing, tracking calculations/raw rows, scheduler, Knowledge, File Store, recipient policy and database schemas are unchanged. No dependency or `requirements.txt` change is required.
- `postmaster-mcp.yml` changes only to let the existing bootstrap read/clear the safe runtime-control intent before executing the same release selection/download/start path. No new port, volume, public callback endpoint or secret is introduced.
- Production release/deployment remain separate; this release work does not itself redeploy or restart production.

## 9.5.2 - 2026-08-21

### Fixed
- Fixed the Mail Health browser refresh flow by registering the authenticated dashboard route before the catch-all MCP mount. Refresh remains a CSRF-verified POST, reuses the existing `test_email_account` diagnostics with `refresh=True`, and returns to the structured Mail Health view; defensive GET navigation redirects without executing a refresh.
- Fixed Inbox search and message-detail browser navigation to reuse the existing `search_emails` and `get_email` paths with real message UIDs while preserving account, mailbox, subject, text, since-days and unread-only state across View and Back-to-results navigation.
- Fixed dashboard URL composition so empty query parameters are removed, query strings precede fragments, Inbox does not inherit project scope, and project-scoped Tasks / Knowledge / Files / Projects views retain their non-empty project filter.
- Added route-level browser regression coverage for Mail Health POST/GET behavior and CSRF, Inbox argument forwarding and UID/detail navigation, state preservation, URL canonicalization and route ordering before the catch-all mount.

### Compatibility / security / deployment
- **WebGUI-only patch:** no MCP command names are added, removed or renamed; SMTP/IMAP protocol, DSN, retry/backoff, throttling, suppression, tracking, MIME, scheduler, Knowledge, File Store, account-store and recipient-policy semantics are unchanged.
- No database schema or migration, dependency/`requirements.txt`, workflow, environment variable, port, volume, Cloudflare, Portainer/bootstrap or `postmaster-mcp.yml` change is introduced.
- No new public callback endpoint is added. The Inbox search helper is authenticated dashboard navigation only, and Mail Health continues through the existing CSRF verification path.

## 9.5.1 - 2026-08-21

### Added
- WebGUI-only navigation and information architecture aligned with the approved redesign: Dashboard, Accounts, Mail Health, Inbox, Compose, Tracking, Deliveries, Suppressions, Projects, Tasks, Knowledge, Files, Security, AMP, System and MCP Coverage, while keeping the pre-existing domain/recipient controls reachable.
- Tasks Agenda/Calendar presentation over the same real task-registry rows and persisted `next_run_utc`, with deterministic project colors shared across Tasks, Knowledge, Files and Projects. Calendar rendering does not synthesize recurring executions or imply an autonomous scheduler.
- Shared 1D / 7D / 30D / 90D / All time controls for genuinely chronological Dashboard, Tracking and Deliveries data. Filtering uses existing persisted campaign, delivery, open/link-event and retry-attempt timestamps; point-in-time inventories and runtime/store status remain unfiltered snapshots.
- Structured Mail Health cards with Raw / Details troubleshooting, Inbox search/message presentation, existing-send Compose UI, delivery/retry views, current suppressions, Security summary, System snapshots and an explicit MCP Coverage map of the existing 90-function surface.
- WebGUI regression coverage for navigation, chronological-window/All-time behavior, snapshot preservation, tracking truthfulness, CSRF-protected Compose reuse, deterministic project colors and provider-neutral presentation.

### Changed
- Tracking now surfaces range-filtered observed activity while keeping raw events authoritative, the unique-click definition `delivery_id + link_id + client_fingerprint` unchanged, provider/scanner classification query-time only, and pixel/open telemetry explicitly described as not proof of human reading.
- Mail Health presentation distinguishes capability/configuration observations from runtime failures and does not fabricate DKIM failures, optional-standard failures, pure TLS-handshake latency or historical health series.
- Suppressions remain a current inventory because the existing v9.5.0 service does not expose suppression-event history through a read API; the WebGUI does not reconstruct or invent that history.
- The v9.5.1 layer composes the existing server-rendered dashboard incrementally instead of replacing backend services or introducing a parallel runtime.

### Compatibility / security / deployment
- **WebGUI-only patch:** no MCP command names are added, removed or renamed; mail, tracking, suppression, scheduler, Knowledge, File Store, account-store and security-policy semantics are unchanged.
- The Compose presentation reuses the existing `send_email` path and existing recipient authorization/suppression/retry/DSN/newsletter semantics; the new WebGUI POST continues through `_verified_form` and carries the existing CSRF token.
- No database schema change, migration, new worker, new dependency, new public callback path, environment variable, deployment/bootstrap change or production update is introduced.
- The public tree remains provider-neutral and uses synthetic test data. `postmaster-mcp.yml` is intentionally byte-for-byte unchanged.

## 9.5.0 - 2026-08-21

### Added
- Provider-independent SMTP capability discovery for `SIZE`, `8BITMIME`, `SMTPUTF8`, `PIPELINING`, `DSN`, `STARTTLS`, advertised AUTH mechanisms and unknown extensions, plus IMAP capability discovery for `IDLE`, `MOVE`, `UIDPLUS`, `SPECIAL-USE`, `NAMESPACE`, `QUOTA`, `CONDSTORE`, `QRESYNC`, `SORT`, `THREAD` and unknown capabilities.
- RFC 3461 DSN support with xtext encoding, `ENVID`, per-recipient `ORCPT`, default `NOTIFY=FAILURE,DELAY`, explicit-only `SUCCESS`, graceful fallback when a server does not advertise DSN, multipart/report parsing and conservative textual bounce fallback.
- Provider-independent sender/mailbox health covering MX, SPF and recursive lookup count, DMARC, selector-explicit DKIM, MTA-STS, TLS-RPT, DANE TLSA, optional BIMI, IMAP quota, TLS socket/cipher/certificate metadata and cached account diagnostics. STARTTLS reports both pre-TLS and post-TLS SMTP capability snapshots.
- Delivery reliability state with bounded exponential retry/backoff, global/account/domain throttling, SMTP failure classification, post-DATA delivery-uncertainty protection, local idempotency guard, delivery-attempt history, recipient suppressions, DSN correlation, human-reply versus auto-reply state, delivery/reply/suppression/domain metrics and IMAP IDLE with reconnect/re-IDLE/poll fallback.
- MIME/header diagnostics for authentication results, List-* fields, observed spam/junk metadata and sent-versus-received comparison. The WebGUI adds authenticated mail-health and suppression controls using the existing CSRF verification pattern.
- Regression coverage for newsletter headers, tracking without newsletter semantics, DSN options/correlation/fallback, retry/permanent/uncertain failures, suppression policy, IMAP IDLE lifecycle/timing, UID range searches, SMTP/IMAP capabilities, STARTTLS pre/post observations, truthful latency semantics, CSRF and MCP surface preservation.

### Changed
- Existing `test_email_account` and `mailbox_status` diagnostics are extended with capability, quota, DNS and TLS health instead of adding parallel MCP commands.
- Existing `send_email`, `reply_email`, `follow_up_email`, `create_draft`, `create_reply_draft` and `create_follow_up_draft` accept additive explicit newsletter/unsubscribe options. Send/reply/follow-up also accept an additive explicit DSN-success request; default DSN notification remains failure/delay only.
- `List-Unsubscribe` and `List-Unsubscribe-Post` are emitted only from explicit newsletter context. **Tracking alone does not imply newsletter.** One-click unsubscribe requires an explicit HTTPS unsubscribe URL and explicit one-click configuration.
- Existing `list_tracking_deliveries`, `get_tracking_summary`, `tracking_status` and `build_status` are enriched with delivery/reliability state while preserving the existing open/click telemetry and query-time provider-classification model.
- SMTP/IMAP latency reporting distinguishes an observed TCP connection probe from protocol connection/auth/TLS aggregates. Pure TLS handshake latency is intentionally not fabricated; STARTTLS exposes the observed upgrade command-plus-handshake latency while the pure handshake field remains unavailable.

### Schema / compatibility
- `tracking_deliveries` receives additive idempotent fields when that table exists: `delivery_state`, `attempt_count`, `last_attempt_at`, `next_retry_at`, `last_error_classification`, `bounce_classification`, `bounce_status`, `bounce_diagnostic`, `conversation_state`, `replied_at`, `auto_reply_at` and `correlation_confidence`.
- New additive reliability tables are `delivery_attempts`, `recipient_suppressions`, `suppression_events` and `conversation_events`.
- Hard bounce/user-unknown may suppress a recipient. One soft bounce does not create permanent suppression; repeated soft bounces can suppress only after the configured threshold. `delivery_uncertain` is not automatically retried when duplicate delivery is possible.
- Observability keeps `observed`, `inferred` and `estimated` semantics distinct. Pixel opens remain telemetry rather than proof of human reading, provider inference remains query-time interpretation, and an absent DKIM selector is not represented as a DKIM failure.
- No MCP command names are added by v9.5.0; the tranche replaces/extends existing registrations and structured outputs/options additively.

### Security / deployment
- Dashboard POST forms added by v9.5.0 carry the existing CSRF token and continue through `_verified_form`; the security check is not weakened.
- The public tree remains provider-neutral and contains no deployment credentials or private recipient/domain configuration. Provider-specific spam headers are treated only as observed message metadata, not architecture or persisted provider truth.
- `dnspython>=2.6,<3` is added through `requirements.txt`; the existing requirements-hash persistent-venv rebuild installs it on source update.
- `postmaster-mcp.yml` is intentionally unchanged. No new port, volume, public callback path, Cloudflare rule or deployment procedure is required by this tranche.
- Known limits: DKIM DNS verification requires an explicitly supplied selector; pure TLS-handshake latency is not isolated from the standard library protocol setup; optional controlled seed-account inbox-placement testing is not implemented.

## 9.4.6 - 2026-08-21

### Added
- Persistent File Store attachments through the existing `attachments` structure using `{"file_id":"..."}` with optional MIME-only `filename` and `media_type` overrides. File bytes are resolved exclusively server-side from the canonical persistent blob and never require Base64, MCP resource handoff, or signed download URLs.
- Tracked Persistent File Store downloads through the existing `/t/c/<opaque-token>` public tracking infrastructure. Internal `postmaster-file:<file_id>` links in existing `body_html` are resolved before delivery and the recipient receives only a cryptographically random per-delivery tracking token.
- Lazy on-demand stable-release checks shared by `build_status` and the WebGUI footer with a 60-second process cache. `build_status` now adds `latest_version`, `update_available`, `update_checked_at`, `update_last_attempt_at`, `update_check_status` and the cache TTL without changing its name or inputs.
- WebGUI footer update status: `Postmaster vX.Y.Z · Up to date`, `Update available: vX.Y.Z`, or a non-false-negative unavailable/last-known state after remote failures.
- Regression coverage for all three attachment sources, metadata defaults/overrides, missing/unauthorized/corrupt stored files, size limits, mixed attachments, reply/follow-up drafts and sends, opaque tracked downloads, revocation/expiry/unavailable targets, tracking/provider classification, and the shared 60-second version cache.

### Changed
- The common v9.4.6 runtime mail client resolves Persistent File Store attachment records in the order record → owner/project authorization → canonical blob → validated filename/MIME → MIME attachment, so `create_draft`, `create_reply_draft`, `create_follow_up_draft`, `send_email`, `reply_email` and `follow_up_email` inherit the new source without parallel per-command implementations.
- `tracking_links` receives an automatic, idempotent additive migration for `target_type`, internal stored-file reference, download filename/media type, status, expiry and revocation timestamps. Existing URL rows default to `target_type='url'`; click rows, the historical `event_type='link'`, the unique-click key and query-time provider/scanner classification remain unchanged.
- Stored-file public download targets reuse the existing campaign/delivery/link association and `record_click` pipeline. Unknown, revoked, expired and unavailable tokens intentionally return the same non-descriptive public 404 response.
- The latest-version checker preserves the last known good remote version when a refresh fails. With no previous successful value, `update_available` is unknown (`null`) rather than falsely reporting `false`. Explicitly pinned deployments still report a newer stable release when one exists but never auto-upgrade at runtime.

### Security / deployment
- Public stored-file URLs never contain `file_id` in the path, query string or reversible encoding. There is no public `?file_id=` lookup and no new public File Store endpoint. Cloudflare Access continues to protect `/mcp`, dashboard/admin/private APIs and tracking analytics.
- File Store attachment source authorization is evaluated against the canonical persistent record before reading bytes; metadata overrides affect MIME presentation only and cannot select another file or bypass owner/project scope.
- Redirect targets for tracked stored-file downloads are resolved only from canonical server records and opaque tokens. The public endpoint never accepts an arbitrary target URL or file identifier from a query parameter.
- The existing `/t/c/*` Cloudflare exposure, public base URL, opaque token generator, event/fingerprint pipeline and provider classification are reused. No new port, domain, Cloudflare rule, mandatory environment variable or signed File Store URL is introduced.
- The pre-existing `/files/*` signed handoff remains a separate client-download feature and is not used by File Store → email attachment or tracked stored-file delivery.
- The Persistent File Store and analytics schema remain under the existing `/data` volumes. The migration is backward-compatible and idempotent for existing databases.
- `postmaster-mcp.yml` is unchanged. No new dependency is added; the existing hash-based persistent virtualenv/bootstrap behavior is unchanged.

### MCP compatibility
- **No MCP commands added, removed or renamed.**
- Existing email command names and signatures remain unchanged. The only backward-compatible structured input extension is that `attachments[]` may now use `{"file_id":"..."}` with optional `filename` / `media_type` MIME-only overrides; existing `content_base64` and `source_mailbox` + `source_uid` sources continue unchanged.
- The internal `postmaster-file:<file_id>` href is interpreted inside the already-existing `body_html` string and does not add a command or a new input parameter.
- `build_status` keeps the same name and zero-input signature; only informational output fields are added.

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
