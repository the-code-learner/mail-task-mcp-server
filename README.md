# Postmaster MCP

**Self-hosted MCP server for AI-assisted email management and persistent task workflows.**

Postmaster MCP is a self-contained Model Context Protocol server that connects MCP-capable AI clients to one or more IMAP/SMTP mailboxes while keeping credentials, task state, recipient policies, analytics and operational data on your own server.

The v9 structural runtime keeps the application as normal source files in this repository while preserving a **single-YAML Portainer deployment**. `postmaster-mcp.yml` bootstraps a pinned GitHub ref into persistent code/venv volumes; v8.7 remains the previous monolithic-stack design.

> Original project by **the-code-learner**.  
> Licensed under the **Apache License 2.0**. See `LICENSE` and `NOTICE`.

---

## What it does

Postmaster MCP combines four main components:

```text
MCP clients
    |
    v
Postmaster MCP
    |
    +-- Multi-account IMAP/SMTP mail operations
    +-- Persistent task registry
    +-- Web administration dashboard
    +-- AMP / per-recipient open analytics
```

The email account credentials remain on the server and are not returned through MCP tools.

The scheduler is intentionally **registry-only**: it stores tasks and schedule metadata but does not run jobs or send messages by itself. An AI client can inspect due tasks, reason about what should happen, perform the appropriate MCP actions and then mark the task as handled.

In v8.7, the same account-level open-tracking policy is also applied to replies. A client can still opt out or opt in for an individual send/reply using `track_opens`.

---

## v8.7 capabilities

The included stack exposes **54 MCP tools**.

v8.7 adds open tracking to threaded replies using the same per-recipient analytics engine already used by tracked sends. The per-account `tracking_default` now applies consistently to both `send_email` and `reply_email`, while either operation can explicitly override it for a single message.

### System and account management

```text
build_status
list_email_accounts
test_email_account
amp_account_status
set_amp_account_state
validate_amp_email
```

### Mailbox and message access

```text
mailbox_status
list_mailboxes
search_emails
get_email
list_known_contacts
list_email_attachments
get_email_attachment
read_email_attachment
```

### Recipient safety policy

```text
email_security_status
recipient_authorization_status
list_authorized_recipients
list_authorized_domains
authorize_domain
revoke_domain
authorize_recipient
revoke_recipient
```

### Tracking and analytics

```text
tracking_status
list_tracking_campaigns
get_tracking_campaign
list_tracking_deliveries
list_open_events
```

### Email write operations

```text
send_email
reply_email
create_draft
create_reply_draft
move_email
mark_not_spam
mark_as_spam
set_email_seen
```

### Persistent task registry

```text
scheduler_status
create_owner
list_owners
create_project
list_projects
create_execution_profile
list_execution_profiles
preview_schedule
create_job
list_jobs
list_due_jobs
get_job
update_job
pause_job
resume_job
approve_job
complete_job
delete_job
get_job_history
```

`approve_job` is retained for compatibility, but the v8.7 task registry does not autonomously execute tasks.

---

# Quick start with Portainer

## Requirements

- Docker / Portainer
- Internet access during the first container start so Python dependencies can be installed
- an IMAP/SMTP email account if you want to use the mail features

The stack can start **without any mailbox configured**.

## 1. Create a stack

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

and deploy the stack.

The stack publishes:

```text
host port 8787 -> container port 8000
```

## 2. Open the dashboard

On a trusted local network, open:

```text
http://YOUR_SERVER_IP:8787/
```

The container starts even when no mail account exists. The dashboard will show that no account is configured and allows you to add one from the **Accounts** tab.

## 3. Add an email account

From the dashboard, provide:

```text
Account ID
Label
Email / From address

IMAP host
IMAP port
IMAP security
IMAP username
IMAP password

SMTP host
SMTP port
SMTP security
SMTP username
SMTP password

Inbox mailbox
Sent mailbox
Draft mailbox
Junk / Spam mailbox
```

Passwords are encrypted before being stored in the persistent account database.

The relevant persistent files are:

```text
/data/mail_accounts.db
/data/mail_accounts.key
```

Do not lose `mail_accounts.key`: existing encrypted mailbox credentials cannot be decrypted without the matching key.

---

# Portainer stack design

The v9 public distribution still requires only **one YAML file** in Portainer, but application source is no longer embedded in Compose `configs:` entries.

`postmaster-mcp.yml` contains only the service definition, environment, persistent volumes, bootstrap command and health check. On startup it downloads the configured `NOMADCOMPASS_REPO@NOMADCOMPASS_REF`, validates archive paths, installs it into a persistent code cache and starts the selected release.

The repository itself contains the maintainable source tree:

```text
src/nomadcompass/
scripts/
tests/
docs/
requirements.txt
```

The Python virtual environment is cached in `mcp_venv`, source releases are cached in `mcp_code`, and state remains in the persistent `mcp_data` volume mounted at `/data`.

For production, pin `NOMADCOMPASS_REF` to an immutable release tag or commit. A mutable branch is useful while testing upgrades.

---

# Persistent data

The stack uses separate SQLite databases and key files.

```text
/data/scheduler.db
/data/mail_policy.db
/data/mail_accounts.db
/data/mail_accounts.key
/data/email_analytics.db
/data/email_analytics.key
```

The Docker volumes are:

```text
mcp_code
mcp_venv
mcp_data
```

Back up `mcp_data` if you want to preserve accounts, task state, recipient policy and analytics across migrations or host failures.

---

# Task registry model

Postmaster MCP does **not** run scheduled tasks autonomously.

```text
Task registry
    |
    | stores due date / recurrence / context
    v
MCP-capable AI client
    |
    | reads due task
    | evaluates current context
    | performs explicit MCP actions
    v
Postmaster MCP
    |
    +-- email
    +-- mailbox operations
    +-- task completion/history
```

This makes conditional tasks possible, for example:

```text
Follow up only if no reply has arrived.

Review Junk and restore only genuine false positives.

Check unread mail and summarize messages that require attention.
```

The server remains the persistent source of task state, while the AI client provides the reasoning.

The public stack seeds a generic owner and project only:

```text
owner: default
project: default
```

They contain no personal information and can be replaced or supplemented through MCP.

---

# Recipient safety

Automated sending uses a recipient authorization policy.

The public stack ships with:

```text
SEND_RECIPIENT_ALLOWLIST: ''
ALLOW_PREVIOUS_SENT_RECIPIENTS: 'true'
```

No private recipient or company allowlist is included.

Recipients can be authorized explicitly by exact address or domain using the MCP tools or dashboard policy controls. Historical Sent recipients may also be accepted when that behavior is enabled.

Draft creation intentionally permits new/unlisted recipients so a human can review a draft before an actual send.

---

# Multi-account email

Multiple IMAP/SMTP accounts can be stored in the encrypted account database.

Each mailbox operation accepts an optional:

```text
account_id
```

If omitted, the configured default account is used.

This allows one MCP server to manage multiple mail identities while keeping the credentials server-side.

---

# HTML email

Postmaster MCP supports plain-text and HTML email for both sending and reading.

A normal multipart message can contain:

```text
text/plain
text/html
```

In v9, `get_email` exposes the selected text body and `body_html`. MIME parsing evaluates both alternatives instead of blindly preferring `text/plain`, so a tiny forwarding boilerplate cannot hide a substantially richer HTML body. Forwarded `message/rfc822` parts are traversed explicitly, and URLs in HTML-derived text are preserved.

Attachments can also be added to sends and drafts.

---

# AMP for Email

AMP is an **optional per-account capability**.

When an AMP body is supplied, the sender produces the MIME alternatives in this order:

```text
text/plain
text/x-amp-html
text/html
```

The server includes:

- per-account AMP enable/test/registration state
- local AMP preflight checks
- dashboard guidance for Gmail AMP registration
- recipient-scoped dynamic AMP status URLs

Normal plain/HTML email continues to work when AMP is disabled.

For dynamic AMP endpoints, configure a real public base URL before use.

---

# Per-recipient open analytics

Open tracking is disabled by default for newly configured accounts and can be enabled as an account default or overridden per message.

The account setting:

```text
Track opens by default (send + reply)
```

applies to both:

```text
send_email
reply_email
```

The MCP parameters use the following behavior:

```text
track_opens: null   -> use the account default
track_opens: true   -> force tracking for this message
track_opens: false  -> disable tracking for this message
```

For tracked multi-recipient sends and replies, v8.7 uses one SMTP envelope per recipient with a distinct tracking token while preserving the original visible `To` / `Cc` headers on every copy. `Bcc` recipients remain hidden.

Tracked replies also preserve the threading headers:

```text
In-Reply-To
References
```

so enabling analytics does not intentionally break normal mail threading or Reply-All context.

Before a tracked or AMP delivery creates campaign rows, the server validates that a public base URL is configured. This prevents analytics records from being created when tracking cannot produce a usable public pixel URL.

The tracker records image-load events such as:

```text
recipient
timestamp
country code reported by the edge proxy
parsed browser / OS
user agent
source / confidence
HMAC client fingerprint
```

It does **not** intentionally store the raw client IP address.

An open event is only telemetry from an external image request. It is not proof that a human actually read a message: image proxies, scanners, prefetching and image blocking can affect the result.

---

# Public URL configuration

The anonymous stack intentionally ships with:

```yaml
PUBLIC_MCP_HOST: ''
PUBLIC_EMAIL_BASE_URL: ''
```

This is enough for the container and local WebGUI to start.

Before using the MCP remotely through a hostname, set:

```yaml
PUBLIC_MCP_HOST: mcp.example.com
```

For AMP dynamic endpoints or open tracking, set either that host or a complete base URL:

```yaml
PUBLIC_EMAIL_BASE_URL: https://mcp.example.com
```

These values are deployment-specific and therefore are not hard-coded in the public repository.

---

# Security model

Postmaster MCP is designed around a **split security perimeter**:

```text
                         Internet
                            |
                            v
                    Cloudflare Tunnel
                            |
                            v
                  Cloudflare Access
                  /              \
                 /                \
                v                  v
      authenticated control     narrowly scoped
             plane             public callbacks
                |                  |
       +--------+--------+      +--+----------------+
       |                 |      |                   |
       v                 v      v                   v
   Dashboard           /mcp  /api/amp/*       /track/open/*
       |                 |      |                   |
       +--------+--------+      +---------+---------+
                |                         |
                v                         v
                     Postmaster MCP
                         :8000
```

The general rule is:

> **Protect the whole application by default, then explicitly carve out only the machine-to-machine callback paths that cannot authenticate through the normal user or OAuth flow.**

The Docker service itself is not intended to be exposed directly to the public Internet.

## Protected control plane

The following surfaces belong to the authenticated **control plane**:

```text
/
dashboard routes
/mcp
account administration
recipient/domain authorization
task management
tracking analytics
mailbox operations
write actions
```

They should sit behind a Cloudflare Access application with an **Allow** policy restricted to the identities that are authorized to administer or use the server.

The public Docker port should normally be reachable only from the trusted network or through the tunnel origin path.

A typical deployment is:

```text
MCP client / browser
        |
        v
Cloudflare Access
        |
   authenticated
        |
        v
Cloudflare Tunnel
        |
        v
Postmaster MCP
```

The application deliberately delegates the external authentication boundary to Cloudflare Access rather than implementing a second public username/password login system.

## Managed OAuth for MCP clients

For MCP clients that support OAuth, the protected Access application can use **Cloudflare Access Managed OAuth**.

The general setup is:

```text
MCP client
    |
    | OAuth authorization
    v
Cloudflare Access
    |
    | authenticated request
    v
/mcp
```

Register only the redirect URIs required by the MCP clients you actually intend to use.

Localhost or loopback redirect clients should be enabled only when they are needed for local development or for a trusted client that specifically requires them.

OAuth grant duration and access-token lifetime are deployment policy choices and should be selected according to the security requirements of the installation.

Managed OAuth protects the MCP connection. It does **not** make the email callback endpoints below private, because receiving mail clients cannot complete this OAuth flow when fetching message resources.

## Public callback exception

Two classes of endpoint need a different policy:

```text
/api/amp/*
/track/open/*
```

These paths are fetched by external email infrastructure rather than by the authenticated administrator or MCP client.

For example:

- an AMP-capable mail client performs an XHR request to the AMP endpoint;
- an email client or image proxy fetches the open-tracking pixel.

Those systems cannot complete the normal Cloudflare Access login or MCP OAuth flow.

If these features are enabled, create a **separate, narrowly scoped Access application or equivalent path policy** whose destinations contain only the required callback paths and apply a **Bypass** action to those destinations.

Conceptually:

```text
Protected application
    mcp.example.com/*
        -> Allow authorized identities

Public callback application
    mcp.example.com/api/amp/*
    mcp.example.com/track/open/*
        -> Bypass
```

Do **not** apply the bypass to:

```text
/
 /mcp
/dashboard/*
mailbox routes
account management
task management
```

A bypass should never cover the whole hostname simply because AMP or tracking is enabled.

## Why the public routes can be exposed

The public routes are deliberately designed as narrow capability endpoints rather than general API access.

### Open-tracking endpoint

Tracked deliveries receive a cryptographically random per-recipient token.

The public URL has the form:

```text
https://mcp.example.com/track/open/<random-token>.gif
```

The token is created with a cryptographically secure random generator and is unique for the delivery.

The endpoint:

- records an open/image-load observation when the token is valid;
- does not expose mailbox credentials;
- does not expose MCP tools;
- does not provide dashboard access;
- always returns the tracking image even when recording fails, avoiding a simple token-validity response oracle;
- sends no-cache headers.

Open tracking remains telemetry, not proof that a human read a message. Image proxies, scanners, prefetching and image blocking can create or suppress observations.

### AMP endpoint

Each AMP-enabled delivery receives a separate cryptographically random token.

The AMP callback URL is recipient-scoped and time-limited.

The endpoint verifies:

```text
delivery token
token expiration
sender account AMP state
AMP Email sender/origin headers
```

before returning dynamic data.

The AMP token does not grant access to the general MCP API or dashboard.

## Path isolation is essential

The security of this design depends on keeping the public callback surface small.

A useful mental model is:

```text
authenticated control plane
    !=
public email callback plane
```

The callback plane must never grow into a general unauthenticated API.

Any new public endpoint should be reviewed individually before being added to the Access bypass destinations.

## Origin isolation

Cloudflare Access protects requests that pass through Cloudflare, so the origin must not provide an easier route around it.

Recommended deployment rules:

- expose the service to Cloudflare through a Tunnel or another trusted reverse-proxy path;
- do not publish the raw application port directly to the Internet;
- restrict firewall/NAT rules accordingly;
- use the direct Portainer-published port only on a trusted LAN when needed;
- do not create a second public DNS/origin route that bypasses Access.

The desired topology is:

```text
Internet
   |
   X----> raw :8787 / :8000        blocked
   |
   v
Cloudflare
   |
   v
Tunnel
   |
   v
Postmaster MCP
```

## Server-side defense in depth

Cloudflare Access is only the outer authentication boundary. Postmaster MCP still applies server-side protections.

### Encrypted mailbox credentials

Mailbox credentials are stored in the persistent account database in encrypted form.

```text
/data/mail_accounts.db
/data/mail_accounts.key
```

The encryption key is kept separately from the database content. Both must be protected and backed up appropriately.

Credentials are used server-side and are not returned through normal MCP tools.

### Recipient authorization

Outbound email can be constrained by recipient/domain authorization rules.

This provides a second boundary between:

```text
AI has access to send_email
```

and:

```text
AI may send to any address on the Internet
```

Deployments can maintain explicit authorized recipients and domains and review them from the dashboard/MCP tools.

### Explicit write operations

Tools that modify state are intentionally distinct from read-only tools.

Examples include:

```text
send_email
reply_email
create_draft
move_email
mark_as_spam
mark_not_spam
authorize_recipient
authorize_domain
create_job
complete_job
```

This makes it possible for MCP clients and surrounding policy systems to treat write operations differently from inspection operations.

### Dashboard CSRF protection

Authentication is delegated to Cloudflare Access, but dashboard form writes also use an application-side CSRF token.

This provides an additional control for browser-originated state-changing requests.

### Registry-only task execution

The task scheduler is intentionally configured as:

```text
task_registry_only
autonomous_execution: false
```

A stored task cannot independently send an email merely because its schedule became due.

An authorized AI/client must:

1. retrieve the due task;
2. inspect the relevant current state;
3. decide what action is appropriate;
4. invoke an explicit MCP operation;
5. mark the task handled.

This prevents the task database itself from becoming an unattended email execution engine.

## Recommended Cloudflare Access layout

A generic production layout is:

```text
Application A — Postmaster MCP control plane

Destination:
    mcp.example.com

Policy:
    Allow -> authorized administrators / MCP users

Managed OAuth:
    enabled when OAuth-capable MCP clients are used

Redirect URIs:
    only those required by trusted clients


Application B — Postmaster public email callbacks

Destinations:
    mcp.example.com/api/amp/*
    mcp.example.com/track/open/*

Policy:
    Bypass
```

The names of the applications and policies are arbitrary. What matters is the separation of responsibilities.

## Security checklist

Before exposing a deployment publicly, verify:

```text
[ ] the whole hostname is protected by default
[ ] /mcp requires authenticated Access
[ ] the dashboard requires authenticated Access
[ ] only /api/amp/* and/or /track/open/* are bypassed when needed
[ ] the bypass does not cover the root hostname
[ ] Managed OAuth redirect URIs are restricted to trusted clients
[ ] the raw Docker port is not publicly reachable
[ ] mailbox credentials remain server-side
[ ] the account encryption key is backed up securely
[ ] recipient/domain authorization is configured as intended
[ ] tracking/AMP public hostname uses HTTPS
[ ] task execution remains registry-only unless deliberately redesigned
```

If AMP or tracking are not used, there is no reason to create the public callback bypass at all.

---

# MCP endpoint

The MCP endpoint is:

```text
/mcp
```

For example, after configuring a public hostname:

```text
https://mcp.example.com/mcp
```

The MCP transport is Streamable HTTP and runs stateless HTTP responses while using the MCP session manager internally.

---

# Dashboard

The WebGUI is served from:

```text
/
```

It provides views for:

```text
Overview
Accounts
AMP
Tracking
Authorized domains
Authorized recipients
Tasks
```

The dashboard and MCP endpoint share the same application server and container port.

---

# Configuration values in the public YAML

The included defaults are intentionally non-personal.

Important values include:

```yaml
TZ: UTC
ENABLE_SEND: 'true'
SEND_RECIPIENT_ALLOWLIST: ''
ALLOW_PREVIOUS_SENT_RECIPIENTS: 'true'
MCP_HOST: 0.0.0.0
MCP_PORT: '8000'
PUBLIC_MCP_HOST: ''
PUBLIC_EMAIL_BASE_URL: ''
DEFAULT_OWNER_ID: default
DEFAULT_OWNER_NAME: Default User
SEED_DEFAULT_PROJECT: 'true'
ALLOW_AUTOMATIC_EMAIL_JOBS: 'false'
```

Legacy single-account environment migration variables exist only for compatibility and are blank by default:

```yaml
LEGACY_EMAIL_ADDRESS: ''
LEGACY_EMAIL_PASSWORD: ''
```

For normal v8.7 use, configure accounts in the WebGUI instead.

---

# Health check

The stack contains a Docker health check that verifies that the application accepts TCP connections on:

```text
127.0.0.1:8000
```

The first start may take longer because the virtual environment and Python dependencies are installed before the server launches.

---

# Updating

Because the application source is embedded directly in the Compose YAML, updating the public distribution means replacing the stack YAML with the newer version and redeploying.

Persistent state remains in the named volumes unless those volumes are explicitly deleted.

Before a major update, back up:

```text
mcp_data
```

or at minimum the contents of `/data`.

---

# Repository structure

A minimal public repository can be:

```text
postmaster-mcp/
├── README.md
├── postmaster-mcp.yml
├── LICENSE
├── NOTICE
└── .gitignore
```

The YAML contains the deployable application itself.

No real mailbox address, password, private hostname, private domain allowlist or personal project data should be committed.

---

# Privacy-safe public distribution

The public stack replaces deployment-specific values with generic defaults and placeholders.

It does not contain:

```text
personal email accounts
mailbox passwords
private domains
personal names
private recipient allowlists
private task/project names
private Cloudflare hostnames
```

Technical public addresses required by standards or documentation may remain, such as Google's official AMP registration address.

---

# License

Postmaster MCP is licensed under the **Apache License 2.0**.

You may use, modify and redistribute the software, including commercially, subject to the license terms.

See:

```text
LICENSE
NOTICE
```

for the complete terms and attribution notices.

---

# Attribution

**Original project by the-code-learner.**

Please preserve the attribution notices contained in `NOTICE` when redistributing the software or derivative works in accordance with the Apache License 2.0.

---

# Disclaimer

This software can read, move and send real email and can persist access credentials for configured mailboxes.

Test it with a non-critical account before relying on it for important mail. Review your provider settings, recipient policy, backups and network exposure before production use.
