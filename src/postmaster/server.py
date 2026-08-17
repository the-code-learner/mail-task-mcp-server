from __future__ import annotations

import os
import contextlib
import logging
import hmac
import secrets
from functools import lru_cache
from html import escape
from typing import Any
from urllib.parse import urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, PlainTextResponse, Response, JSONResponse
from starlette.routing import Mount, Route

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .hostinger_mail import MailBridgeError, Settings
from .mail_extensions import EnhancedHostingerMailClient
from .account_store import MailAccountStore, AccountStoreError
from .email_analytics import analytics_store, AnalyticsError, TRANSPARENT_GIF, validate_amp_document
from .scheduler_engine import SchedulerEngine, SchedulerError, SchedulerSettings
from .knowledge_store import KnowledgeError
from .semantic_engine import SemanticError
from .context_engine import ContextEngine


logger = logging.getLogger("postmaster-mcp")


mcp = MCPServer(
    "Postmaster Self-Hosted MCP",
    instructions=(
        "Private self-hosted MCP server protected externally by Cloudflare Access. "
        "v9.0 adds persistent project memory/skills, revision history, FTS5 and optional Model2Vec hybrid retrieval. "
        "Multiple encrypted IMAP/SMTP accounts remain configurable from the WebGUI. "
        "Every mailbox/email MCP operation accepts an optional account_id; when omitted, "
        "the configured default account is used. HTML email, drafts, attachments, attachment "
        "reuse/readback, mailbox moves, spam actions and recipient safety policies remain available. "
        "The scheduler is registry-only: it stores tasks/reminders and schedule metadata but never "
        "runs actions or sends email autonomously. AMP for Email is opt-in per sender account. ""Per-recipient open tracking is opt-in per send (or an account default) and records repeat image-load events separately."
    ),
)


@lru_cache(maxsize=1)
def account_store() -> MailAccountStore:
    return MailAccountStore()


def mail_client(account_id: str | None = None) -> EnhancedHostingerMailClient:
    return EnhancedHostingerMailClient(account_store().settings(account_id))


@lru_cache(maxsize=1)
def policy_client() -> EnhancedHostingerMailClient:
    # Recipient/domain policy is global and can be managed even before an account exists.
    return EnhancedHostingerMailClient(
        Settings(
            email_address="policy@localhost",
            email_password="",
            enable_send=False,
            account_id="policy",
        )
    )


@lru_cache(maxsize=1)
def scheduler() -> SchedulerEngine:
    # Registry only. No worker is started and no mail client is attached for execution.
    return SchedulerEngine(SchedulerSettings.from_env())


@lru_cache(maxsize=1)
def context_engine() -> ContextEngine:
    return ContextEngine()


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (MailBridgeError, SchedulerError, AccountStoreError, AnalyticsError, KnowledgeError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("Unhandled MCP operation failure in %s", getattr(fn, "__name__", repr(fn)))
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# -------------------------
# Build / capability identity
# -------------------------
@mcp.tool()
def build_status():
    """Read-only. Return the running bridge build and high-level v9.0 capabilities."""
    return {
        "ok": True,
        "build": os.getenv("BRIDGE_BUILD", "unknown"),
        "multi_account": True,
        "amp_per_account": True,
        "per_recipient_open_tracking": True,
        "reply_open_tracking": True,
        "tracking_default_applies_to_replies": True,
        "visible_to_cc_preserved_for_tracked_fanout": True,
        "scheduler_mode": "task_registry_only",
        "persistent_context": True,
        "fts5_search": True,
        "optional_model2vec": True,
    }


# -------------------------
# Email accounts
# -------------------------
@mcp.tool()
def list_email_accounts():
    """Read-only. List configured email accounts without credentials."""
    return {"ok": True, "accounts": account_store().list_accounts()}


@mcp.tool()
def test_email_account(account_id: str | None = None):
    """Read-only network test. Authenticate to the selected account's IMAP and SMTP servers without sending."""
    return _safe_call(mail_client(account_id).test_connections)


@mcp.tool()
def amp_account_status(account_id: str | None = None):
    """
    Read-only. Return AMP-for-Email capability and Google registration checklist for one sender.
    AMP capability is per account and is only usable when amp_enabled=true.
    """
    return _safe_call(account_store().amp_status, account_id)


@mcp.tool()
def set_amp_account_state(
    account_id: str,
    enabled: bool | None = None,
    tested: bool | None = None,
    registered: bool | None = None,
    review_sent: bool = False,
    notes: str | None = None,
):
    """WRITE ACTION. Update opt-in AMP capability/registration state for one sender account."""
    return _safe_call(
        account_store().set_amp_state,
        account_id,
        enabled=enabled,
        tested=tested,
        registered=registered,
        review_sent=review_sent,
        notes=notes,
    )


@mcp.tool()
def validate_amp_email(body_amp: str):
    """
    Read-only. Run local structural AMP-for-Email preflight checks.
    This does not replace Google's delivered-message/AMP validation.
    """
    return validate_amp_document(body_amp)


# -------------------------
# Email read/search
# -------------------------
@mcp.tool()
def mailbox_status(account_id: str | None = None) -> dict[str, Any]:
    """Read-only. Verify IMAP connectivity for one account. Omit account_id to use the default account."""
    client = mail_client(account_id)
    base = _safe_call(client.ping)
    if isinstance(base, dict) and base.get("ok"):
        account = account_store().get_account(account_id)
        base.update({
            "build": os.getenv("BRIDGE_BUILD", "unknown"),
            "html_email": True,
            "draft_mailbox": client.draft_mailbox,
            "inbox_mailbox": client.inbox_mailbox,
            "junk_mailbox": client.junk_mailbox,
            "attachment_download": True,
            "attachment_text_read": True,
            "mailbox_move": True,
            "open_tracking": True,
            "amp_email": bool(account.get("amp_enabled")),
        })
    return base


@mcp.tool()
def list_mailboxes(account_id: str | None = None):
    """Read-only. List available IMAP mailboxes for the selected account."""
    return _safe_call(mail_client(account_id).list_mailboxes)


@mcp.tool()
def search_emails(
    mailbox: str = "INBOX",
    from_address: str | None = None,
    to_address: str | None = None,
    subject: str | None = None,
    text: str | None = None,
    since_days: int = 90,
    unread_only: bool = False,
    limit: int = 20,
    account_id: str | None = None,
):
    """Read-only. Search one selected email account using structured filters."""
    return _safe_call(
        mail_client(account_id).search_emails,
        mailbox=mailbox, from_address=from_address, to_address=to_address,
        subject=subject, text=text, since_days=since_days,
        unread_only=unread_only, limit=limit,
    )


@mcp.tool()
def get_email(mailbox: str, uid: str, account_id: str | None = None):
    """Read-only. Fetch one complete email from the selected account by mailbox and IMAP UID."""
    return _safe_call(mail_client(account_id).get_email, mailbox, uid)


@mcp.tool()
def list_known_contacts(account_id: str | None = None):
    """Read-only. List historical Sent recipients/domains for the selected account."""
    return _safe_call(mail_client(account_id).list_known_contacts)


# Attachments
@mcp.tool()
def list_email_attachments(mailbox: str, uid: str, account_id: str | None = None):
    """Read-only. List attachments for one email in the selected account."""
    return _safe_call(mail_client(account_id).list_email_attachments, mailbox, uid)


@mcp.tool()
def get_email_attachment(
    mailbox: str,
    uid: str,
    filename: str | None = None,
    index: int | None = None,
    include_base64: bool = True,
    account_id: str | None = None,
):
    """Read-only. Download one attachment from the selected account."""
    return _safe_call(
        mail_client(account_id).get_email_attachment,
        mailbox, uid, filename=filename, index=index, include_base64=include_base64,
    )


@mcp.tool()
def read_email_attachment(
    mailbox: str,
    uid: str,
    filename: str | None = None,
    index: int | None = None,
    max_chars: int | None = None,
    account_id: str | None = None,
):
    """Read-only. Extract readable text from a supported attachment in the selected account."""
    return _safe_call(
        mail_client(account_id).read_email_attachment,
        mailbox, uid, filename=filename, index=index, max_chars=max_chars,
    )


# Recipient policy
@mcp.tool()
def email_security_status(account_id: str | None = None):
    """Read-only. Show outbound policy for the selected sender account without exposing credentials."""
    client = mail_client(account_id)
    cfg = client.settings
    explicit = policy_client().list_authorized_recipients()
    return {
        "ok": True,
        "account_id": cfg.account_id,
        "account": cfg.email_address,
        "send_enabled": cfg.enable_send,
        "managed_allowlist_domains": [
            x["domain"] for x in policy_client().list_authorized_domains().get("domains", [])
        ],
        "allow_previous_sent_recipients": cfg.allow_previous_sent_recipients,
        "exact_authorized_recipient_count": explicit.get("count", 0),
        "draft_mailbox": client.draft_mailbox,
        "html_email": True,
        "drafts_allow_unlisted_recipients": True,
        "attachments": {
            "enabled": True,
            "base64_download": True,
            "reuse_from_existing_email": True,
            "max_decoded_total_bytes": client.max_attachment_bytes,
        },
        "mailbox_writes": {
            "move_email": True,
            "mark_not_spam": True,
            "mark_as_spam": True,
            "set_seen": True,
        },
    }


@mcp.tool()
def recipient_authorization_status(recipients: list[str], account_id: str | None = None):
    """Read-only. Explain authorization for recipients using the selected account's Sent history plus global policy."""
    return _safe_call(mail_client(account_id).recipient_authorization_status, recipients)


@mcp.tool()
def list_authorized_recipients():
    """Read-only. List global exact-address authorizations used by all configured sender accounts."""
    return _safe_call(policy_client().list_authorized_recipients)


@mcp.tool()
def list_authorized_domains():
    """Read-only. List global authorized domains used by all configured sender accounts."""
    return _safe_call(policy_client().list_authorized_domains)


@mcp.tool()
def authorize_domain(domain: str, note: str = ""):
    """WRITE ACTION. Persistently authorize a domain and its subdomains for automated sends from configured accounts."""
    return _safe_call(policy_client().authorize_domain, domain, note)


@mcp.tool()
def revoke_domain(domain: str):
    """WRITE ACTION. Remove a domain from the persistent automated-send allowlist."""
    return _safe_call(policy_client().revoke_domain, domain)


@mcp.tool()
def authorize_recipient(email_address: str, note: str = ""):
    """WRITE ACTION. Persistently authorize one exact recipient for automated sends from configured accounts."""
    return _safe_call(policy_client().authorize_recipient, email_address, note)


@mcp.tool()
def revoke_recipient(email_address: str):
    """WRITE ACTION. Revoke one global exact-address authorization."""
    return _safe_call(policy_client().revoke_recipient, email_address)


# Email tracking / analytics
@mcp.tool()
def tracking_status():
    """Read-only. Return open-tracking store status and limitations."""
    return _safe_call(analytics_store().status)


@mcp.tool()
def list_tracking_campaigns(account_id: str | None = None, limit: int = 100):
    """Read-only. List tracked/AMP send campaigns with per-recipient and open-event counts."""
    return _safe_call(analytics_store().list_campaigns, account_id=account_id, limit=limit)


@mcp.tool()
def get_tracking_campaign(campaign_id: str):
    """Read-only. Return one tracking campaign summary."""
    return _safe_call(analytics_store().get_campaign, campaign_id)


@mcp.tool()
def list_tracking_deliveries(
    campaign_id: str | None = None,
    recipient: str | None = None,
    account_id: str | None = None,
    limit: int = 250,
):
    """Read-only. List individualized deliveries. Secret tracking/AMP tokens are never returned."""
    return _safe_call(
        analytics_store().list_deliveries,
        campaign_id=campaign_id,
        recipient=recipient,
        account_id=account_id,
        limit=limit,
    )


@mcp.tool()
def list_open_events(
    delivery_id: str | None = None,
    campaign_id: str | None = None,
    recipient: str | None = None,
    account_id: str | None = None,
    limit: int = 500,
):
    """
    Read-only. List observed remote-image load events, including repeat loads,
    kept separate by recipient/delivery. These are not guaranteed human reads.
    """
    return _safe_call(
        analytics_store().list_open_events,
        delivery_id=delivery_id,
        campaign_id=campaign_id,
        recipient=recipient,
        account_id=account_id,
        limit=limit,
    )


# Email writes / drafts
@mcp.tool()
def send_email(
    to: list[str],
    subject: str,
    body: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_html: str | None = None,
    body_amp: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    track_opens: bool | None = None,
    campaign_id: str | None = None,
    account_id: str | None = None,
):
    """
    WRITE ACTION. Send from the selected account.

    body_amp is optional and accepted only when AMP is enabled for that specific sender
    account. Normal accounts remain plain+HTML only.

    track_opens is optional. When true (or enabled as the account default), each recipient
    receives an individualized SMTP delivery with a unique pixel/token. The original visible
    To/Cc headers are preserved on every copy; Bcc remains hidden. Mail proxies/scanners can
    affect counts and browser/OS/country attribution.

    AMP templates may use {{AMP_STATUS_URL}}, {{TRACKING_PIXEL_URL}}, {{RECIPIENT}},
    {{CAMPAIGN_ID}} and {{DELIVERY_ID}} placeholders.
    """
    return _safe_call(
        mail_client(account_id).send_email,
        to=to, subject=subject, body=body, cc=cc, bcc=bcc,
        body_html=body_html, body_amp=body_amp, attachments=attachments,
        track_opens=track_opens, campaign_id=campaign_id,
    )


@mcp.tool()
def reply_email(
    mailbox: str,
    uid: str,
    body: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_html: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    track_opens: bool | None = None,
    campaign_id: str | None = None,
    account_id: str | None = None,
):
    """
    WRITE ACTION. Threaded HTML reply from the selected account.

    track_opens is optional:
    - None: use the selected account's tracking_default
    - True: force per-recipient open tracking for this reply
    - False: disable tracking for this reply

    Tracked replies preserve visible To/Cc plus In-Reply-To/References on every
    individualized delivery. Bcc recipients remain hidden.
    """
    return _safe_call(
        mail_client(account_id).reply_email,
        mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
        body_html=body_html, attachments=attachments,
        track_opens=track_opens, campaign_id=campaign_id,
    )


@mcp.tool()
def create_draft(
    to: list[str],
    subject: str,
    body: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_html: str | None = None,
    body_amp: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    account_id: str | None = None,
):
    """
    WRITE ACTION. Save a draft in the selected account. body_amp is optional and
    accepted only when AMP capability is enabled for that sender account.
    New/unlisted recipients are allowed for manual review.
    """
    return _safe_call(
        mail_client(account_id).create_draft,
        to=to, subject=subject, body=body, cc=cc, bcc=bcc,
        body_html=body_html, body_amp=body_amp, attachments=attachments,
    )


@mcp.tool()
def create_reply_draft(
    mailbox: str,
    uid: str,
    body: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    body_html: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    account_id: str | None = None,
):
    """WRITE ACTION. Save a threaded HTML reply draft in the selected account."""
    return _safe_call(
        mail_client(account_id).create_reply_draft,
        mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
        body_html=body_html, attachments=attachments,
    )


# Mailbox state writes
@mcp.tool()
def move_email(mailbox: str, uid: str, destination_mailbox: str, account_id: str | None = None):
    """WRITE ACTION. Move one email inside the selected account."""
    return _safe_call(mail_client(account_id).move_email, mailbox, uid, destination_mailbox)


@mcp.tool()
def mark_not_spam(mailbox: str, uid: str, account_id: str | None = None):
    """WRITE ACTION. Clear junk flags best-effort and move a message to the selected account's Inbox."""
    return _safe_call(mail_client(account_id).mark_not_spam, mailbox, uid)


@mcp.tool()
def mark_as_spam(mailbox: str, uid: str, account_id: str | None = None):
    """WRITE ACTION. Add junk flags best-effort and move a message to the selected account's Junk mailbox."""
    return _safe_call(mail_client(account_id).mark_as_spam, mailbox, uid)


@mcp.tool()
def set_email_seen(mailbox: str, uid: str, seen: bool = True, account_id: str | None = None):
    """WRITE ACTION. Mark an email read or unread in the selected account."""
    return _safe_call(mail_client(account_id).set_seen, mailbox, uid, seen)



# -------------------------
# Persistent Context / Memory / Skills (v9)
# -------------------------
def _require_knowledge_scope(owner_id: str, project_id: str | None = None) -> None:
    owner_id = (owner_id or "").strip()
    owners = scheduler().list_owners()
    if not any(str(o.get("id")) == owner_id for o in owners):
        raise SchedulerError(f"Unknown owner: {owner_id}")
    if project_id:
        projects = scheduler().list_projects(owner_id=owner_id)
        if not any(str(p.get("id")) == project_id and bool(p.get("active")) for p in projects):
            raise SchedulerError(f"Unknown/disabled project {project_id!r} for owner {owner_id!r}")


def _require_knowledge_kind(item_id: str, kind: str) -> dict[str, Any]:
    item = context_engine().store.get_item(item_id)
    if item.get("kind") != kind:
        raise KnowledgeError(f"Knowledge item {item_id} is {item.get('kind')!r}, not {kind!r}")
    return item


@mcp.tool()
def knowledge_status():
    """Read-only. Return v9 persistent-context, FTS5 and optional semantic-engine status."""
    return _safe_call(context_engine().status)


@mcp.tool()
def create_memory(
    owner_id: str, title: str, content: str, project_id: str | None = None,
    priority: float = 0.5, always_include: bool = False, enabled: bool = True,
    tags: list[str] | None = None, metadata: dict[str, Any] | None = None,
):
    """WRITE ACTION. Create persistent project/global memory. project_id reuses the scheduler project registry."""
    try:
        _require_knowledge_scope(owner_id, project_id)
        return context_engine().create(
            kind="memory", owner_id=owner_id, project_id=project_id, title=title, content=content,
            priority=priority, always_include=always_include, enabled=enabled, tags=tags or [],
            metadata=metadata or {}, actor="mcp",
        )
    except (KnowledgeError, SchedulerError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def get_memory(item_id: str):
    """Read-only. Get one persistent memory by ID."""
    try:
        return _require_knowledge_kind(item_id, "memory")
    except KnowledgeError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def update_memory(
    item_id: str, title: str | None = None, content: str | None = None,
    priority: float | None = None, always_include: bool | None = None, enabled: bool | None = None,
    tags: list[str] | None = None, metadata: dict[str, Any] | None = None,
):
    """WRITE ACTION. Update a memory and create an immutable revision snapshot."""
    try:
        _require_knowledge_kind(item_id, "memory")
        return context_engine().update(
            item_id, title=title, content=content, priority=priority, always_include=always_include,
            enabled=enabled, tags=tags, metadata=metadata, actor="mcp",
        )
    except (KnowledgeError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def delete_memory(item_id: str):
    """WRITE ACTION. Delete a memory. The audit event is retained."""
    try:
        _require_knowledge_kind(item_id, "memory")
        return context_engine().delete(item_id, actor="mcp")
    except KnowledgeError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_memories(
    owner_id: str | None = None, project_id: str | None = None,
    include_global: bool = True, enabled_only: bool = False, tag: str | None = None, limit: int = 200,
):
    """Read-only. List memories, optionally scoped to an existing owner/project."""
    return _safe_call(
        context_engine().store.list_items, kind="memory", owner_id=owner_id, project_id=project_id,
        include_global=include_global, enabled_only=enabled_only, tag=tag, limit=limit,
    )


@mcp.tool()
def create_skill(
    owner_id: str, title: str, content: str, project_id: str | None = None,
    priority: float = 0.5, always_include: bool = False, enabled: bool = True,
    tags: list[str] | None = None, metadata: dict[str, Any] | None = None,
):
    """WRITE ACTION. Create a persistent reusable skill/instruction scoped globally or to a scheduler project."""
    try:
        _require_knowledge_scope(owner_id, project_id)
        return context_engine().create(
            kind="skill", owner_id=owner_id, project_id=project_id, title=title, content=content,
            priority=priority, always_include=always_include, enabled=enabled, tags=tags or [],
            metadata=metadata or {}, actor="mcp",
        )
    except (KnowledgeError, SchedulerError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def get_skill(item_id: str):
    """Read-only. Get one persistent skill by ID."""
    try:
        return _require_knowledge_kind(item_id, "skill")
    except KnowledgeError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def update_skill(
    item_id: str, title: str | None = None, content: str | None = None,
    priority: float | None = None, always_include: bool | None = None, enabled: bool | None = None,
    tags: list[str] | None = None, metadata: dict[str, Any] | None = None,
):
    """WRITE ACTION. Update a skill and create an immutable revision snapshot."""
    try:
        _require_knowledge_kind(item_id, "skill")
        return context_engine().update(
            item_id, title=title, content=content, priority=priority, always_include=always_include,
            enabled=enabled, tags=tags, metadata=metadata, actor="mcp",
        )
    except (KnowledgeError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def delete_skill(item_id: str):
    """WRITE ACTION. Delete a skill. The audit event is retained."""
    try:
        _require_knowledge_kind(item_id, "skill")
        return context_engine().delete(item_id, actor="mcp")
    except KnowledgeError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_skills(
    owner_id: str | None = None, project_id: str | None = None,
    include_global: bool = True, enabled_only: bool = False, tag: str | None = None, limit: int = 200,
):
    """Read-only. List skills, optionally scoped to an existing owner/project."""
    return _safe_call(
        context_engine().store.list_items, kind="skill", owner_id=owner_id, project_id=project_id,
        include_global=include_global, enabled_only=enabled_only, tag=tag, limit=limit,
    )


@mcp.tool()
def search_knowledge(
    query: str, owner_id: str | None = None, project_id: str | None = None,
    include_global: bool = True, kinds: list[str] | None = None, limit: int = 20,
):
    """Read-only. Hybrid FTS5 + Model2Vec search. Automatically falls back to FTS5 if semantic search is unavailable."""
    if project_id and not owner_id:
        return {"ok": False, "error": "owner_id is required when project_id is provided"}
    return _safe_call(
        context_engine().search, query, owner_id=owner_id, project_id=project_id,
        include_global=include_global, kinds=kinds, limit=limit,
    )


@mcp.tool()
def get_project_context(
    owner_id: str, project_id: str | None = None, query: str = "",
    budget_chars: int = 12000, kinds: list[str] | None = None, limit: int = 40,
):
    """Read-only. Build a bounded context package from always-include and relevant memories/skills."""
    try:
        _require_knowledge_scope(owner_id, project_id)
        return context_engine().project_context(
            owner_id=owner_id, project_id=project_id, query=query, budget_chars=budget_chars,
            kinds=kinds, limit=limit,
        )
    except (KnowledgeError, SchedulerError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def get_knowledge_history(item_id: str, limit: int = 100):
    """Read-only. Return immutable revision snapshots for one memory/skill."""
    return _safe_call(context_engine().store.revisions, item_id, limit=limit)


@mcp.tool()
def restore_knowledge_revision(item_id: str, revision: int):
    """WRITE ACTION. Restore an older snapshot as a new current revision; history is not rewritten."""
    try:
        result = context_engine().store.restore_revision(item_id, revision, actor="mcp-restore")
        context_engine()._index_item_if_loaded(item_id)
        return result
    except (KnowledgeError, SemanticError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def get_knowledge_audit(item_id: str | None = None, limit: int = 200):
    """Read-only. Return persistent create/update/delete/import audit events."""
    return _safe_call(context_engine().store.audit, item_id=item_id, limit=limit)


@mcp.tool()
def export_knowledge(owner_id: str | None = None, project_id: str | None = None):
    """Read-only. Export memories/skills as a portable postmaster-knowledge-v1 JSON-compatible bundle."""
    return _safe_call(context_engine().store.export_bundle, owner_id=owner_id, project_id=project_id)


@mcp.tool()
def import_knowledge(
    bundle: dict[str, Any], owner_id_override: str | None = None,
    project_id_override: str | None = None, replace_existing: bool = False,
):
    """WRITE ACTION. Import a portable knowledge bundle. Scopes must already exist in the scheduler registry."""
    try:
        items = bundle.get("items", []) if isinstance(bundle, dict) else []
        scopes: set[tuple[str, str | None]] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            owner = owner_id_override or str(raw.get("owner_id") or "")
            project = project_id_override if project_id_override is not None else raw.get("project_id")
            scopes.add((owner, str(project) if project else None))
        for owner, project in scopes:
            _require_knowledge_scope(owner, project)
        result = context_engine().store.import_bundle(
            bundle, owner_id_override=owner_id_override, project_id_override=project_id_override,
            replace_existing=replace_existing, actor="mcp-import",
        )
        return result
    except (KnowledgeError, SchedulerError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def reindex_knowledge(
    item_id: str | None = None, owner_id: str | None = None,
    project_id: str | None = None, force: bool = False, limit: int = 100000,
):
    """WRITE ACTION. Generate/rebuild Model2Vec embeddings. Safe to call after installing a new model artifact."""
    return _safe_call(
        context_engine().reindex, item_id=item_id, owner_id=owner_id,
        project_id=project_id, force=force, limit=limit,
    )

# Scheduler
@mcp.tool()
def scheduler_status():
    """Read-only. Return persistent task-registry status. No autonomous worker runs in v8.7."""
    return _safe_call(scheduler().status)


@mcp.tool()
def create_owner(owner_id: str, display_name: str):
    """WRITE ACTION. Create a top-level scheduler owner/workspace."""
    return _safe_call(scheduler().create_owner, owner_id, display_name)


@mcp.tool()
def list_owners():
    """Read-only. List scheduler owners/workspaces."""
    return _safe_call(scheduler().list_owners)


@mcp.tool()
def create_project(owner_id: str, project_id: str, name: str, description: str = ""):
    """WRITE ACTION. Create a project under an owner."""
    return _safe_call(
        scheduler().create_project,
        owner_id=owner_id, project_id=project_id, name=name, description=description,
    )


@mcp.tool()
def list_projects(owner_id: str | None = None):
    """Read-only. List projects."""
    return _safe_call(scheduler().list_projects, owner_id=owner_id)


@mcp.tool()
def create_execution_profile(
    owner_id: str,
    project_id: str,
    profile_id: str,
    provider: str,
    identity: str,
    description: str = "",
):
    """WRITE ACTION. Create a project-scoped execution identity."""
    return _safe_call(
        scheduler().create_execution_profile,
        owner_id=owner_id, project_id=project_id, profile_id=profile_id,
        provider=provider, identity=identity, description=description,
    )


@mcp.tool()
def list_execution_profiles(owner_id: str | None = None, project_id: str | None = None):
    """Read-only. List execution profiles."""
    return _safe_call(
        scheduler().list_execution_profiles, owner_id=owner_id, project_id=project_id
    )


@mcp.tool()
def preview_schedule(
    schedule_type: str, schedule_value: str,
    timezone: str = "Europe/Rome", count: int = 5,
):
    """Read-only. Preview future once/interval/cron occurrences."""
    return _safe_call(
        scheduler().preview_schedule,
        schedule_type=schedule_type, schedule_value=schedule_value,
        timezone=timezone, count=count,
    )


@mcp.tool()
def create_job(
    owner_id: str,
    project_id: str,
    title: str,
    action_type: str,
    schedule_type: str,
    schedule_value: str,
    timezone: str = "Europe/Rome",
    description: str = "",
    execution_profile_id: str | None = None,
    payload: dict[str, Any] | None = None,
    approval_mode: str = "approval_required",
):
    """WRITE ACTION. Register a persistent task/reminder with schedule metadata. It will never execute autonomously."""
    return _safe_call(
        scheduler().create_job,
        owner_id=owner_id, project_id=project_id, title=title,
        description=description, action_type=action_type,
        execution_profile_id=execution_profile_id, payload=payload or {},
        schedule_type=schedule_type, schedule_value=schedule_value,
        timezone=timezone, approval_mode=approval_mode,
    )


@mcp.tool()
def list_jobs(
    owner_id: str | None = None, project_id: str | None = None,
    status: str | None = None, limit: int = 200,
):
    """Read-only. List registered tasks."""
    return _safe_call(
        scheduler().list_jobs,
        owner_id=owner_id, project_id=project_id, status=status, limit=limit,
    )


@mcp.tool()
def list_due_jobs(
    owner_id: str | None = None, project_id: str | None = None, limit: int = 200
):
    """Read-only. List tasks whose stored schedule is due. This is a read-only calculation."""
    return _safe_call(
        scheduler().list_due_jobs, owner_id=owner_id, project_id=project_id, limit=limit
    )


@mcp.tool()
def get_job(job_id: str):
    """Read-only. Get one scheduler job."""
    return _safe_call(scheduler().get_job, job_id)


@mcp.tool()
def update_job(
    job_id: str, title: str | None = None, description: str | None = None,
    payload: dict[str, Any] | None = None, schedule_type: str | None = None,
    schedule_value: str | None = None, timezone: str | None = None,
    approval_mode: str | None = None,
):
    """WRITE ACTION. Update a scheduler job."""
    return _safe_call(
        scheduler().update_job,
        job_id=job_id, title=title, description=description, payload=payload,
        schedule_type=schedule_type, schedule_value=schedule_value,
        timezone=timezone, approval_mode=approval_mode,
    )


@mcp.tool()
def pause_job(job_id: str):
    """WRITE ACTION. Pause a job."""
    return _safe_call(scheduler().pause_job, job_id)


@mcp.tool()
def resume_job(job_id: str):
    """WRITE ACTION. Resume a job."""
    return _safe_call(scheduler().resume_job, job_id)


@mcp.tool()
def approve_job(job_id: str):
    """Disabled in v8.2. The task registry never executes actions; use explicit MCP tools instead."""
    return _safe_call(scheduler().approve_job, job_id)


@mcp.tool()
def complete_job(job_id: str, note: str = ""):
    """WRITE ACTION. Mark a due registered task handled and advance recurring schedule metadata."""
    return _safe_call(scheduler().complete_job, job_id, note=note)


@mcp.tool()
def delete_job(job_id: str):
    """WRITE ACTION. Permanently delete a scheduled job; history is retained."""
    return _safe_call(scheduler().delete_job, job_id)


@mcp.tool()
def get_job_history(job_id: str, limit: int = 100):
    """Read-only. Show execution/due/approval history for one job."""
    return _safe_call(scheduler().get_job_history, job_id, limit=limit)





# -------------------------
# Web dashboard
# -------------------------
# Authentication is delegated to Cloudflare Access.
# The origin should not be directly exposed. CSRF remains enabled for writes.

_DASH_CSRF = secrets.token_urlsafe(32)


def _csrf_value() -> str:
    return _DASH_CSRF


def _layout(title: str, body: str, *, flash: str = "") -> HTMLResponse:
    flash_html = f'<div class="flash">{escape(flash)}</div>' if flash else ""
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{
  color-scheme: light dark;
  --bg:#0b0d10; --card:#15191e; --muted:#939ca8; --text:#eef2f6;
  --line:#2a3139; --accent:#68a0ff; --danger:#ff7676; --ok:#65d38e; --warn:#ffd166;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--text); }}
main {{ max-width:1320px; margin:0 auto; padding:28px 18px 60px; }}
h1 {{ margin:0 0 6px; font-size:28px; }}
h2 {{ margin:0 0 14px; font-size:18px; }}
h3 {{ margin:4px 0 10px; font-size:15px; }}
p.sub {{ margin:0 0 22px; color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; }}
.wide {{ grid-column:1/-1; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:9px 7px; vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
code,.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
input,select,button,textarea {{ border:1px solid var(--line); border-radius:8px; background:#101419; color:var(--text); padding:8px 10px; }}
textarea {{ width:100%; resize:vertical; font:inherit; line-height:1.45; }}
input[type=text],input[type=password],input[type=number],select {{ width:100%; }}
button {{ cursor:pointer; }}
button.primary {{ background:var(--accent); border-color:var(--accent); color:#07101e; font-weight:700; }}
button.danger {{ border-color:#673232; color:var(--danger); }}
button.ok {{ border-color:#285f3d; color:var(--ok); }}
.row {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.row > .grow {{ flex:1; min-width:160px; }}
.field {{ min-width:180px; flex:1; }}
.field label {{ display:block; font-size:12px; color:var(--muted); margin:0 0 5px; }}
.badge {{ display:inline-block; border:1px solid var(--line); border-radius:999px; padding:2px 8px; color:var(--muted); }}
.badge.ok {{ color:var(--ok); }}
.badge.warn {{ color:var(--warn); }}
.badge.err {{ color:var(--danger); }}
.muted {{ color:var(--muted); }}
.flash {{ margin:0 0 16px; border:1px solid #365b87; background:#10243a; padding:10px 12px; border-radius:9px; }}
.tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin:18px 0; border-bottom:1px solid var(--line); padding-bottom:10px; }}
.tab-link {{ display:inline-flex; align-items:center; gap:7px; border:1px solid var(--line); border-radius:10px; padding:9px 12px; color:var(--muted); text-decoration:none; background:var(--card); }}
.tab-link:hover {{ color:var(--text); border-color:var(--accent); }}
.tab-link.active {{ color:var(--text); border-color:var(--accent); box-shadow:inset 0 -2px 0 var(--accent); }}
.tab-count {{ display:inline-block; min-width:20px; text-align:center; border:1px solid var(--line); border-radius:999px; padding:1px 6px; font-size:11px; }}
.tab-panel {{ display:none; }}
.tab-panel.active {{ display:block; }}
.panel-title {{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:14px; }}
.panel-title h2 {{ margin:0; }}
.actions form {{ display:inline-block; margin:2px; }}
.scroll {{ overflow:auto; }}
.small {{ font-size:12px; }}
hr {{ border:0; border-top:1px solid var(--line); margin:16px 0; }}
.account-picker {{ max-width:540px; margin:0 0 16px; }}
.form-section {{ border-top:1px solid var(--line); padding-top:14px; margin-top:14px; }}
@media (prefers-color-scheme: light) {{
  :root {{ --bg:#f5f7fa; --card:white; --text:#17202a; --line:#dfe5ec; --muted:#65717e; }}
  input,select,button,textarea {{ background:white; color:var(--text); }}
}}
</style>
</head>
<body><main>{flash_html}{body}</main></body></html>"""
    return HTMLResponse(html)


async def _verified_form(request: Request):
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), _csrf_value()):
        return None, PlainTextResponse("Invalid CSRF token", status_code=403)
    return form, None


def _redir(message: str = "", tab: str = "overview", account_id: str | None = None):
    params = {}
    if message:
        params["flash"] = message
    if account_id:
        params["account"] = account_id
    target = "/" + (("?" + urlencode(params)) if params else "")
    if tab in {"overview", "accounts", "amp", "tracking", "domains", "recipients", "knowledge", "scheduler"}:
        target += f"#{tab}"
    return RedirectResponse(target, status_code=303)


def _account_options(accounts: list[dict[str, Any]], selected: str | None) -> str:
    out = ""
    for a in accounts:
        aid = str(a.get("id", ""))
        label = str(a.get("label") or a.get("email_address") or aid)
        suffix = " · default" if a.get("is_default") else ""
        sel = " selected" if aid == selected else ""
        out += f'<option value="{escape(aid)}"{sel}>{escape(label)} — {escape(str(a.get("email_address","")))}{suffix}</option>'
    return out


async def dashboard_home(request: Request):
    flash = request.query_params.get("flash", "")
    accounts = account_store().list_accounts()
    requested_account = request.query_params.get("account") or None
    selected_id = None
    selected = None
    client = None
    mail_stat: dict[str, Any] = {"ok": False, "error": "No account configured"}

    if accounts:
        try:
            selected_id = account_store().resolve_id(requested_account)
            selected = account_store().get_account(selected_id)
            client = mail_client(selected_id)
            mail_stat = _safe_call(client.ping)
        except Exception as exc:
            mail_stat = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    sched_stat = _safe_call(scheduler().status)
    domains = _safe_call(policy_client().list_authorized_domains)
    recipients = _safe_call(policy_client().list_authorized_recipients)
    jobs = _safe_call(scheduler().list_jobs, limit=250)
    due = _safe_call(scheduler().list_due_jobs, limit=100)

    knowledge_stat = _safe_call(context_engine().status)
    knowledge_items = _safe_call(context_engine().store.list_items, limit=500)
    knowledge_query = request.query_params.get("knowledge_q", "").strip()
    knowledge_search = _safe_call(context_engine().search, knowledge_query, limit=50) if knowledge_query else {"ok": True, "results": []}
    knowledge_projects = _safe_call(scheduler().list_projects)
    knowledge_owners = _safe_call(scheduler().list_owners)

    tracking_stat = _safe_call(analytics_store().status)
    tracking_campaigns = _safe_call(analytics_store().list_campaigns, limit=500)
    tracking_deliveries = _safe_call(analytics_store().list_deliveries, limit=1000)
    open_events = _safe_call(analytics_store().list_open_events, limit=2000)

    inbox, junk = [], []
    if client:
        try:
            inbox = client.search_emails(mailbox=client.inbox_mailbox, since_days=30, limit=8)
        except Exception:
            inbox = []
        try:
            junk = client.search_emails(mailbox=client.junk_mailbox, since_days=30, limit=8)
        except Exception:
            junk = []

    def status_badge(ok: bool, yes: str, no: str):
        return f'<span class="badge {"ok" if ok else "err"}">{escape(yes if ok else no)}</span>'

    domain_rows = ""
    for item in (domains.get("domains", []) if isinstance(domains, dict) else []):
        d = escape(str(item.get("domain", "")))
        note = escape(str(item.get("note", "")))
        domain_rows += f"""<tr><td class="mono">{d}</td><td>{note}</td><td class="actions">
<form method="post" action="/dashboard/domain/remove">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<input type="hidden" name="domain" value="{d}">
<button class="danger" type="submit">Remove</button></form></td></tr>"""

    recipient_rows = ""
    for item in (recipients.get("recipients", []) if isinstance(recipients, dict) else []):
        e = escape(str(item.get("email", "")))
        note = escape(str(item.get("note", "")))
        recipient_rows += f"""<tr><td class="mono">{e}</td><td>{note}</td><td class="actions">
<form method="post" action="/dashboard/recipient/remove">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<input type="hidden" name="email" value="{e}">
<button class="danger" type="submit">Remove</button></form></td></tr>"""

    account_rows = ""
    for a in accounts:
        aid = escape(str(a.get("id", "")))
        label = escape(str(a.get("label", "")))
        email = escape(str(a.get("email_address", "")))
        imap = f'{escape(str(a.get("imap_host","")))}:{escape(str(a.get("imap_port","")))} / {escape(str(a.get("imap_security","")))}'
        smtp = f'{escape(str(a.get("smtp_host","")))}:{escape(str(a.get("smtp_port","")))} / {escape(str(a.get("smtp_security","")))}'
        default_badge = '<span class="badge ok">default</span>' if a.get("is_default") else ""
        enabled_badge = '<span class="badge ok">enabled</span>' if a.get("enabled") else '<span class="badge err">disabled</span>'
        amp_badge = '<span class="badge ok">AMP enabled</span>' if a.get("amp_enabled") else '<span class="badge">AMP off</span>'
        tracking_badge = '<span class="badge warn">tracking default</span>' if a.get("tracking_default") else ""
        account_rows += f"""<tr>
<td><strong>{label or email}</strong><div class="small muted mono">{aid}</div></td>
<td>{email}<div class="small muted">{default_badge} {enabled_badge} {amp_badge} {tracking_badge}</div></td>
<td class="mono">{imap}</td><td class="mono">{smtp}</td>
<td class="actions">
<a href="/?edit_account={aid}#accounts"><button type="button">Edit</button></a>
<form method="post" action="/dashboard/account/test"><input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="account_id" value="{aid}"><button class="ok" type="submit">Test</button></form>
<form method="post" action="/dashboard/account/default"><input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="account_id" value="{aid}"><button type="submit">Default</button></form>
<form method="post" action="/dashboard/account/delete" onsubmit="return confirm('Delete this account configuration?');"><input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="account_id" value="{aid}"><button class="danger" type="submit">Delete</button></form>
</td></tr>"""

    job_rows = ""
    for j in (jobs if isinstance(jobs, list) else []):
        jid = escape(str(j.get("id", "")))
        status = escape(str(j.get("status", "")))
        title = escape(str(j.get("title", "")))
        owner = escape(str(j.get("owner_id", "")))
        project = escape(str(j.get("project_id", "")))
        action = escape(str(j.get("action_type", "")))
        nxt = escape(str(j.get("next_run_utc") or "—"))
        payload = j.get("payload") or {}
        acct_ref = escape(str(payload.get("account_id", ""))) if isinstance(payload, dict) else ""
        buttons = ""
        if status == "paused":
            buttons = f"""<form method="post" action="/dashboard/job/resume">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="job_id" value="{jid}">
<button class="ok" type="submit">Resume</button></form>"""
        elif status not in {"completed"}:
            buttons = f"""<form method="post" action="/dashboard/job/pause">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="job_id" value="{jid}">
<button type="submit">Pause</button></form>"""
        account_note = f'<div class="small muted">account ref: <span class="mono">{acct_ref}</span></div>' if acct_ref else ""
        job_rows += f"""<tr>
<td><strong>{title}</strong><div class="small muted mono">{jid}</div></td>
<td>{owner}<br><span class="muted">{project}</span>{account_note}</td>
<td>{action}</td><td><span class="badge">{status}</span></td><td class="mono">{nxt}</td>
<td class="actions">{buttons}</td></tr>"""

    def mail_rows(messages, mailbox, spam_action):
        rows = ""
        for m in messages:
            uid = escape(str(m.get("uid", "")))
            sender = escape(str(m.get("from", "")))
            subject = escape(str(m.get("subject", "")))
            date = escape(str(m.get("date", "")))
            action_label = "Mark spam" if spam_action == "spam" else "Not spam"
            action_path = "/dashboard/mail/spam" if spam_action == "spam" else "/dashboard/mail/not-spam"
            btn_class = "danger" if spam_action == "spam" else "ok"
            rows += f"""<tr><td>{sender}</td><td><strong>{subject}</strong><div class="small muted">{date}</div></td>
<td class="actions"><form method="post" action="{action_path}">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<input type="hidden" name="account_id" value="{escape(selected_id or '')}">
<input type="hidden" name="mailbox" value="{escape(mailbox)}"><input type="hidden" name="uid" value="{uid}">
<button class="{btn_class}" type="submit">{action_label}</button></form></td></tr>"""
        return rows

    edit_id = request.query_params.get("edit_account", "")
    edit = None
    if edit_id:
        try:
            edit = account_store().get_account(edit_id)
        except Exception:
            edit = None

    def val(key: str, default: str = "") -> str:
        return escape(str((edit or {}).get(key, default)))

    def security_options(current: str, kind: str) -> str:
        labels = [("ssl","SSL/TLS"),("starttls","STARTTLS"),("plain","Plain / no TLS")]
        return "".join(
            f'<option value="{v}"{" selected" if current==v else ""}>{label}</option>'
            for v,label in labels
        )

    account_form_title = "Edit account" if edit else "Add email account"
    account_id_value = val("id")
    disabled_id = " readonly" if edit else ""
    imap_sec = str((edit or {}).get("imap_security", "ssl"))
    smtp_sec = str((edit or {}).get("smtp_security", "ssl"))
    enabled_checked = " checked" if (edit is None or edit.get("enabled")) else ""
    default_checked = " checked" if (edit and edit.get("is_default")) else ""

    tracking_checked = " checked" if (edit and edit.get("tracking_default")) else ""

    account_form = f"""
<section class="card wide">
<div class="panel-title"><h2>{account_form_title}</h2><span class="small muted">Passwords are encrypted at rest in /data</span></div>
<form method="post" action="/dashboard/account/save">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<div class="row">
 <div class="field"><label>Account ID</label><input type="text" name="account_id" value="{account_id_value}" placeholder="work-mail"{disabled_id}></div>
 <div class="field"><label>Label</label><input type="text" name="label" value="{val('label')}" placeholder="Tinkerer"></div>
 <div class="field"><label>Email / From address</label><input type="text" name="email_address" value="{val('email_address')}" required></div>
</div>
<div class="form-section"><h3>IMAP</h3><div class="row">
 <div class="field"><label>Host</label><input type="text" name="imap_host" value="{val('imap_host','imap.hostinger.com')}" required></div>
 <div class="field"><label>Port</label><input type="number" name="imap_port" value="{val('imap_port','993')}" required></div>
 <div class="field"><label>Security</label><select name="imap_security">{security_options(imap_sec,'imap')}</select></div>
 <div class="field"><label>Username</label><input type="text" name="imap_username" value="{val('imap_username')}" placeholder="defaults to email"></div>
 <div class="field"><label>Password</label><input type="password" name="imap_password" placeholder="{'leave blank to keep saved password' if edit else 'required'}"></div>
</div></div>
<div class="form-section"><h3>SMTP</h3><div class="row">
 <div class="field"><label>Host</label><input type="text" name="smtp_host" value="{val('smtp_host','smtp.hostinger.com')}" required></div>
 <div class="field"><label>Port</label><input type="number" name="smtp_port" value="{val('smtp_port','465')}" required></div>
 <div class="field"><label>Security</label><select name="smtp_security">{security_options(smtp_sec,'smtp')}</select></div>
 <div class="field"><label>Username</label><input type="text" name="smtp_username" value="{val('smtp_username')}" placeholder="defaults to email"></div>
 <div class="field"><label>Password</label><input type="password" name="smtp_password" placeholder="{'leave blank to keep saved password' if edit else 'blank = IMAP password'}"></div>
</div></div>
<div class="form-section"><h3>Mailbox names</h3><div class="row">
 <div class="field"><label>Inbox</label><input type="text" name="inbox_mailbox" value="{val('inbox_mailbox','INBOX')}"></div>
 <div class="field"><label>Sent</label><input type="text" name="sent_mailbox" value="{val('sent_mailbox','INBOX.Sent')}"></div>
 <div class="field"><label>Drafts</label><input type="text" name="draft_mailbox" value="{val('draft_mailbox','INBOX.Drafts')}"></div>
 <div class="field"><label>Junk / Spam</label><input type="text" name="junk_mailbox" value="{val('junk_mailbox','INBOX.Junk')}"></div>
</div></div>
<div class="row" style="margin-top:14px">
 <label><input type="checkbox" name="enabled" value="1"{enabled_checked}> Enabled</label>
 <label><input type="checkbox" name="make_default" value="1"{default_checked}> Make default</label>
 <label><input type="checkbox" name="tracking_default" value="1"{tracking_checked}> Track opens by default (send + reply)</label>
 <button class="primary" type="submit">Save account</button>
 {'<a href="/#accounts" class="muted">Cancel edit</a>' if edit else ''}
</div>
</form></section>
"""

    amp_rows = ""
    for a in accounts:
        aid_raw = str(a.get("id", ""))
        aid = escape(aid_raw)
        label = escape(str(a.get("label") or a.get("email_address") or aid_raw))
        email = escape(str(a.get("email_address", "")))
        enabled_checked_amp = " checked" if a.get("amp_enabled") else ""
        tested_checked_amp = " checked" if a.get("amp_tested") else ""
        registered_checked_amp = " checked" if a.get("amp_registered") else ""
        notes = escape(str(a.get("amp_notes", "")))
        review = escape(str(a.get("amp_review_sent_at") or "—"))
        amp_rows += f"""<tr>
<td><strong>{label}</strong><div class="small muted mono">{email}</div><div class="small muted mono">{aid}</div></td>
<td>
  {'<span class="badge ok">MCP capability enabled</span>' if a.get("amp_enabled") else '<span class="badge">disabled</span>'}
  {'<span class="badge ok">tested</span>' if a.get("amp_tested") else ''}
  {'<span class="badge ok">Google registered</span>' if a.get("amp_registered") else ''}
  <div class="small muted">review email marked sent: {review}</div>
</td>
<td>
<form method="post" action="/dashboard/amp/state">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<input type="hidden" name="account_id" value="{aid}">
<div class="row">
<label><input type="checkbox" name="enabled" value="1"{enabled_checked_amp}> Enable AMP capability</label>
<label><input type="checkbox" name="tested" value="1"{tested_checked_amp}> Gmail dev-tested</label>
<label><input type="checkbox" name="registered" value="1"{registered_checked_amp}> Google registered</label>
<label><input type="checkbox" name="review_sent" value="1"> Mark review email sent now</label>
</div>
<div style="margin-top:7px"><input type="text" name="notes" value="{notes}" placeholder="AMP notes / registration status"></div>
<div style="margin-top:7px"><button class="primary" type="submit">Save AMP state</button></div>
</form>
</td></tr>"""

    campaign_rows = ""
    for c in (tracking_campaigns if isinstance(tracking_campaigns, list) else []):
        cid = escape(str(c.get("id", "")))
        aid = escape(str(c.get("account_id", "")))
        subj = escape(str(c.get("subject", "")))
        created = escape(str(c.get("created_at", "")))
        recipients_n = int(c.get("recipient_count") or 0)
        opened_n = int(c.get("opened_recipient_count") or 0)
        events_n = int(c.get("total_open_events") or 0)
        flags = []
        if c.get("track_opens"):
            flags.append('<span class="badge warn">tracking</span>')
        if c.get("amp_used"):
            flags.append('<span class="badge ok">AMP</span>')
        campaign_rows += f"""<tr>
<td><strong>{subj}</strong><div class="small muted mono">{cid}</div></td>
<td class="mono">{aid}</td>
<td>{' '.join(flags) or '<span class="badge">plain</span>'}</td>
<td><strong>{opened_n}/{recipients_n}</strong> recipients<div class="small muted">{events_n} observed open/image-load events</div></td>
<td class="small mono">{created}</td>
</tr>"""

    delivery_rows = ""
    for item in (tracking_deliveries if isinstance(tracking_deliveries, list) else []):
        did = escape(str(item.get("id", "")))
        cid = escape(str(item.get("campaign_id", "")))
        recipient = escape(str(item.get("recipient", "")))
        role = escape(str(item.get("recipient_role", "")))
        count = int(item.get("open_count") or 0)
        first_open = escape(str(item.get("first_open_at") or "—"))
        last_open = escape(str(item.get("last_open_at") or "—"))
        sent = escape(str(item.get("sent_at") or "—"))
        message_id = escape(str(item.get("message_id") or ""))
        delivery_rows += f"""<tr>
<td><strong>{recipient}</strong><div class="small muted">{role}</div></td>
<td class="mono">{cid}<div class="small muted mono">{did}</div></td>
<td><strong>{count}</strong><div class="small muted">first {first_open}<br>last {last_open}</div></td>
<td class="small mono">{sent}</td>
<td class="small mono">{message_id}</td>
</tr>"""

    open_rows = ""
    for item in (open_events if isinstance(open_events, list) else []):
        recipient = escape(str(item.get("recipient", "")))
        opened = escape(str(item.get("opened_at", "")))
        cid = escape(str(item.get("campaign_id", "")))
        did = escape(str(item.get("delivery_id", "")))
        event_type = escape(str(item.get("event_type", "pixel")))
        fp = escape(str(item.get("client_fingerprint", "")))
        ua = escape(str(item.get("user_agent", "")))
        country = escape(str(item.get("country_code", "") or "—"))
        browser = escape(str(item.get("browser", "") or "Unknown"))
        os_name = escape(str(item.get("os", "") or "Unknown"))
        source = escape(str(item.get("client_source", "") or "unknown"))
        confidence = escape(str(item.get("metadata_confidence", "") or "unknown"))
        open_rows += f"""<tr>
<td><strong>{recipient}</strong><div class="small muted">{event_type}</div></td>
<td class="mono">{opened}</td>
<td><strong>{country}</strong><div class="small muted">{source} · {confidence}</div></td>
<td><strong>{browser}</strong><div class="small muted">{os_name}</div></td>
<td class="mono">{cid}<div class="small muted">{did}</div></td>
<td class="mono">{fp}</td>
<td class="small">{ua}</td>
</tr>"""

    knowledge_memory_count = int(knowledge_stat.get("memories", 0)) if isinstance(knowledge_stat, dict) else 0
    knowledge_skill_count = int(knowledge_stat.get("skills", 0)) if isinstance(knowledge_stat, dict) else 0
    knowledge_total_count = knowledge_memory_count + knowledge_skill_count
    knowledge_sem = knowledge_stat.get("semantic", {}) if isinstance(knowledge_stat, dict) else {}
    knowledge_semantic_available = bool(knowledge_sem.get("available"))
    knowledge_missing_embeddings = int(knowledge_stat.get("missing_embeddings", 0)) if isinstance(knowledge_stat, dict) else 0

    knowledge_edit = None
    knowledge_edit_id = request.query_params.get("edit_knowledge", "").strip()
    if knowledge_edit_id:
        try:
            knowledge_edit = context_engine().store.get_item(knowledge_edit_id)
        except Exception:
            knowledge_edit = None

    def _knowledge_owner_options(selected_owner: str) -> str:
        rows = knowledge_owners if isinstance(knowledge_owners, list) else []
        return "".join(
            f'<option value="{escape(str(o.get("id","")))}"{" selected" if str(o.get("id","")) == selected_owner else ""}>{escape(str(o.get("display_name") or o.get("id") or ""))} — {escape(str(o.get("id","")))}</option>'
            for o in rows
        )

    def _knowledge_project_options(selected_project: str | None) -> str:
        rows = knowledge_projects if isinstance(knowledge_projects, list) else []
        out = '<option value="">Global / owner-wide</option>'
        for p in rows:
            pid = str(p.get("id", ""))
            owner = str(p.get("owner_id", ""))
            name = str(p.get("name") or pid)
            sel = " selected" if selected_project and pid == selected_project else ""
            out += f'<option value="{escape(pid)}"{sel}>{escape(owner)} / {escape(name)} ({escape(pid)})</option>'
        return out

    knowledge_rows = ""
    for item in (knowledge_items if isinstance(knowledge_items, list) else []):
        iid = escape(str(item.get("id", "")))
        kind = escape(str(item.get("kind", "")))
        title = escape(str(item.get("title", "")))
        owner = escape(str(item.get("owner_id", "")))
        project = escape(str(item.get("project_id") or "global"))
        tags = escape(", ".join(item.get("tags") or []))
        priority = float(item.get("priority") or 0.0)
        flags = []
        if item.get("always_include"):
            flags.append('<span class="badge warn">always</span>')
        if item.get("enabled"):
            flags.append('<span class="badge ok">enabled</span>')
        else:
            flags.append('<span class="badge">disabled</span>')
        knowledge_rows += f"""<tr>
<td><strong>{title}</strong><div class="small muted mono">{iid}</div><div class="small muted">{tags}</div></td>
<td><span class="badge">{kind}</span></td><td>{owner}<div class="small muted">{project}</div></td>
<td>{priority:.2f}<div>{' '.join(flags)}</div></td>
<td class="actions"><a href="/?edit_knowledge={iid}#knowledge"><button type="button">Edit</button></a>
<form method="post" action="/dashboard/knowledge/delete" onsubmit="return confirm('Delete this memory/skill? Revision history for the deleted item will no longer be available through the item, while the audit event is retained.');">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="item_id" value="{iid}"><button class="danger" type="submit">Delete</button></form></td></tr>"""

    knowledge_search_rows = ""
    if knowledge_query:
        for item in (knowledge_search.get("results", []) if isinstance(knowledge_search, dict) else []):
            iid = escape(str(item.get("item_id") or item.get("id") or ""))
            title = escape(str(item.get("title", "")))
            kind = escape(str(item.get("kind", "")))
            score = float(item.get("score") or 0.0)
            chunk = escape(str(item.get("best_chunk") or item.get("content") or "")[:500])
            knowledge_search_rows += f'<tr><td><strong>{title}</strong><div class="small muted mono">{iid}</div></td><td><span class="badge">{kind}</span></td><td>{score:.5f}</td><td class="small">{chunk}</td></tr>'

    ked = knowledge_edit or {}
    knowledge_form_title = "Edit knowledge item" if knowledge_edit else "Add memory / skill"
    knowledge_owner_selected = str(ked.get("owner_id") or os.getenv("DEFAULT_OWNER_ID", ""))
    knowledge_project_selected = str(ked.get("project_id") or "") or None
    knowledge_kind_selected = str(ked.get("kind") or "memory")
    knowledge_tags_value = escape(", ".join(ked.get("tags") or []))
    knowledge_content_value = escape(str(ked.get("content") or ""))
    knowledge_title_value = escape(str(ked.get("title") or ""))
    knowledge_priority_value = escape(str(ked.get("priority", 0.5)))
    knowledge_always_checked = " checked" if ked.get("always_include") else ""
    knowledge_enabled_checked = " checked" if (not knowledge_edit or ked.get("enabled")) else ""
    knowledge_id_value = escape(str(ked.get("id") or ""))
    knowledge_kind_control = (
        f'<input type="hidden" name="kind" value="{escape(knowledge_kind_selected)}"><div class="mono">{escape(knowledge_kind_selected)}</div>'
        if knowledge_edit else
        '<select name="kind"><option value="memory">Memory</option><option value="skill">Skill</option></select>'
    )

    due_count = len(due) if isinstance(due, list) else 0
    job_count = len(jobs) if isinstance(jobs, list) else 0
    domain_count = domains.get("count", 0) if isinstance(domains, dict) else 0
    recipient_count = recipients.get("count", 0) if isinstance(recipients, dict) else 0
    account_count = len(accounts)
    amp_enabled_count = sum(1 for a in accounts if a.get("amp_enabled"))
    campaign_count = len(tracking_campaigns) if isinstance(tracking_campaigns, list) else 0
    tracked_delivery_count = len(tracking_deliveries) if isinstance(tracking_deliveries, list) else 0
    open_event_count = int(tracking_stat.get("open_events", 0)) if isinstance(tracking_stat, dict) else 0
    total_campaign_count = int(tracking_stat.get("campaigns", 0)) if isinstance(tracking_stat, dict) else 0
    total_delivery_count = int(tracking_stat.get("deliveries", 0)) if isinstance(tracking_stat, dict) else 0

    if selected:
        selected_label = (
            str(selected.get("label") or "").strip()
            or str(selected.get("email_address") or "").strip()
            or str(selected.get("id") or "").strip()
            or "Selected account"
        )
    else:
        selected_label = "No email account configured"

    mail_error = ""
    if isinstance(mail_stat, dict) and not mail_stat.get("ok"):
        mail_error = escape(str(mail_stat.get("error") or "Mail unavailable"))

    if accounts:
        account_picker = f"""
<form class="account-picker" method="get" action="/">
<label class="small muted">Mailbox shown in Overview</label>
<div class="row">
  <div class="grow"><select name="account">{_account_options(accounts, selected_id)}</select></div>
  <button type="submit">Switch</button>
</div>
</form>"""
    else:
        account_picker = """
<div class="small muted">
  No email accounts configured yet. Open the <strong>Accounts</strong> tab to add one.
</div>
"""

    body = f"""
<h1>Postmaster MCP</h1>
<p class="sub">Persistent Context + multi-account IMAP/SMTP + analytics + task registry · v9.0</p>

<nav class="tabs" aria-label="Dashboard sections">
  <a class="tab-link" href="#overview" data-tab="overview">Overview</a>
  <a class="tab-link" href="#accounts" data-tab="accounts">Accounts <span class="tab-count">{account_count}</span></a>
  <a class="tab-link" href="#amp" data-tab="amp">AMP <span class="tab-count">{amp_enabled_count}</span></a>
  <a class="tab-link" href="#tracking" data-tab="tracking">Tracking <span class="tab-count">{open_event_count}</span></a>
  <a class="tab-link" href="#domains" data-tab="domains">Domains <span class="tab-count">{domain_count}</span></a>
  <a class="tab-link" href="#recipients" data-tab="recipients">Recipients <span class="tab-count">{recipient_count}</span></a>
  <a class="tab-link" href="#knowledge" data-tab="knowledge">Knowledge <span class="tab-count">{knowledge_total_count}</span></a>
  <a class="tab-link" href="#scheduler" data-tab="scheduler">Tasks <span class="tab-count">{job_count}</span></a>
</nav>

<section class="tab-panel" id="panel-overview" data-panel="overview">
{account_picker}
<div class="grid">
<section class="card">
<h2>System</h2>
<div class="row">
{status_badge(bool(mail_stat.get("ok")) if isinstance(mail_stat,dict) else False, "Mail online", "Mail unavailable")}
<span class="badge ok">Task registry only</span>
</div>
<hr>
<div><strong>{selected_label}</strong></div>
<div class="small muted">Selected account</div>
<div class="small muted mono" style="margin-top:8px">build: {escape(os.getenv("BRIDGE_BUILD","unknown"))}</div>
<div style="margin-top:10px"><strong>{job_count}</strong> tasks · <strong>{due_count}</strong> due</div>
<div><strong>{account_count}</strong> mail accounts · <strong>{amp_enabled_count}</strong> AMP-enabled</div>
<div><strong>{campaign_count}</strong> recent campaigns · <strong>{open_event_count}</strong> total observed open events</div>
<div><strong>{domain_count}</strong> domains · <strong>{recipient_count}</strong> exact recipients</div>
<div><strong>{knowledge_memory_count}</strong> memories · <strong>{knowledge_skill_count}</strong> skills</div>
{f'<div class="small" style="color:var(--danger);margin-top:8px">{mail_error}</div>' if mail_error else ''}
</section>

<section class="card">
<h2>Capabilities</h2>
<div class="row">
<span class="badge ok">HTML</span>
<span class="badge ok">Attachments</span>
<span class="badge ok">Multi-account</span>
<span class="badge warn">Open tracking opt-in</span>
<span class="badge ok">FTS5 context</span>
{('<span class="badge ok">Model2Vec</span>' if knowledge_semantic_available else '<span class="badge">Model2Vec fallback</span>')}
{('<span class="badge ok">AMP enabled here</span>' if (selected or {}).get("amp_enabled") else '<span class="badge">AMP disabled here</span>')}
</div>
<p class="small muted">AMP is a per-account capability. Open tracking is per send unless enabled as that account's default.</p>
</section>

<section class="card wide">
<div class="panel-title"><h2>Recent Inbox</h2><span class="small muted">Selected account · last 30 days · up to 8</span></div>
<div class="scroll"><table><thead><tr><th>From</th><th>Message</th><th></th></tr></thead>
<tbody>{mail_rows(inbox, client.inbox_mailbox if client else "INBOX", "spam") if client else '<tr><td colspan="3" class="muted">Configure an account first</td></tr>'}</tbody></table></div>
</section>

<section class="card wide">
<div class="panel-title"><h2>Recent Junk / Spam</h2><span class="small muted">Selected account · last 30 days · up to 8</span></div>
<div class="scroll"><table><thead><tr><th>From</th><th>Message</th><th></th></tr></thead>
<tbody>{mail_rows(junk, client.junk_mailbox if client else "Junk", "notspam") if client else '<tr><td colspan="3" class="muted">Configure an account first</td></tr>'}</tbody></table></div>
</section>
</div>
</section>

<section class="tab-panel" id="panel-accounts" data-panel="accounts">
<div class="grid">
{account_form}
<section class="card wide">
<div class="panel-title"><h2>Configured accounts</h2><span class="badge">{account_count} total</span></div>
<p class="small muted">The MCP uses the default account whenever account_id is omitted. Password values are encrypted at rest and never displayed or returned by MCP tools. AMP remains disabled unless explicitly enabled for that sender.</p>
<div class="scroll"><table><thead><tr><th>Account</th><th>Address / capabilities</th><th>IMAP</th><th>SMTP</th><th></th></tr></thead>
<tbody>{account_rows or '<tr><td colspan="5" class="muted">No accounts configured</td></tr>'}</tbody></table></div>
</section>
</div>
</section>

<section class="tab-panel" id="panel-amp" data-panel="amp">
<div class="grid">
<section class="card wide">
<div class="panel-title"><h2>AMP for Email — per sender account</h2><span class="badge">{amp_enabled_count} enabled</span></div>
<p><strong>Optional capability.</strong> Enabling AMP here only makes <code>body_amp</code> usable for that account. Normal plain/HTML email continues to work exactly as before. An AMP body supplied for a disabled account is rejected.</p>
<div class="scroll"><table><thead><tr><th>Sender</th><th>Status</th><th>Controls</th></tr></thead>
<tbody>{amp_rows or '<tr><td colspan="3" class="muted">No accounts configured</td></tr>'}</tbody></table></div>
</section>

<section class="card">
<h2>Google registration procedure</h2>
<ol class="small">
<li><strong>Enable AMP</strong> only for the exact sender you want to test.</li>
<li>On the destination Gmail test account open <strong>Settings → General → Dynamic email → Developer settings</strong> and allow the exact sender address.</li>
<li>Send a production-system email containing <code>text/plain</code>, then <code>text/x-amp-html</code>, then <code>text/html</code> fallback. The v8.2 sender creates this MIME order automatically when <code>body_amp</code> is supplied.</li>
<li>Verify that SPF passes, DKIM passes and is aligned with the From domain; DMARC is recommended. AMP delivery must use TLS.</li>
<li>Validate the delivered AMP email in Gmail. The local MCP preflight is intentionally not treated as Google's validator.</li>
<li>When production-ready, send a <strong>real professional AMP email</strong> directly from the exact sender to <code>ampforemail.whitelisting@gmail.com</code>. Do not forward it and do not send an empty “test” message.</li>
<li>Submit Google's AMP sender registration form. Registration is <strong>per sender email address</strong>, even when multiple addresses share the same domain.</li>
</ol>
<p class="small muted">
Official docs:
<a href="https://developers.google.com/workspace/gmail/ampemail/testing-dynamic-email" target="_blank" rel="noreferrer">testing</a> ·
<a href="https://developers.google.com/workspace/gmail/ampemail/security-requirements" target="_blank" rel="noreferrer">security</a> ·
<a href="https://developers.google.com/workspace/gmail/ampemail/register" target="_blank" rel="noreferrer">registration</a>.
</p>
</section>

<section class="card">
<h2>Dynamic endpoint</h2>
<p class="small">AMP templates can use <code>{{{{AMP_STATUS_URL}}}}</code>. v8.3 replaces it with a cryptographically random, recipient-scoped URL valid for 31 days. Gmail XHR requests are authenticated without cookies and the endpoint implements both AMP Email CORS variants.</p>
<div class="mono small">{escape(os.getenv("PUBLIC_EMAIL_BASE_URL","") or ("https://" + os.getenv("PUBLIC_MCP_HOST","")) + "/api/amp/status")}</div>
<hr>
<p class="small"><strong>Cloudflare Access:</strong> Gmail cannot pass your dashboard OTP. The paths <code>/api/amp/*</code> must be reachable publicly (normally via a narrowly scoped Access bypass rule). Tokens remain unguessable and scoped to one delivery.</p>
</section>
</div>
</section>

<section class="tab-panel" id="panel-tracking" data-panel="tracking">
<div class="grid">
<section class="card">
<h2>Tracking status</h2>
<div><strong>{total_campaign_count}</strong> campaigns stored · <span class="muted">{campaign_count} shown</span></div>
<div><strong>{total_delivery_count}</strong> individualized deliveries stored · <span class="muted">{tracked_delivery_count} shown</span></div>
<div><strong>{open_event_count}</strong> observed open/image-load events stored</div>
<p class="small muted">Tracking is opt-in. For tracked sends and replies, v8.7 uses one SMTP envelope per recipient with a distinct token while preserving the original visible To/Cc headers on every copy. The account tracking default applies to both send_email and reply_email unless explicitly overridden.</p>
</section>

<section class="card">
<h2>Accuracy / privacy model</h2>
<p class="small">An “open” is an external image request. It is useful telemetry, but not proof that a human read the message. Gmail/other image proxies, security scanners, prefetching and image blocking can create or suppress events.</p>
<p class="small">The tracker stores recipient, timestamp, country code observed at Cloudflare, parsed browser/OS, user-agent, source/confidence and an HMAC client fingerprint. It does <strong>not</strong> store the raw client IP address. Proxy-derived country/browser/OS may describe the mail provider rather than the reader.</p>
<p class="small"><strong>Cloudflare Access:</strong> <code>/track/open/*</code> must be publicly fetchable or mail clients cannot load the pixel.</p>
</section>

<section class="card wide">
<div class="panel-title"><h2>Campaigns</h2><span class="small muted">Grouped logical sends</span></div>
<div class="scroll"><table><thead><tr><th>Campaign</th><th>Account</th><th>Mode</th><th>Recipients / opens</th><th>Created UTC</th></tr></thead>
<tbody>{campaign_rows or '<tr><td colspan="5" class="muted">No tracked or AMP campaigns yet</td></tr>'}</tbody></table></div>
</section>

<section class="card wide">
<div class="panel-title"><h2>Per-recipient deliveries</h2><span class="small muted">Tokens are never displayed</span></div>
<div class="scroll"><table><thead><tr><th>Recipient</th><th>Campaign / delivery</th><th>Observed opens</th><th>Sent UTC</th><th>Message-ID</th></tr></thead>
<tbody>{delivery_rows or '<tr><td colspan="5" class="muted">No individualized deliveries yet</td></tr>'}</tbody></table></div>
</section>

<section class="card wide">
<div class="panel-title"><h2>Open-event history</h2><span class="small muted">Repeat loads are deliberately retained separately</span></div>
<div class="scroll"><table><thead><tr><th>Recipient</th><th>Observed UTC</th><th>Country / source</th><th>Browser / OS</th><th>Campaign / delivery</th><th>Client fingerprint</th><th>User-Agent</th></tr></thead>
<tbody>{open_rows or '<tr><td colspan="7" class="muted">No open events yet</td></tr>'}</tbody></table></div>
</section>
</div>
</section>

<section class="tab-panel" id="panel-domains" data-panel="domains">
<div class="grid"><section class="card wide">
<div class="panel-title"><h2>Authorized domains</h2><span class="badge">{domain_count} total</span></div>
<form method="post" action="/dashboard/domain/add"><input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<div class="row"><div class="grow"><input type="text" name="domain" placeholder="example.com" required></div>
<div class="grow"><input type="text" name="note" placeholder="Optional note"></div><button class="primary" type="submit">Add domain</button></div></form>
<p class="small muted">Global safety policy: applies to every configured sender account.</p><hr>
<div class="scroll"><table><thead><tr><th>Domain</th><th>Note</th><th></th></tr></thead>
<tbody>{domain_rows or '<tr><td colspan="3" class="muted">No domains</td></tr>'}</tbody></table></div>
</section></div>
</section>

<section class="tab-panel" id="panel-recipients" data-panel="recipients">
<div class="grid"><section class="card wide">
<div class="panel-title"><h2>Exact authorized recipients</h2><span class="badge">{recipient_count} total</span></div>
<form method="post" action="/dashboard/recipient/add"><input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<div class="row"><div class="grow"><input type="text" name="email" placeholder="person@example.com" required></div>
<div class="grow"><input type="text" name="note" placeholder="Optional note"></div><button class="primary" type="submit">Add recipient</button></div></form>
<p class="small muted">Global safety policy. Historical Sent authorization remains account-specific.</p><hr>
<div class="scroll"><table><thead><tr><th>Email</th><th>Note</th><th></th></tr></thead>
<tbody>{recipient_rows or '<tr><td colspan="3" class="muted">No exact recipients</td></tr>'}</tbody></table></div>
</section></div>
</section>

<section class="tab-panel" id="panel-knowledge" data-panel="knowledge">
<div class="grid">
<section class="card">
<h2>Context engine</h2>
<div><strong>{knowledge_memory_count}</strong> memories · <strong>{knowledge_skill_count}</strong> skills</div>
<div><strong>{int(knowledge_stat.get('chunks',0)) if isinstance(knowledge_stat,dict) else 0}</strong> chunks · <strong>{int(knowledge_stat.get('embedded_chunks',0)) if isinstance(knowledge_stat,dict) else 0}</strong> embedded</div>
<div class="row" style="margin-top:10px">
<span class="badge ok">FTS5</span>
{('<span class="badge ok">Model2Vec ready</span>' if knowledge_semantic_available else '<span class="badge warn">Model2Vec unavailable / FTS fallback</span>')}
</div>
<p class="small muted">Semantic search is optional. The server remains fully usable with lexical FTS5 search if the model package, model file or download is unavailable.</p>
<div class="small muted mono">{escape(str(knowledge_sem.get('last_error') or ''))}</div>
<form method="post" action="/dashboard/knowledge/reindex" style="margin-top:12px">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><button type="submit">Reindex embeddings ({knowledge_missing_embeddings} missing)</button></form>
</section>

<section class="card wide">
<div class="panel-title"><h2>{knowledge_form_title}</h2><span class="small muted">Uses the same owner/project IDs as Tasks</span></div>
<form method="post" action="/dashboard/knowledge/save">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="item_id" value="{knowledge_id_value}">
<div class="row">
 <div class="field"><label>Kind</label>{knowledge_kind_control}</div>
 <div class="field"><label>Owner</label><select name="owner_id" required>{_knowledge_owner_options(knowledge_owner_selected)}</select></div>
 <div class="field"><label>Project</label><select name="project_id">{_knowledge_project_options(knowledge_project_selected)}</select></div>
 <div class="field"><label>Priority 0–1</label><input type="number" name="priority" min="0" max="1" step="0.05" value="{knowledge_priority_value}"></div>
</div>
<div class="row" style="margin-top:10px"><div class="field grow"><label>Title</label><input type="text" name="title" value="{knowledge_title_value}" required></div>
<div class="field grow"><label>Tags (comma separated)</label><input type="text" name="tags" value="{knowledge_tags_value}" placeholder="linux, cluster, workflow"></div></div>
<div class="field" style="margin-top:10px"><label>Content</label><textarea name="content" rows="12" required>{knowledge_content_value}</textarea></div>
<div class="row" style="margin-top:10px">
 <label><input type="checkbox" name="always_include" value="1"{knowledge_always_checked}> Always include in project context</label>
 <label><input type="checkbox" name="enabled" value="1"{knowledge_enabled_checked}> Enabled</label>
 <button class="primary" type="submit">{'Save changes' if knowledge_edit else 'Create item'}</button>
 {('<a href="/#knowledge" class="muted">Cancel edit</a>' if knowledge_edit else '')}
</div>
</form>
</section>

<section class="card wide">
<div class="panel-title"><h2>Search knowledge</h2><span class="small muted">hybrid when Model2Vec is available</span></div>
<form method="get" action="/">
<div class="row"><div class="grow"><input type="text" name="knowledge_q" value="{escape(knowledge_query)}" placeholder="How did we decide to build the cluster?"></div><button class="primary" type="submit">Search</button><a href="/#knowledge"><button type="button">Clear</button></a></div>
</form>
{('<div class="scroll" style="margin-top:12px"><table><thead><tr><th>Item</th><th>Kind</th><th>Score</th><th>Best chunk</th></tr></thead><tbody>' + (knowledge_search_rows or '<tr><td colspan="4" class="muted">No matches</td></tr>') + '</tbody></table></div>' if knowledge_query else '')}
</section>

<section class="card wide">
<div class="panel-title"><h2>Memories + Skills</h2><span class="badge">{knowledge_total_count} total</span></div>
<div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Owner / project</th><th>Priority</th><th></th></tr></thead>
<tbody>{knowledge_rows or '<tr><td colspan="5" class="muted">No persistent context yet</td></tr>'}</tbody></table></div>
</section>
</div>
</section>

<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">
<div class="grid"><section class="card wide">
<div class="panel-title"><h2>Task registry</h2><span class="badge">{job_count} total · {due_count} due</span></div>
<p class="small muted"><strong>No cron worker runs here.</strong> Dates and recurrence are stored only so an AI or user can query what is due. Tasks never send email or execute actions by themselves. A task payload may optionally contain an account_id as a descriptive reference.</p>
<div class="scroll"><table><thead><tr><th>Task</th><th>Owner / project</th><th>Type</th><th>Status</th><th>Due / next UTC</th><th></th></tr></thead>
<tbody>{job_rows or '<tr><td colspan="6" class="muted">No tasks registered</td></tr>'}</tbody></table></div>
</section></div>
</section>

<script>
(() => {{
 const allowed = new Set(['overview','accounts','amp','tracking','domains','recipients','knowledge','scheduler']);
 function activate() {{
   const raw = (window.location.hash || '#overview').slice(1);
   const tab = allowed.has(raw) ? raw : 'overview';
   document.querySelectorAll('[data-tab]').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
   document.querySelectorAll('[data-panel]').forEach(el => el.classList.toggle('active', el.dataset.panel === tab));
 }}
 window.addEventListener('hashchange', activate); activate();
}})();
</script>
"""
    return _layout("Postmaster MCP v9.0", body, flash=flash)


async def dashboard_knowledge_save(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    try:
        item_id = str(form.get("item_id", "")).strip()
        kind = str(form.get("kind", "memory")).strip().lower()
        owner_id = str(form.get("owner_id", "")).strip()
        project_id = str(form.get("project_id", "")).strip() or None
        _require_knowledge_scope(owner_id, project_id)
        kwargs = dict(
            title=str(form.get("title", "")), content=str(form.get("content", "")),
            priority=float(str(form.get("priority", "0.5"))),
            always_include=str(form.get("always_include", "")) == "1",
            enabled=str(form.get("enabled", "")) == "1",
            tags=str(form.get("tags", "")), actor="webgui",
        )
        if item_id:
            existing = context_engine().store.get_item(item_id)
            if existing.get("kind") != kind:
                raise KnowledgeError("Changing memory/skill kind in-place is not supported")
            context_engine().update(
                item_id, owner_id=owner_id, project_id=project_id, set_project=True, **kwargs,
            )
            return _redir("Knowledge item updated", "knowledge")
        context_engine().create(kind=kind, owner_id=owner_id, project_id=project_id, **kwargs)
        return _redir("Knowledge item created", "knowledge")
    except Exception as exc:
        logger.exception("Knowledge save failed")
        return _redir(f"{type(exc).__name__}: {exc}", "knowledge")


async def dashboard_knowledge_delete(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    result = _safe_call(context_engine().delete, str(form.get("item_id", "")), actor="webgui")
    return _redir("Knowledge item deleted" if result.get("ok") else result.get("error", "Failed"), "knowledge")


async def dashboard_knowledge_reindex(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    result = _safe_call(context_engine().reindex, force=False, limit=100000)
    if result.get("ok"):
        return _redir(f"Embeddings indexed: {result.get('indexed', 0)}", "knowledge")
    return _redir(result.get("error", "Semantic model unavailable"), "knowledge")



async def dashboard_account_save(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    try:
        result = account_store().save_account(
            account_id=str(form.get("account_id","")),
            label=str(form.get("label","")),
            email_address=str(form.get("email_address","")),
            imap_host=str(form.get("imap_host","")),
            imap_port=int(str(form.get("imap_port","993"))),
            imap_security=str(form.get("imap_security","ssl")),
            imap_username=str(form.get("imap_username","")),
            imap_password=str(form.get("imap_password","")),
            smtp_host=str(form.get("smtp_host","")),
            smtp_port=int(str(form.get("smtp_port","465"))),
            smtp_security=str(form.get("smtp_security","ssl")),
            smtp_username=str(form.get("smtp_username","")),
            smtp_password=str(form.get("smtp_password","")),
            sent_mailbox=str(form.get("sent_mailbox","Sent")),
            draft_mailbox=str(form.get("draft_mailbox","Drafts")),
            inbox_mailbox=str(form.get("inbox_mailbox","INBOX")),
            junk_mailbox=str(form.get("junk_mailbox","Junk")),
            enabled=str(form.get("enabled","")) == "1",
            make_default=str(form.get("make_default","")) == "1",
            tracking_default=str(form.get("tracking_default","")) == "1",
        )
        return _redir("Account saved", "accounts", str(result.get("id","")))
    except Exception as exc:
        logger.exception("Account save failed")
        return _redir(f"{type(exc).__name__}: {exc}", "accounts")


async def dashboard_account_test(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    aid = str(form.get("account_id",""))
    result = _safe_call(mail_client(aid).test_connections)
    msg = "IMAP + SMTP authentication successful" if result.get("ok") and result.get("smtp_login") else result.get("error","Connection test failed")
    return _redir(msg, "accounts", aid)


async def dashboard_account_default(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    aid = str(form.get("account_id",""))
    result = _safe_call(account_store().set_default, aid)
    return _redir("Default account updated" if result.get("id") else result.get("error","Failed"), "accounts", aid)


async def dashboard_account_delete(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    aid = str(form.get("account_id",""))
    result = _safe_call(account_store().delete_account, aid)
    return _redir("Account deleted" if result.get("ok") else result.get("error","Failed"), "accounts")


async def dashboard_amp_state(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    aid = str(form.get("account_id", ""))
    result = _safe_call(
        account_store().set_amp_state,
        aid,
        enabled=str(form.get("enabled", "")) == "1",
        tested=str(form.get("tested", "")) == "1",
        registered=str(form.get("registered", "")) == "1",
        review_sent=str(form.get("review_sent", "")) == "1",
        notes=str(form.get("notes", "")),
    )
    msg = "AMP state updated" if result.get("id") else result.get("error", "Failed")
    return _redir(msg, "amp", aid)


async def dashboard_domain_add(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(policy_client().authorize_domain, str(form.get("domain","")), str(form.get("note","")))
    return _redir("Domain added" if result.get("ok") else result.get("error","Failed"), "domains")


async def dashboard_domain_remove(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(policy_client().revoke_domain, str(form.get("domain","")))
    return _redir("Domain removed" if result.get("ok") else result.get("error","Failed"), "domains")


async def dashboard_recipient_add(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(policy_client().authorize_recipient, str(form.get("email","")), str(form.get("note","")))
    return _redir("Recipient added" if result.get("ok") else result.get("error","Failed"), "recipients")


async def dashboard_recipient_remove(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(policy_client().revoke_recipient, str(form.get("email","")))
    return _redir("Recipient removed" if result.get("ok") else result.get("error","Failed"), "recipients")


async def dashboard_job_pause(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(scheduler().pause_job, str(form.get("job_id","")))
    return _redir("Task paused" if result.get("id") or result.get("ok") else result.get("error","Failed"), "scheduler")


async def dashboard_job_resume(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    result = _safe_call(scheduler().resume_job, str(form.get("job_id","")))
    return _redir("Task resumed" if result.get("id") or result.get("ok") else result.get("error","Failed"), "scheduler")


async def dashboard_mail_spam(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    aid = str(form.get("account_id","")) or None
    result = _safe_call(mail_client(aid).mark_as_spam, str(form.get("mailbox","")), str(form.get("uid","")))
    return _redir("Message moved to Spam" if result.get("ok") else result.get("error","Failed"), "overview", aid)


async def dashboard_mail_not_spam(request: Request):
    form, error = await _verified_form(request)
    if error: return error
    aid = str(form.get("account_id","")) or None
    result = _safe_call(mail_client(aid).mark_not_spam, str(form.get("mailbox","")), str(form.get("uid","")))
    return _redir("Message restored to Inbox" if result.get("ok") else result.get("error","Failed"), "overview", aid)


async def tracking_open_pixel(request: Request):
    token = str(request.path_params.get("token", ""))
    try:
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = (forwarded.split(",", 1)[0].strip() if forwarded else "")
        if not client_ip and request.client:
            client_ip = request.client.host or ""
        analytics_store().record_open(
            token,
            user_agent=request.headers.get("user-agent", ""),
            client_ip=client_ip,
            country_code=request.headers.get("cf-ipcountry", ""),
        )
    except Exception:
        # Never leak token validity to image clients and never break the pixel response.
        logger.info("Tracking pixel load could not be recorded", exc_info=True)
    return Response(
        TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "private, no-store, no-cache, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _amp_cors_headers(request: Request, expected_sender: str) -> dict[str, str]:
    expected = expected_sender.strip().lower()
    sender_v2 = (request.headers.get("amp-email-sender") or "").strip().lower()
    if sender_v2:
        if sender_v2 != expected:
            raise AnalyticsError("AMP sender header does not match the sender account")
        return {
            "AMP-Email-Allow-Sender": expected_sender,
            "Cache-Control": "private, no-store, max-age=0",
        }

    origin = (request.headers.get("origin") or "").strip()
    source = (request.query_params.get("__amp_source_origin") or "").strip().lower()
    if origin and source:
        if source != expected:
            raise AnalyticsError("AMP source origin does not match the sender account")
        return {
            "Access-Control-Allow-Origin": origin,
            "AMP-Access-Control-Allow-Source-Origin": expected_sender,
            "Access-Control-Expose-Headers": "AMP-Access-Control-Allow-Source-Origin",
            "Cache-Control": "private, no-store, max-age=0",
            "Vary": "Origin",
        }

    raise AnalyticsError("Missing AMP Email CORS sender/origin headers")


async def amp_live_status(request: Request):
    try:
        token = (request.query_params.get("token") or "").strip()
        if not token:
            raise AnalyticsError("Missing AMP access token")
        delivery = analytics_store().get_delivery_by_amp_token(token)
        account = account_store().get_account(str(delivery["account_id"]))
        if not account.get("amp_enabled"):
            raise AnalyticsError("AMP is disabled for this sender account")

        headers = _amp_cors_headers(request, str(account["email_address"]))
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = (forwarded.split(",", 1)[0].strip() if forwarded else "")
        if not client_ip and request.client:
            client_ip = request.client.host or ""
        analytics_store().record_amp_view(
            token,
            user_agent=request.headers.get("user-agent", ""),
            client_ip=client_ip,
            country_code=request.headers.get("cf-ipcountry", ""),
        )
        mail_stat = _safe_call(mail_client(str(delivery["account_id"])).ping)
        sched = _safe_call(scheduler().status)
        campaigns = analytics_store().list_campaigns(
            account_id=str(delivery["account_id"]),
            limit=500,
        )
        payload = {
            "items": [{
                "generated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "status": "online" if bool(mail_stat.get("ok")) else "degraded",
                "mail_online": bool(mail_stat.get("ok")),
                "account_id": str(delivery["account_id"]),
                "sender": str(account["email_address"]),
                "recipient": str(delivery["recipient"]),
                "amp_registered": bool(account.get("amp_registered")),
                "task_registry_only": True,
                "scheduled_tasks": int((sched.get("job_counts") or {}).get("scheduled", 0))
                    if isinstance(sched, dict) else 0,
                "campaigns": len(campaigns),
            }]
        }
        return JSONResponse(payload, headers=headers)
    except (AnalyticsError, AccountStoreError, MailBridgeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    except Exception as exc:
        logger.exception("AMP live status failed")
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


_public_host = os.getenv("PUBLIC_MCP_HOST", "").strip()
_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
if _public_host:
    _allowed_hosts.extend([_public_host, f"{_public_host}:*"])
    _allowed_origins.extend([f"https://{_public_host}", f"https://{_public_host}:*"])

_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed_hosts,
    allowed_origins=_allowed_origins,
)

_mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security,
)


@contextlib.asynccontextmanager
async def app_lifespan(app: Starlette):
    # Initialize persistent stores but deliberately do not start any scheduler worker.
    account_store()
    scheduler()
    analytics_store()
    ctx = context_engine()
    try:
        ctx.warmup()
    except Exception:
        logger.exception("Context semantic warmup failed; FTS5 remains available")
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/", dashboard_home, methods=["GET"]),
        Route("/dashboard/account/save", dashboard_account_save, methods=["POST"]),
        Route("/dashboard/account/test", dashboard_account_test, methods=["POST"]),
        Route("/dashboard/account/default", dashboard_account_default, methods=["POST"]),
        Route("/dashboard/account/delete", dashboard_account_delete, methods=["POST"]),
        Route("/dashboard/amp/state", dashboard_amp_state, methods=["POST"]),
        Route("/dashboard/domain/add", dashboard_domain_add, methods=["POST"]),
        Route("/dashboard/domain/remove", dashboard_domain_remove, methods=["POST"]),
        Route("/dashboard/recipient/add", dashboard_recipient_add, methods=["POST"]),
        Route("/dashboard/recipient/remove", dashboard_recipient_remove, methods=["POST"]),
        Route("/dashboard/knowledge/save", dashboard_knowledge_save, methods=["POST"]),
        Route("/dashboard/knowledge/delete", dashboard_knowledge_delete, methods=["POST"]),
        Route("/dashboard/knowledge/reindex", dashboard_knowledge_reindex, methods=["POST"]),
        Route("/dashboard/job/pause", dashboard_job_pause, methods=["POST"]),
        Route("/dashboard/job/resume", dashboard_job_resume, methods=["POST"]),
        Route("/dashboard/mail/spam", dashboard_mail_spam, methods=["POST"]),
        Route("/dashboard/mail/not-spam", dashboard_mail_not_spam, methods=["POST"]),
        Route("/track/open/{token}.gif", tracking_open_pixel, methods=["GET"]),
        Route("/api/amp/status", amp_live_status, methods=["GET"]),
        Mount("/", app=_mcp_app),
    ],
    lifespan=app_lifespan,
)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
