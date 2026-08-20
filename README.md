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
        +-- drafts, replies, follow-ups and attachments
        +-- open analytics / AMP support
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

The v9.2 bootstrap uses one persistent YAML and a version policy:

```yaml
POSTMASTER_REPO: the-code-learner/mail-task-mcp-server
POSTMASTER_VERSION: latest
POSTMASTER_CHECK_UPDATES_ON_START: "true"
POSTMASTER_FORCE_REFRESH: "false"
```

`latest` follows the newest stable `vX.Y.Z` GitHub Release. With `POSTMASTER_CHECK_UPDATES_ON_START=true` (the default), Postmaster resolves the newest stable application release at every container start and only downloads it when that release is not already cached. Set `POSTMASTER_CHECK_UPDATES_ON_START=false` to keep using the currently cached source without contacting GitHub for an update check; if no usable cached source exists yet, Postmaster resolves `latest` once so the first boot can succeed.

To freeze a deployment independently of the update-check switch, use an exact release such as `v9.2.1` (or `9.2.1`), or an immutable commit SHA. Explicit versions never require a latest-release lookup. Existing deployments that still provide only `POSTMASTER_REF` remain supported as a compatibility fallback.

If GitHub is temporarily unavailable during an enabled update check, a previously working cached release is kept and started instead of replacing it with an incomplete update. `POSTMASTER_FORCE_REFRESH=true` is separate: it deliberately redownloads the already selected revision and may therefore use the network even when update checking is disabled.

## 3. Open the dashboard

On a trusted network:

```text
http://YOUR_SERVER_IP:8787/
```

The dashboard can be used to configure mail accounts, recipient authorization, tasks and knowledge/context data.

## 4. Configure mail

The public YAML intentionally contains no credentials.

You can configure an account from the dashboard with:

```text
Account ID / label
From address
IMAP host / port / security
IMAP username / password
SMTP host / port / security
SMTP username / password
Inbox / Sent / Draft / Junk mailboxes
```

Passwords are encrypted before being written to the persistent account database.

Important files include:

```text
/data/mail-accounts.db
/data/mail-accounts.key
```

Back up the key together with the database. Existing encrypted credentials cannot be recovered without the matching key.

---

# Structural deployment model

v9 deliberately separates **deployment** from **application source**.

Portainer receives one small Compose YAML:

```text
postmaster-mcp.yml
```

That YAML contains only:

```text
service definition
environment
persistent volumes
bootstrap command
health check
```

At startup the bootstrap:

```text
GitHub repository + ref
        |
        v
safe staged archive download
        |
        v
persistent source cache
        |
        +--> persistent Python venv
        |
        +--> /data application state
        |
        v
Postmaster MCP runtime
```

The downloaded archive is checked before extraction. Absolute paths, `..` traversal and archive links are rejected. A failed refresh does not replace a previously cached working source tree.

The repository itself remains a normal project:

```text
src/postmaster/
    server.py
    mail_bridge.py
    mail_extensions.py
    account_store.py
    scheduler_engine.py
    email_analytics.py
    knowledge_store.py
    context_engine.py
    semantic_engine.py
    file_store.py
    remote_file.py

scripts/
    start.sh
    prepare_context_model.py

tests/
docs/
requirements.txt
VERSION
CHANGELOG.md
postmaster-mcp.yml
```

Docker volumes:

```text
mcp_code   -> downloaded source releases
mcp_venv   -> Python virtual environment
mcp_data   -> databases, keys, model and persistent state
```

A source update therefore does not require rebuilding a giant Compose file, while a deployment still needs only one YAML.

---

# Persistent memory, skills and project context

v9 adds a persistent knowledge layer shared across conversations and MCP clients.

Knowledge items can be stored as:

```text
memory
skill
```

and scoped by:

```text
owner
project
owner-global context
```

The store supports:

- tags;
- priority;
- `always_include` context;
- enabled/disabled state;
- metadata;
- immutable revision history;
- restore-to-new-revision behavior;
- audit events;
- import/export;
- chunked indexing.

Project-scoped context can combine exact project knowledge with owner-global knowledge without mixing unrelated project data.

Persistent storage is kept in:

```text
/data/knowledge.db
```

---

# Hybrid retrieval

Context retrieval combines several signals rather than relying on one search method:

```text
SQLite FTS5 lexical search
          +
compact multilingual embeddings
          +
priority
          +
scope
          |
          v
rank fusion
          |
          v
project context
```

The default weighting is:

```text
semantic  0.60
lexical   0.25
priority  0.10
scope     0.05
```

If semantic retrieval is unavailable, lexical FTS remains usable and the service continues to start.

---

# Compact multilingual context model

The v9 semantic runtime uses a compact derivative of:

```text
sentence-transformers/static-similarity-mrl-multilingual-v1
```

Runtime profile:

```text
128 dimensions
int8 static embeddings
multilingual, including Italian and English
Model2Vec runtime
Apache-2.0 source license
```

The verified release asset is:

```text
context-model-v1
postmaster-context-mrl-128d-int8.tar.gz
```

Compressed size is approximately **9.9 MB**.

SHA-256:

```text
33aebe14cc1cc8e506bca5f2d08fe243f94d4a716875f172f96229bb33bff632
```

On first start the provisioning script:

1. downloads the compact release asset;
2. verifies SHA-256;
3. rejects unsafe archive paths/links;
4. loads the model through the real Model2Vec runtime;
5. validates a 128-dimensional inference probe;
6. installs it atomically under `/data/models`.

A pinned upstream-source rebuild is available as a fallback. No user email, project memory, conversation or other private data is used to construct the public model.

See `docs/context-model.md` for details.

---

# Email and MIME handling

Postmaster MCP supports plain text, HTML, attachments, drafts, replies, follow-ups and forwarded messages.

A normal multipart message may contain:

```text
text/plain
text/html
```

v9 fixes an important forwarded-mail failure mode where a tiny generated `text/plain` part could hide the real HTML message.

`get_email` now:

- extracts plain and HTML alternatives independently;
- exposes `body_html` in addition to the selected text body;
- compares useful content instead of blindly preferring `text/plain`;
- converts rich HTML to readable text when the HTML is the meaningful body;
- preserves URLs when deriving text from HTML;
- traverses nested `message/rfc822` forwarded messages;
- does not treat ordinary text attachments as the message body;
- reports body-source and forwarded-message metadata.

This behavior is covered by regression tests.

---

# Multi-account mail

Multiple IMAP/SMTP identities can be stored on the server.

Mailbox operations accept an optional:

```text
account_id
```

If omitted, the configured default account is used.

Credentials remain server-side and are not returned through MCP tools.

---

# Reply vs follow-up (v9.4.2)

Threaded mail actions deliberately separate inbound replies from outbound follow-ups:

```text
reply_email             -> reply to an inbound message
create_reply_draft      -> draft a reply to an inbound message
follow_up_email         -> follow up an outbound/Sent message
create_follow_up_draft  -> draft a follow-up to an outbound/Sent message
```

For inbound messages, `reply_email` prefers a valid `Reply-To` and otherwise uses `From`. Calling it on a message clearly sent by the selected account is rejected with guidance to use `follow_up_email`, preventing self-replies.

For outbound/Sent messages, `follow_up_email` reuses the original visible `To` and preserves the original visible `Cc` by default. The sender account and its configured email identities are removed case-insensitively, duplicates are removed while preserving order, and at least one external `To` recipient must remain. Original Bcc recipients are never rediscovered, inferred or exposed. Calling follow-up on an inbound message is rejected.

Both modes preserve normal threading: one normalized `Re:` prefix, `In-Reply-To` pointing to the selected message's `Message-ID`, and `References` preserved/extended. Follow-up sending uses the same recipient-authorization, tracking, individualized-delivery and clean-Sent pipeline as existing sends/replies; it does not introduce a parallel tracking implementation.

---

# Recipient safety

Sending is protected by an authorization policy.

The public stack ships without private recipients or domains:

```yaml
SEND_RECIPIENT_ALLOWLIST: ''
ALLOW_PREVIOUS_SENT_RECIPIENTS: 'true'
```

Recipients can be authorized by exact address or by domain. Previously sent recipients can optionally be accepted through history.

Draft creation intentionally remains more permissive because a draft is not an external delivery and can be reviewed before sending.

---

# Persistent task registry

The scheduler stores persistent task definitions, recurrence and execution context.

The normal model is:

```text
Task registry
    |
    | due task / recurrence / context
    v
MCP-capable AI client
    |
    | reasons about current state
    | performs explicit actions
    v
Postmaster MCP
```

This supports workflows such as:

```text
Follow up only if no reply has arrived.
Review Junk and restore genuine false positives.
Check unread mail and summarize messages requiring attention.
```

The server persists the task state; the AI client performs the reasoning and explicit action.

---

# Open tracking and AMP

Open tracking can be configured per account and overridden for individual sends/replies/follow-ups.

```text
track_opens: null   -> account default
track_opens: true   -> enable for this message
track_opens: false  -> disable for this message
```

Tracked multi-recipient delivery uses a distinct token per recipient while preserving visible `To` / `Cc` headers. `Bcc` remains hidden. Replies and follow-ups preserve normal threading headers.

Open events are telemetry, not proof that a human read a message. Mail scanners, proxies, prefetching and image blocking can affect observations.

AMP for Email is optional and uses separately scoped, time-limited delivery tokens.

---

# Security model

Postmaster MCP uses a split security perimeter:

```text
                        Internet
                           |
                           v
                   Cloudflare Access
                   /               \
                  /                 \
                 v                   v
      authenticated control     narrow callbacks
              plane                 only
          /          \           /       \
         /            \         /         \
        v              v       v           v
   Dashboard          /mcp  /api/amp/*  /track/open/*
         \              /       \           /
          \            /         \         /
                 Postmaster MCP
```

General rule:

> **Protect the whole application by default, then carve out only the machine-to-machine callback paths that cannot authenticate through the normal user/OAuth flow.**

The dashboard, MCP endpoint, mailbox operations, task management, analytics administration and write operations belong to the authenticated control plane.

If AMP or tracking is enabled, only the required callback paths should receive a narrowly scoped Access bypass:

```text
/api/amp/*
/track/open/*
```

Do not bypass authentication for `/`, `/mcp`, dashboard routes or general APIs.

The raw Docker port should not be exposed directly to the public Internet. Prefer Cloudflare Tunnel or another trusted reverse proxy and restrict origin access accordingly.

The public callback URLs use random capability tokens and do not expose mailbox credentials or MCP administration.

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
```

Back up `mcp_data`, especially database/key pairs. Losing an encryption key can make the corresponding encrypted data unrecoverable.

---

# Updating v9

During development you can point:

```yaml
POSTMASTER_REF: v9-structural-runtime
POSTMASTER_REFRESH_ON_START: 'true'
```

and restart the container to fetch the latest branch revision.

For a stable deployment, pin an immutable tag or commit:

```yaml
POSTMASTER_REF: v9.0.0
```

The bootstrap stages the new source before replacing the cached release. If refresh fails and an older cached copy exists, the previous copy is retained.

Dependencies are installed into a persistent virtual environment and rebuilt only when `requirements.txt` changes.

---

# CI and regression coverage

The v9 runtime workflow validates:

```text
Python source compilation
MIME / forwarded-email regression tests
knowledge-store CRUD / FTS / history / export
real compact-model download
SHA-256 model verification
128d Model2Vec inference
full MCP server import
Portainer YAML structure
bootstrap shell syntax
Compose variable escaping
```

The recommended repository policy is to protect `main`, require pull requests and require the v9 runtime status check before merging.

---

# Repository layout

```text
.
├── postmaster-mcp.yml
├── requirements.txt
├── src/
│   └── postmaster/
├── scripts/
├── tests/
├── docs/
├── .github/workflows/
├── LICENSE
├── NOTICE
└── README.md
```

---

# v8.7 migration note

v8.7 used a monolithic Portainer stack where the Python application was embedded directly in Compose `configs:` entries.

v9 keeps the same practical deployment goal — **paste one YAML into Portainer** — but the YAML is now only a bootstrap. Application code lives in the GitHub repository and is downloaded into a persistent source volume.

Persistent data remains under `/data`; migrating an existing installation should preserve the data volume and its encryption keys.

Before replacing a working v8.7 deployment, back up the persistent data volume and test v9 against your real mailboxes and reverse-proxy configuration.

---

# Privacy and public distribution

The public repository intentionally contains no mailbox credentials, private recipient allowlists, personal domains, private project context or conversation data.

The compact semantic model is derived only from a public Apache-2.0 source model and contains no user-specific training data.

Deployment-specific secrets belong in your private Portainer stack or secret-management layer, not in the public repository.

---

# License

Apache License 2.0. See:

```text
LICENSE
NOTICE
```


## v9.1 small-file store

v9.1 adds a private persistent store for small reference files. Metadata is kept in SQLite while file bytes are stored as SHA-256-addressed blobs under `/data/files`, so user-provided filenames never become filesystem paths. The default public stack limits individual files to 1 MiB, the logical store to 100 MiB and 1000 records; hard application caps prevent accidentally configuring unbounded values.

MCP clients can save UTF-8 text directly or binary data as base64, list scoped metadata, read text with a character budget, retrieve binary content as base64, update metadata and delete files. Owner/project scopes reuse the scheduler registry. The WebGUI has a Files tab for upload, download and deletion. Downloads are forced as attachments with `X-Content-Type-Options: nosniff`; Postmaster never executes stored content and does not expose public file URLs.

The file store is intentionally separate from Knowledge in v9.1. Uploading a document does not automatically inject it into semantic context; a later version can add explicit opt-in document extraction/indexing without making arbitrary uploads part of prompts by default.

---

# Versioning and updates

Stable Postmaster releases use Semantic Versioning and are recorded in `CHANGELOG.md`. The repository `VERSION` file contains the application version, while GitHub release tags use `vX.Y.Z`.

For a Portainer deployment:

```text
POSTMASTER_VERSION=latest   -> follow the latest stable GitHub Release on restart
POSTMASTER_VERSION=v9.2.0  -> stay pinned to that exact release
POSTMASTER_VERSION=<SHA>   -> stay pinned to an immutable commit
```

`build_status` reports the application `version`, the resolved running `build`, and the `requested_version` policy so an MCP client can distinguish `latest` from the concrete release actually running.

# Native ChatGPT file upload (v9.2)

The portable MCP `save_file(content_base64=...)` tool remains available. ChatGPT clients can instead use `save_uploaded_file` or `save_uploaded_files`; those tools declare `_meta["openai/fileParams"]`, so ChatGPT passes temporary authorized file download objects rather than forcing large Base64 strings through model context.

Remote downloads are HTTPS-only, bounded by the same per-file store limit while streaming, limited in redirects and timeout, checked against non-public address resolution, and then stored through the same SHA-256 content-addressed `FileStore`. Uploaded content is never executed or automatically added to semantic Knowledge.

# Native Postmaster file handoff (v9.3)

v9.3 completes the reverse path from Postmaster to MCP clients. `get_stored_file_resource(file_id, transport="auto")` returns a real MCP `ResourceLink` content block using the canonical FileStore `file_id`; constructing the link reads metadata only and does not serialize the link into text or load the stored blob.

The preferred hierarchy is native ResourceLink/file reference, signed HTTPS streaming, MCP `resources/read`, Base64 fallback, and inline Base64 only as a last resort. `postmaster://files/{file_id}` is registered as a resource template, and the SDK turns returned bytes into protocol `BlobResourceContents` when a client follows the MCP resource.

`GET` and `HEAD /files/{file_id}` provide temporary HMAC-signed HTTPS capabilities with byte-range support. The HTTP path streams the original content-addressed blob directly: it does not resize, recompress, transcode, Base64-encode, or create a second transfer copy.

The existing `PUBLIC_MCP_HOST` is reused as the normal HTTPS base for the same service, so the public `postmaster-mcp.yml` does not need new required variables. Advanced deployments may optionally override the file base or signing behavior with `FILE_STORE_PUBLIC_BASE_URL`, `FILE_STORE_DOWNLOAD_SECRET`, and `FILE_STORE_DOWNLOAD_URL_TTL_SECONDS`; otherwise the signing secret is generated once and persisted at `/data/file-store-download.secret` and the TTL defaults to 900 seconds.

An existing stack with `POSTMASTER_VERSION=latest` can therefore receive v9.3 by restarting after the stable release is published. If the external access layer protects the full app, ensure the signed `/files/*` route is reachable according to the deployment's proxy policy without weakening protection for `/mcp`, the dashboard, or unrelated routes.

See `docs/FILE_HANDOFF.md` for the handoff hierarchy, security model, signed URL behavior and deployment details.

# Per-link tracking and clean Sent copies (v9.4)

v9.4 adds per-link HTTP/HTTPS click telemetry to the existing tracked-delivery pipeline while leaving the existing `/track/open/*` pixel behavior unchanged. Eligible recipient HTML anchors are rewritten to opaque URLs under:

```text
/t/c/<token>
```

The random token identifies a server-side link occurrence; recipient data and destination URLs are not encoded into it. The click endpoint resolves the stored record, records a `link` event with the existing country/source/browser/OS/User-Agent/client-fingerprint enrichment and immediately redirects to the exact stored `original_url`. Query parameters supplied to `/t/c/<token>` are never accepted as redirect destinations.

The v9.4 unique-click definition is:

```text
delivery_id + link_id + client_fingerprint
```

Analytics expose total clicks, unique clicks, unique recipients, first/last click, destination host, per-campaign/per-delivery/per-link filtering and top links. Existing `tracking_status` and `get_tracking_campaign` are extended, while `get_tracking_summary`, `list_tracking_links` and `list_tracking_events` provide read-only link/event queries. Opaque tracking tokens are not returned by list/dashboard APIs.

Recipient and Sent MIME are now generated separately from the same canonical body and attachment inputs. The recipient copy keeps the existing tracking pixel and tracked URLs; the archived Sent copy keeps original URLs and contains no active recipient pixel or `/t/c/<token>`. `Message-ID`, Date and normal threading headers are preserved and attachments reuse the same original bytes. This prevents sender self-opens/self-clicks from being attributed to the recipient for messages generated by v9.4+.

The single-YAML bootstrap is unchanged. With `POSTMASTER_VERSION=latest` and `POSTMASTER_CHECK_UPDATES_ON_START=true`, restart the stack after the stable release is published.

Cloudflare Access is external to the container. Keep the existing public bypasses for `/track/open/*` and `/api/amp/*`, and add exactly one new public bypass required by v9.4:

```text
/t/c/*
```

Do not expose `/mcp`, dashboard/admin/private APIs, mail/task/memory/skill/file-management endpoints or tracking analytics. The pre-existing v9.3 signed `/files/*` handoff remains a separate deployment-policy concern and is not added automatically as part of v9.4.

See `docs/LINK_TRACKING.md` for architecture, schema, Sent-clean behavior, analytics and the live Cloudflare preflight.

# Explicit reply/follow-up semantics (v9.4.2)

v9.4.2 prevents outbound messages from accidentally being replied back to the sender account. `reply_email` / `create_reply_draft` are inbound-only semantics, while `follow_up_email` / `create_follow_up_draft` operate on outbound/Sent messages and reuse the original visible recipients after sender-identity filtering. Source Bcc is never recovered.

Tracked follow-ups reuse the v9.4 dual-MIME pipeline: recipient copies may contain the configured open/link instrumentation, while archived Sent copies keep original URLs and omit active recipient pixel, click-tracking URLs and recipient AMP callbacks. Visible `To` / `Cc`, threading headers and attachment bytes remain consistent. No new environment variables, ports, volumes, callback paths or Portainer YAML changes are required.
