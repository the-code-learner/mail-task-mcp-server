# Postmaster MCP

**Self-hosted MCP control plane for email, persistent tasks, project context and AI-assisted workflows.**

Postmaster MCP connects MCP-capable AI clients to one or more IMAP/SMTP mailboxes while keeping credentials, recipient policy, task state, analytics, memories, skills and project context on your own server.

Version 9 moves the project from the old all-in-one Compose source layout to a normal, maintainable multi-file application **without giving up one-file deployment**: Portainer still needs only `postmaster-mcp.yml`. From v9.2 the same YAML can follow stable releases automatically or stay pinned to an exact version.

> Original project by **the-code-learner**.  
> Licensed under the **Apache License 2.0**. See `LICENSE` and `NOTICE`.

---

## v9 at a glance

```text
MCP clients / browser
        |
        v
Cloudflare Access / trusted LAN
        |
        v
Postmaster MCP
        |
        +-- IMAP / SMTP mail operations
        +-- encrypted multi-account storage
        +-- recipient safety policy
        +-- drafts, replies and attachments
        +-- open + per-link analytics / AMP support
        +-- persistent task registry
        +-- memories / skills / project context
        +-- lexical + semantic retrieval
        +-- Web administration dashboard
```

The important v9 changes are:

- normal source tree under `src/postmaster/` instead of Python embedded inside Compose `configs:`;
- **single-YAML Portainer bootstrap** that fetches a selected GitHub ref automatically;
- persistent code, virtual-environment and application-data volumes;
- persistent memories, skills and project context with revisions and audit history;
- SQLite FTS5 plus compact multilingual semantic retrieval;
- verified ~9.9 MB compressed context-model release;
- improved MIME parsing for forwarded mail and HTML-heavy messages;
- CI coverage for the bootstrap, MIME parser, knowledge store and semantic-model provisioning;
- persistent small-file storage plus native ChatGPT file inputs in v9.2;
- native Postmaster-to-client file handoff in v9.3;
- per-link click analytics plus clean Sent copies in v9.4;
- semantic release history through `VERSION`, `CHANGELOG.md` and immutable `vX.Y.Z` release tags.

---

# Quick start with Portainer

## Requirements

- Docker / Portainer
- Internet access on first start
- an IMAP/SMTP account if you want to use mail features

The service can start without a mailbox configured.

## 1. Create the stack

In Portainer:

```text
Stacks
-> Add stack
-> Web editor
```

Paste the complete contents of:

```text
postmaster-mcp.yml
```

Then deploy the stack.

The default mapping is:

```text
host :8787 -> container :8000
```

## 2. Choose the update policy

The v9.2+ bootstrap uses one persistent YAML and a version policy:

```yaml
POSTMASTER_REPO: the-code-learner/mail-task-mcp-server
POSTMASTER_VERSION: latest
POSTMASTER_CHECK_UPDATES_ON_START: "true"
POSTMASTER_FORCE_REFRESH: "false"
```

`latest` follows the newest stable `vX.Y.Z` GitHub Release. With `POSTMASTER_CHECK_UPDATES_ON_START=true`, Postmaster resolves the newest stable application release at every container start and only downloads it when that release is not already cached. Set the switch to `false` to keep using the currently cached source without a remote update check; if no usable cache exists, Postmaster resolves `latest` once so first boot can succeed.

Explicit `vX.Y.Z`, `X.Y.Z` or immutable commit selections remain pinned. Existing deployments that still provide only `POSTMASTER_REF` remain supported as a compatibility fallback. Failed refreshes preserve the previously working cached release.

## 3. Open the dashboard

On a trusted network:

```text
http://YOUR_SERVER_IP:8787/
```

The dashboard can configure mail accounts, recipient authorization, tasks, files, tracking and knowledge/context data.

## 4. Configure mail

The public YAML intentionally contains no credentials. Account configuration includes IMAP/SMTP identity, hosts, ports/security, credentials and mailbox names. Passwords are encrypted before being written to persistent storage.

Important files include:

```text
/data/mail-accounts.db
/data/mail-accounts.key
```

Back up the key together with the database.

---

# Structural deployment model

v9 separates **deployment** from **application source**. Portainer receives one small Compose YAML, `postmaster-mcp.yml`, containing the service definition, environment, persistent volumes, bootstrap command and health check.

At startup:

```text
GitHub repository + version policy
        |
        v
safe staged archive download
        |
        v
persistent versioned source cache
        |
        +--> persistent Python venv
        |
        +--> /data application state
        |
        v
Postmaster MCP runtime
```

The downloaded archive is checked before extraction. Absolute paths, `..` traversal and archive links are rejected. A failed refresh does not replace a previously cached working source tree. Persistent code, virtual environment and data are kept in Docker volumes.

---

# Persistent memory, skills and project context

v9 adds a persistent knowledge layer shared across conversations and MCP clients. Knowledge items can be `memory` or `skill`, scoped by owner/project, tagged, prioritized, enabled/disabled and revisioned. The store supports audit history, restore-to-new-revision behavior, import/export and chunked indexing.

Persistent storage is kept in `/data/knowledge.db`.

# Hybrid retrieval

Context retrieval combines SQLite FTS5 lexical search, compact multilingual embeddings, priority and scope through rank fusion. If semantic retrieval is unavailable, lexical FTS remains usable and the service continues to start.

# Compact multilingual context model

The semantic runtime uses a compact derivative of `sentence-transformers/static-similarity-mrl-multilingual-v1`: 128 dimensions, int8 static embeddings, multilingual Model2Vec runtime and Apache-2.0 source license. The verified compressed release is approximately 9.9 MB and contains no user-specific training data.

See `docs/context-model.md`.

---

# Email and MIME handling

Postmaster supports plain text, HTML, attachments, drafts, replies and forwarded messages. v9 extracts plain/HTML alternatives independently, exposes `body_html`, preserves URLs when deriving readable text from HTML, traverses nested `message/rfc822` forwarded messages and avoids treating ordinary text attachments as the body.

# Multi-account mail

Multiple IMAP/SMTP identities can be stored server-side. Mailbox tools accept an optional `account_id`; when omitted, the configured default account is used. Credentials are never returned through MCP tools.

# Recipient safety

Sending is protected by exact-address/domain authorization plus optional previously-sent recipient history. Draft creation remains more permissive because a draft is not an external delivery and can be reviewed before sending.

# Persistent task registry

The scheduler stores task definitions, recurrence and execution context while an MCP-capable AI client performs reasoning and explicit actions. This supports conditional follow-up, inbox/Junk review and other persistent workflows.

---

# Tracking and AMP

Open tracking is configurable per account and overridable per send/reply:

```text
track_opens: null   -> account default
track_opens: true   -> enable tracking for this message
track_opens: false  -> disable tracking for this message
```

Tracked multi-recipient delivery uses a distinct delivery token per recipient while preserving visible `To`/`Cc`; `Bcc` stays hidden. Replies preserve normal threading headers. Open/click events are telemetry, not proof of human reading or intent; proxies, scanners, prefetching and image blocking can affect observations.

AMP for Email remains optional and uses separately scoped, time-limited delivery tokens.

## Per-link click tracking and clean Sent copies (v9.4)

v9.4 rewrites eligible HTTP/HTTPS anchors in the **recipient** HTML to opaque URLs:

```text
https://<PUBLIC_EMAIL_HOST>/t/c/<token>
```

The token is random and resolves server-side to the delivery, logical link occurrence and exact original destination. `/t/c/<token>` records a `link` event using the existing country/source/browser/OS/User-Agent/fingerprint enrichment pipeline and immediately redirects to the server-stored `original_url`. Query parameters are never accepted as redirect destinations.

`mailto:`, `tel:`, `cid:`, `data:`, `javascript:` and local `#fragment` links are not rewritten. Query strings/fragments are preserved. Repeated occurrences retain separate anchor positions and logical link IDs while normalized URL data allows aggregation.

Unique click is defined as:

```text
delivery_id + link_id + client_fingerprint
```

v9.4 does not aggressively classify human/bot/scanner clicks. It keeps the raw telemetry fields needed for a later evidence-based classifier.

The archived **Sent** copy is generated separately from the same canonical message. It contains the original URLs, no active recipient tracking pixel, no `/t/c/<token>` and no recipient AMP callback alternative. `Message-ID`, `Date`, subject/threading headers and attachment bytes are preserved. This prevents sender self-opens/self-clicks from being attributed to the recipient.

New analytics include total/unique clicks, unique recipients, first/last click, top links, destination host and campaign/delivery/link filtering. Existing `tracking_status` and `get_tracking_campaign` are extended; v9.4 also adds `get_tracking_summary`, `list_tracking_links` and `list_tracking_events`. The dashboard keeps the existing pixel view and adds Top links plus unified pixel/AMP/link events.

See `docs/LINK_TRACKING.md`.

---

# Security model

Postmaster uses a split security perimeter: protect the whole application by default, then carve out only callback paths that cannot authenticate through the normal control plane.

Existing public callback paths:

```text
/api/amp/*
/track/open/*
```

v9.4 adds exactly one new required public callback path:

```text
/t/c/*
```

**Cloudflare Access must bypass `/t/c/*` for link redirects to work.** Keep `/mcp`, dashboard/admin/private APIs, mail/task/memory/skill/file-management endpoints and tracking analytics protected. Do not disable Cloudflare Access globally.

The raw Docker port should not be exposed directly to the public Internet; prefer Cloudflare Tunnel or another trusted reverse proxy and restrict origin access accordingly.

The v9.3 `/files/{file_id}` signed HTTP handoff is a separate pre-existing deployment concern and is not automatically added to the v9.4 bypass list.

---

# Persistent files and backup

Typical persistent files include:

```text
/data/scheduler.db
/data/recipient-policy.db
/data/mail-accounts.db
/data/mail-accounts.key
/data/email-analytics.db
/data/email-analytics.key
/data/knowledge.db
/data/models/
/data/files/
```

Back up `mcp_data`, especially database/key pairs.

---

# CI and regression coverage

The v9 runtime workflow validates source compilation, unit/regression tests, version/changelog consistency, provider-neutral public files, MIME handling, knowledge store, compact semantic model provisioning, full runtime import and single-YAML bootstrap behavior. v9.4 adds link rewrite/redirect/analytics tests and recipient-versus-Sent MIME regression coverage.

The recommended policy is to protect `main`, require pull requests and require the v9 runtime status check before merging.

---

# Native ChatGPT file upload (v9.2)

The portable `save_file(content_base64=...)` tool remains available. ChatGPT clients can instead use `save_uploaded_file` or `save_uploaded_files` with `_meta["openai/fileParams"]`; temporary authorized downloads are streamed server-side through the same bounded FileStore path. Uploaded content is never executed or automatically added to Knowledge.

# Native Postmaster file handoff (v9.3)

`get_stored_file_resource(file_id, transport="auto")` returns a real MCP `ResourceLink` using the canonical FileStore `file_id`. The hierarchy is native ResourceLink/file reference, signed HTTPS streaming, MCP `resources/read`, Base64 fallback, inline Base64 only as a last resort.

`postmaster://files/{file_id}` is registered as a resource template. `GET`/`HEAD /files/{file_id}` provide temporary HMAC-signed HTTPS capabilities with byte-range support and stream the original content-addressed blob without resizing, recompressing or transcoding it.

The existing `PUBLIC_MCP_HOST` is reused as the default HTTPS base. Advanced deployments may optionally set `FILE_STORE_PUBLIC_BASE_URL`, `FILE_STORE_DOWNLOAD_SECRET` and `FILE_STORE_DOWNLOAD_URL_TTL_SECONDS`; otherwise a persistent signing secret is generated under `/data` and TTL defaults to 900 seconds.

See `docs/FILE_HANDOFF.md`.

---

# Versioning and updates

Stable releases use Semantic Versioning. `VERSION` contains the application version; GitHub release tags use `vX.Y.Z`.

```text
POSTMASTER_VERSION=latest   -> newest stable GitHub Release on restart
POSTMASTER_VERSION=v9.4.0  -> exact immutable release
POSTMASTER_VERSION=<SHA>    -> exact immutable commit
```

With `POSTMASTER_VERSION=latest` and `POSTMASTER_CHECK_UPDATES_ON_START=true`, no YAML edit is needed for v9.4: after the stable release is published, restart the stack. `POSTMASTER_FORCE_REFRESH=true` remains an explicit redownload control, separate from update selection.

Cloudflare Access is external to the container. The `/t/c/*` bypass must be configured manually before v9.4 link tracking is fully operational.

---

# Privacy and public distribution

The public repository intentionally contains no mailbox credentials, private recipient allowlists, personal domains, private project context or conversation data. Deployment-specific secrets belong in the private Portainer stack or secret-management layer.

# License

Apache License 2.0. See `LICENSE` and `NOTICE`.
