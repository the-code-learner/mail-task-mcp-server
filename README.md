# Postmaster MCP

**Self-hosted MCP server for AI-assisted email management and persistent task workflows.**

Postmaster MCP is a self-contained Model Context Protocol server that connects MCP-capable AI clients to one or more IMAP/SMTP mailboxes while keeping credentials, task state, recipient policies, analytics and operational data on your own server.

The public v8.6 Portainer stack includes the application source directly inside a single Docker Compose YAML file. No separate application image or source checkout is required to start it.

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

---

## v8.6 capabilities

The included stack exposes **54 MCP tools**.

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

`approve_job` is retained for compatibility, but the v8.6 task registry does not autonomously execute tasks.

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
postmaster-mcp-v8.6-portainer.yml
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

The public distribution is intentionally a **single YAML file**.

It contains:

```text
Docker service definition
runtime environment
persistent volumes
startup command
health check
server.py
mail_bridge.py
scheduler_engine.py
mail_extensions.py
account_store.py
email_analytics.py
```

The Python modules are embedded using Compose `configs:` entries.

At first start the container creates a Python virtual environment in the persistent `mcp_venv` volume and installs:

```text
mcp
croniter
tzdata
pypdf
cryptography
```

Application state is stored in the persistent `mcp_data` volume mounted at:

```text
/data
```

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

Postmaster MCP supports plain-text and HTML email.

A normal multipart message can contain:

```text
text/plain
text/html
```

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

Open tracking is opt-in.

For tracked multi-recipient sends, v8.6 can create individualized deliveries with distinct tracking tokens while preserving the visible To/Cc headers.

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

The application is designed to sit behind an external access layer.

The stack itself does **not** provide a complete public-facing login system for the dashboard. Do not expose port `8787` directly to the public Internet without a trusted access layer.

A typical deployment is:

```text
Internet
    |
    v
Cloudflare Access / trusted reverse proxy
    |
    v
Postmaster MCP :8000
```

The original deployment model uses Cloudflare Access externally.

Recommended rules:

- protect `/` and `/mcp` behind authenticated access;
- keep mailbox credentials only in the encrypted server-side account store;
- back up the encryption key together with the corresponding database;
- review recipient authorization before enabling automated sending;
- avoid exposing the raw Docker port publicly.

## AMP and tracking exception

Mail clients cannot complete an interactive dashboard login when loading an AMP XHR endpoint or tracking pixel.

If you enable those features, narrowly scoped routes such as:

```text
/api/amp/*
/track/open/*
```

must be reachable by the receiving mail client. Configure your reverse proxy/access policy accordingly. The endpoint tokens are scoped and unguessable, but exposing these routes is still a deployment decision you should review carefully.

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

For normal v8.6 use, configure accounts in the WebGUI instead.

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
├── postmaster-mcp-v8.6-portainer.yml
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
