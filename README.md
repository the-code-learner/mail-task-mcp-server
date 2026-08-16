# Postmaster MCP

**A self-hosted MCP server for secure AI-assisted email management and persistent task workflows.**

Postmaster MCP is a self-hosted [Model Context Protocol](https://modelcontextprotocol.io/) server that gives authorized AI clients structured access to email accounts and persistent task management.

It is designed to let an AI client safely work with real mailboxes through MCP tools while keeping credentials, server-side state, task scheduling, permissions, and audit information under the control of the self-hosted server.

The project combines:

- IMAP mailbox access
- SMTP email delivery
- message search and retrieval
- drafts, replies and forwarding
- HTML email
- AMP email
- attachment handling
- spam management
- multiple email accounts
- persistent task registration
- conditional follow-ups
- AI-driven scheduled workflows
- explicit write operations
- human-in-the-loop approval patterns
- audit-friendly structured actions

---

## Why Postmaster MCP?

Most email integrations expose a mailbox directly to a single application or automation system.

Postmaster MCP takes a different approach:

```text
Email provider
      |
      v
 IMAP / SMTP
      |
      v
Postmaster MCP
      |
      v
Model Context Protocol
      |
      +------ AI Client A
      |
      +------ AI Client B
      |
      +------ Other MCP-compatible clients
```

The email provider remains independent from the AI provider.

The MCP server acts as the controlled interface between them.

This means the same self-hosted mailbox infrastructure can be used by different MCP-compatible AI clients without exposing raw credentials to every client.

---

# Core Features

## Email

Postmaster MCP can expose structured operations for:

```text
list mailboxes
search messages
read messages
read threads
download attachments
create drafts
send messages
reply
reply-all
forward
archive
move messages
delete messages
mark read / unread
mark spam / not spam
manage labels or folders
```

Exact capabilities depend on the configured backend and permissions.

---

## HTML Email

Messages can be sent as multipart email with both plain-text and HTML versions.

Example:

```text
text/plain
text/html
```

This allows normal email clients to display formatted content while retaining a plain-text fallback.

---

## AMP Email

Postmaster MCP can optionally generate multipart AMP messages using the standard MIME structure:

```text
text/plain
text/x-amp-html
text/html
```

This allows compatible email clients to display interactive AMP content while preserving standard HTML and plain-text fallbacks.

AMP support is optional and does not affect standard email functionality.

---

## Attachments

The server can support both sending and retrieving attachments.

Typical workflows include:

```text
AI reads an email
      |
      v
detects attachment
      |
      v
retrieves attachment metadata or file
      |
      v
AI evaluates the content
```

and:

```text
AI prepares email
      |
      +-- body
      +-- HTML
      +-- optional AMP
      +-- attachment
      |
      v
Postmaster MCP
      |
      v
SMTP provider
```

---

# Persistent Task Registry

Postmaster MCP includes a persistent task registry for workflows that need to survive individual AI conversations.

Examples:

```text
Check the inbox every 6 hours

Review Spam every 48 hours

Follow up with a contact next Tuesday

Check whether someone replied before sending a follow-up

Revisit a conversation in three months

Remind the AI to inspect a mailbox after a deadline
```

Tasks are stored persistently on the server.

---

## The Scheduler Does Not Need to Be the Agent

A core design principle of Postmaster MCP is the separation between:

```text
task storage
```

and:

```text
task execution
```

The server can behave as a persistent **task registry** rather than an autonomous agent.

For example:

```text
Persistent Task Registry
        |
        | due tasks
        v
   AI Client
        |
        | reasoning
        v
Postmaster MCP tools
        |
        v
 Email / other actions
```

The task registry determines **what is due**.

The AI determines **what should actually be done**.

This is particularly useful for tasks such as:

```text
"Follow up only if no reply was received."

"Move the message out of Spam only if it is clearly legitimate."

"Review unread messages and summarize only important ones."

"Send another message only if the previous conversation requires it."
```

These workflows require context and reasoning that a traditional cron job cannot reliably provide.

---

# Example Task

A registered task might contain:

```json
{
  "title": "Check inbox",
  "action_type": "action_required",
  "schedule_type": "interval",
  "schedule_value": "21600",
  "payload": {
    "mailbox": "INBOX",
    "filter": "unread_only",
    "action": "review_and_summarize"
  }
}
```

When the task becomes due, an authorized AI client can:

1. retrieve the task;
2. inspect the mailbox;
3. reason about the messages;
4. perform the appropriate MCP actions;
5. report the result;
6. mark the task as handled.

---

# Task Types

Possible task categories include:

```text
reminder
action_required
mailbox_review
spam_review
follow_up
conditional_follow_up
contact_check
periodic_summary
```

The task model is intentionally generic so additional workflows can be built on top of it.

---

# Multi-Account Support

Postmaster MCP can be designed to manage multiple email identities.

Example:

```text
accounts/
├── account-primary
├── account-support
└── account-project
```

Credentials remain server-side.

AI clients operate using account identifiers rather than receiving raw passwords or SMTP credentials.

---

# Security Model

The server should be treated as a privileged interface to email infrastructure.

Security should therefore be enforced by the server rather than relying only on prompts.

Recommended principles:

```text
credentials stay server-side
explicit write actions
least-privilege permissions
auditable operations
separate account identities
human approval where appropriate
secret redaction
secure transport
restricted network exposure
```

---

## Read vs Write Operations

Operations should clearly distinguish between read-only and write actions.

Examples:

### Read-only

```text
search_email
read_email
list_mailboxes
download_attachment
list_tasks
list_due_tasks
get_task_history
```

### Write actions

```text
send_email
create_draft
move_email
delete_email
mark_spam
mark_not_spam
complete_task
create_task
update_task
```

Clients can use this distinction when applying permission or approval policies.

---

# Human-in-the-Loop

Sensitive operations can require explicit approval.

Examples:

```text
send a new external email
delete messages
send a bulk message
modify a sensitive task
perform an irreversible operation
```

Read-only inspection and low-risk operations can be handled separately.

The exact approval policy is deployment-specific.

---

# Suggested Architecture

```text
                    Internet
                       |
                       v
                Secure Gateway
                       |
                       v
               Postmaster MCP
                       |
          +------------+------------+
          |            |            |
          v            v            v
        IMAP          SMTP      Task Registry
          |            |            |
          v            v            v
       Mailbox       Delivery      SQLite
```

Optional components:

```text
reverse proxy
Cloudflare Tunnel
Cloudflare Access
OAuth
Web dashboard
scheduler UI
audit log
multi-account configuration
```

---

# Example Deployment

A typical self-hosted deployment might look like:

```text
Debian / Ubuntu server
        |
        +-- Postmaster MCP
        |
        +-- SQLite
        |
        +-- IMAP / SMTP connection
        |
        +-- reverse proxy or secure tunnel
```

No inbound mail server is required if Postmaster MCP is connecting to an existing IMAP/SMTP provider.

---

# Configuration

Configuration should be provided through environment variables or external configuration files.

Example:

```yaml
server:
  host: 127.0.0.1
  port: 8000

accounts:
  - id: account-primary
    imap_host: imap.example.com
    smtp_host: smtp.example.com
```

Secrets should **not** be committed to Git.

Use environment variables or an external secrets mechanism for:

```text
passwords
OAuth tokens
API tokens
private keys
SMTP credentials
IMAP credentials
```

---

# Repository Safety

This repository is intended to be public.

The project follows a **public-by-design** rule:

> If it is committed, it must be safe to publish.

Do not commit:

```text
.env
credentials
email passwords
OAuth tokens
API keys
private keys
real mailbox exports
personal email addresses
private domains
production configuration
mail contents
task databases
logs containing personal data
```

Use placeholders such as:

```text
user@example.com
imap.example.com
smtp.example.com
account-primary
```

---

# Example `.env.example`

```env
MCP_HOST=127.0.0.1
MCP_PORT=8000

IMAP_HOST=imap.example.com
IMAP_PORT=993

SMTP_HOST=smtp.example.com
SMTP_PORT=465

EMAIL_USERNAME=user@example.com

# Never put real secrets in this file.
EMAIL_PASSWORD=CHANGE_ME
```

---

# Development Philosophy

Postmaster MCP aims to keep the MCP interface:

```text
structured
predictable
auditable
provider-independent
AI-provider-independent
```

The server should expose capabilities.

The AI client should provide reasoning.

For example:

```text
MCP:
"Here are the unread emails."

AI:
"This message is important."

MCP:
"Here is the tool for moving it."

AI:
"Move it to Inbox."

MCP:
"Done."
```

The MCP server is not intended to become a second autonomous AI agent.

---

# Provider Independence

Postmaster MCP is not intended to depend on a specific email provider.

The architecture should work with any provider offering compatible:

```text
IMAP
SMTP
```

Similarly, the server should not depend on one particular AI vendor.

Any compatible MCP client can potentially use the same server.

---

# Use Cases

Examples include:

### Personal email assistant

```text
review unread mail
summarize important messages
prepare drafts
surface messages requiring action
```

### Follow-up management

```text
register follow-up
wait for deadline
check conversation
send only if needed
```

### Support mailbox

```text
inspect incoming requests
categorize messages
prepare responses
track unresolved conversations
```

### Spam review

```text
periodically inspect Spam
identify false positives
restore legitimate messages
report what changed
```

### Project mailbox

```text
manage a dedicated project address
track external conversations
schedule follow-ups
retain persistent task state
```

---

# Example Workflow

```text
Incoming message
      |
      v
Postmaster MCP
      |
      v
AI reads message
      |
      +-- no action
      |
      +-- draft response
      |
      +-- register follow-up
              |
              v
       Persistent Task
              |
          three days
              |
              v
        AI checks thread
              |
        +-----+------+
        |            |
      reply       no reply
        |            |
      done       follow up
```

---

# Non-Goals

Postmaster MCP is not intended to be:

```text
a complete email provider
a replacement for IMAP/SMTP
an autonomous spam classifier
a general-purpose AI agent
a CRM
a marketing automation platform
```

Those systems can instead be integrated around the MCP server.

---

# Roadmap

Potential future features include:

- [ ] Web administration dashboard
- [ ] account management UI
- [ ] task registry UI
- [ ] permissions dashboard
- [ ] audit log viewer
- [ ] OAuth-based account authentication
- [ ] additional email providers
- [ ] improved attachment handling
- [ ] richer HTML composition
- [ ] AMP email components
- [ ] message templates
- [ ] contact integration
- [ ] task dependencies
- [ ] task history visualization
- [ ] multi-user access
- [ ] scoped MCP permissions
- [ ] import/export configuration
- [ ] Docker deployment
- [ ] automated secret scanning
- [ ] test mail server environment

---

# Contributing

Contributions are welcome.

When modifying the project:

1. create a dedicated branch;
2. keep changes focused;
3. add or update tests where appropriate;
4. document new MCP capabilities;
5. never commit credentials or personal mailbox data;
6. clearly indicate modifications to existing source files where required by the license.

Example branch names:

```text
feature/task-registry
feature/amp-email
fix/imap-folder-handling
docs/deployment
```

---

# License

Licensed under the **Apache License 2.0**.

You may:

- use the software;
- modify it;
- redistribute it;
- use it commercially;
- include it in other projects.

Redistributions and derivative works must comply with the attribution and notice requirements of the Apache License 2.0.

See:

```text
LICENSE
NOTICE
```

for details.

---

# Attribution

Original project by **The-code-learner**.

Please preserve the attribution notices contained in the `NOTICE` file when redistributing this software or derivative works, in accordance with the Apache License 2.0.

---

# Disclaimer

This software can perform operations on real email accounts.

Use it carefully.

Before deploying it against important mailboxes:

```text
review permissions
configure backups
test write operations
restrict network exposure
protect credentials
review approval policies
```

The authors are not responsible for lost messages, accidental email delivery, account restrictions, data loss, or other consequences resulting from improper configuration or use.
