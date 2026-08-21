from __future__ import annotations

import contextlib
import contextlib as _contextlib
import imaplib
import json
import os
import ssl
from functools import lru_cache
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from .delivery_reliability import ReliabilityStore
from .imap_idle import IMAPIdleManager, IMAPIdleWatcher, IdleSettings
from .mail_v950 import PostmasterV950MailClient
from .stored_file_delivery import stored_file_link_store


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def reliability_store() -> ReliabilityStore:
    return ReliabilityStore()


@lru_cache(maxsize=1)
def idle_manager() -> IMAPIdleManager:
    return IMAPIdleManager(max_accounts=max(1, int(os.getenv("IMAP_IDLE_MAX_ACCOUNTS", "20"))))


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _standards_panel(base: Any) -> str:
    suppressions = reliability_store().list_suppressions(active_only=True, limit=100)
    rows = "".join(
        "<tr>"
        f"<td>{escape(str(row.get('recipient') or ''))}</td>"
        f"<td>{escape(str(row.get('reason') or ''))}</td>"
        f"<td>{escape(str(row.get('source') or ''))}</td>"
        f"<td>{escape(str(row.get('updated_at') or ''))}</td>"
        "<td>"
        '<form method="post" action="/dashboard/suppression/unsuppress" style="display:inline">'
        f'<input type="hidden" name="recipient" value="{escape(str(row.get("recipient") or ""), quote=True)}">'
        '<button type="submit">Unsuppress</button></form>'
        "</td></tr>"
        for row in suppressions
    )
    if not rows:
        rows = '<tr><td colspan="5">No active local suppressions.</td></tr>'
    return f"""
<section class="card" id="mail-standards" style="margin-top:18px">
  <h2>Mail standards health</h2>
  <p>Provider-independent SMTP/IMAP capability discovery, TLS, DNS, quota, DSN, retry and local suppression diagnostics.</p>
  <p><strong>Tracking and newsletter mode are independent.</strong> Open/click tracking never enables unsubscribe headers automatically.</p>
  <form method="post" action="/dashboard/mail-health/refresh" style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
    <label>Account ID <input name="account_id" placeholder="default"></label>
    <label>DKIM selector (optional) <input name="dkim_selector" placeholder="selector"></label>
    <button type="submit">Refresh health</button>
  </form>
  <h3 style="margin-top:18px">Local suppression</h3>
  <form method="post" action="/dashboard/suppression/suppress" style="display:flex;gap:8px;flex-wrap:wrap;align-items:end">
    <label>Recipient <input name="recipient" type="email" required placeholder="person@example.com"></label>
    <label>Reason
      <select name="reason"><option value="manual">manual</option><option value="unsubscribe">unsubscribe</option></select>
    </label>
    <button type="submit">Suppress</button>
  </form>
  <div style="overflow:auto;margin-top:12px"><table><thead><tr><th>Recipient</th><th>Reason</th><th>Source</th><th>Updated</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
"""


def _raw_imap_connect(settings: Any):
    context = ssl.create_default_context()
    security = (settings.imap_security or "ssl").strip().lower()
    if security == "ssl":
        conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, ssl_context=context)
    elif security in {"starttls", "plain"}:
        conn = imaplib.IMAP4(settings.imap_host, settings.imap_port)
        if security == "starttls":
            typ, _ = conn.starttls(ssl_context=context)
            if typ != "OK":
                raise RuntimeError("IMAP STARTTLS failed")
    else:
        raise RuntimeError(f"Unsupported IMAP security mode: {security}")
    typ, _ = conn.login(
        settings.imap_username or settings.email_address,
        settings.imap_password or settings.email_password,
    )
    if typ != "OK":
        with contextlib.suppress(Exception):
            conn.logout()
        raise RuntimeError("IMAP login failed")
    return conn


def install_runtime_v950(app: Any, base: Any, core: Any, legacy_dashboard: Any, legacy_build_status: Any):
    """Compose v9.5 provider-independent mail standards without adding MCP command names."""

    def authorize_stored_file(info: dict[str, Any]) -> bool:
        base._require_knowledge_scope(
            str(info.get("owner_id") or ""),
            str(info.get("project_id")) if info.get("project_id") else None,
        )
        return True

    def mail_client(account_id: str | None = None) -> PostmasterV950MailClient:
        return PostmasterV950MailClient(
            base.account_store().settings(account_id),
            file_store=base.file_store(),
            file_authorizer=authorize_stored_file,
            analytics=base.analytics_store(),
            tracking_store=stored_file_link_store(),
            reliability=reliability_store(),
        )

    base.mail_client = mail_client
    core.mail_client = mail_client

    def build_status():
        status = legacy_build_status()
        if isinstance(status, dict):
            status.update({
                "mail_standards_v950": True,
                "smtp_capability_discovery": True,
                "imap_capability_discovery": True,
                "smtp_dsn_rfc3461": True,
                "imap_idle": True,
                "imap_quota": True,
                "smtp_retry_backoff": True,
                "smtp_throttling": True,
                "local_suppression": True,
                "dsn_bounce_parsing": True,
                "auto_reply_detection": True,
                "newsletter_mode_explicit_only": True,
                "tracking_implies_newsletter": False,
                "dns_mail_health": True,
                "tls_mail_health": True,
                "mime_header_diagnostics": True,
                "delivery_conversation_state": True,
                "new_mail_mcp_commands": 0,
            })
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    core.build_status = build_status
    base.build_status = build_status

    def test_email_account(
        account_id: str | None = None,
        refresh: bool = False,
        dkim_selector: str | None = None,
    ):
        """Read-only network diagnostics for SMTP/IMAP capabilities, TLS, quota and sender-domain DNS health."""
        return base._safe_call(
            mail_client(account_id).test_connections,
            refresh=refresh,
            dkim_selector=dkim_selector,
            include_dns=True,
        )

    core.mcp.remove_tool("test_email_account")
    core.mcp.add_tool(test_email_account, name="test_email_account")
    base.test_email_account = test_email_account

    def mailbox_status(account_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
        """Read-only mailbox status with IMAP capability, quota and TLS health."""
        client = mail_client(account_id)
        result = base._safe_call(client.mailbox_health, refresh=refresh)
        if isinstance(result, dict) and result.get("ok"):
            account = base.account_store().get_account(account_id)
            result.update({
                "build": os.getenv("BRIDGE_BUILD") or os.getenv("POSTMASTER_REF") or "unknown",
                "html_email": True,
                "draft_mailbox": client.draft_mailbox,
                "inbox_mailbox": client.inbox_mailbox,
                "junk_mailbox": client.junk_mailbox,
                "attachment_download": True,
                "attachment_text_read": True,
                "mailbox_move": True,
                "open_tracking": True,
                "amp_email": bool(account.get("amp_enabled")),
                "idle_runtime": idle_manager().status(),
            })
        return result

    core.mcp.remove_tool("mailbox_status")
    core.mcp.add_tool(mailbox_status, name="mailbox_status")
    base.mailbox_status = mailbox_status

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
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False,
    ):
        """WRITE ACTION. Existing send with optional explicit newsletter/unsubscribe and DSN-success request. Tracking never implies newsletter mode."""
        return base._safe_call(
            mail_client(account_id).send_email,
            to=to, subject=subject, body=body, cc=cc, bcc=bcc,
            body_html=body_html, body_amp=body_amp, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
        )

    core.mcp.remove_tool("send_email")
    core.mcp.add_tool(send_email, name="send_email")
    base.send_email = send_email

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
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False,
    ):
        """WRITE ACTION. Existing threaded reply; unsubscribe headers remain off unless newsletter_mode is explicit."""
        return base._safe_call(
            mail_client(account_id).reply_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
        )

    core.mcp.remove_tool("reply_email")
    core.mcp.add_tool(reply_email, name="reply_email")
    base.reply_email = reply_email

    def follow_up_email(
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
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False,
    ):
        """WRITE ACTION. Existing outbound follow-up with the same explicit newsletter and DSN semantics as send_email."""
        return base._safe_call(
            mail_client(account_id).follow_up_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
        )

    core.mcp.remove_tool("follow_up_email")
    core.mcp.add_tool(follow_up_email, name="follow_up_email")
    core.follow_up_email = follow_up_email

    def create_draft(
        to: list[str], subject: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        body_amp: str | None = None, attachments: list[dict[str, Any]] | None = None,
        account_id: str | None = None, newsletter_mode: bool = False,
        unsubscribe_url: str | None = None, unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
    ):
        """WRITE ACTION. Existing draft creation with optional explicit newsletter headers."""
        return base._safe_call(
            mail_client(account_id).create_draft,
            to=to, subject=subject, body=body, cc=cc, bcc=bcc,
            body_html=body_html, body_amp=body_amp, attachments=attachments,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
        )

    core.mcp.remove_tool("create_draft")
    core.mcp.add_tool(create_draft, name="create_draft")
    base.create_draft = create_draft

    def create_reply_draft(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
    ):
        """WRITE ACTION. Existing threaded reply draft with optional explicit newsletter headers."""
        return base._safe_call(
            mail_client(account_id).create_reply_draft,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
        )

    core.mcp.remove_tool("create_reply_draft")
    core.mcp.add_tool(create_reply_draft, name="create_reply_draft")
    base.create_reply_draft = create_reply_draft

    def create_follow_up_draft(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
    ):
        """WRITE ACTION. Existing follow-up draft with optional explicit newsletter headers."""
        return base._safe_call(
            mail_client(account_id).create_follow_up_draft,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
        )

    core.mcp.remove_tool("create_follow_up_draft")
    core.mcp.add_tool(create_follow_up_draft, name="create_follow_up_draft")
    core.create_follow_up_draft = create_follow_up_draft

    def list_tracking_deliveries(
        campaign_id: str | None = None,
        recipient: str | None = None,
        account_id: str | None = None,
        limit: int = 250,
    ):
        """Read-only existing delivery list enriched with retry, bounce, conversation and suppression state."""
        rows = base._safe_call(
            base.analytics_store().list_deliveries,
            campaign_id=campaign_id, recipient=recipient, account_id=account_id, limit=limit,
        )
        if isinstance(rows, list):
            return [reliability_store().enrich_delivery(row) for row in rows]
        return rows

    core.mcp.remove_tool("list_tracking_deliveries")
    core.mcp.add_tool(list_tracking_deliveries, name="list_tracking_deliveries")
    base.list_tracking_deliveries = list_tracking_deliveries

    legacy_tracking_summary = core.get_tracking_summary

    def get_tracking_summary(
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        account_id: str | None = None,
    ):
        """Read-only existing tracking summary plus local provider-independent delivery health metrics."""
        summary = legacy_tracking_summary(
            campaign_id=campaign_id, delivery_id=delivery_id,
            link_id=link_id, account_id=account_id,
        )
        if isinstance(summary, dict):
            summary["delivery_health"] = reliability_store().metrics(
                account_id=account_id, campaign_id=campaign_id
            )
        return summary

    core.mcp.remove_tool("get_tracking_summary")
    core.mcp.add_tool(get_tracking_summary, name="get_tracking_summary")
    core.get_tracking_summary = get_tracking_summary

    legacy_tracking_status = core.tracking_status

    def tracking_status():
        status = legacy_tracking_status()
        if isinstance(status, dict):
            status["delivery_reliability"] = {
                "retry_history": True,
                "suppression": True,
                "bounce_parser": True,
                "conversation_state": True,
                "active_suppressions": len(reliability_store().list_suppressions(active_only=True, limit=5000)),
            }
        return status

    core.mcp.remove_tool("tracking_status")
    core.mcp.add_tool(tracking_status, name="tracking_status")
    core.tracking_status = tracking_status
    base.tracking_status = tracking_status

    async def dashboard_home(request: Request):
        response = await legacy_dashboard(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        try:
            body = response.body.decode("utf-8")
            panel = _standards_panel(base)
            if "id=\"mail-standards\"" not in body:
                if "<footer class=\"postmaster-version-footer\"" in body:
                    body = body.replace('<footer class="postmaster-version-footer"', panel + '<footer class="postmaster-version-footer"', 1)
                elif "</main>" in body:
                    body = body.replace("</main>", panel + "</main>", 1)
                else:
                    body += panel
            return HTMLResponse(
                body,
                status_code=response.status_code,
                headers={key: value for key, value in response.headers.items() if key.lower() != "content-length"},
            )
        except Exception:
            base.logger.info("Could not augment v9.5 mail standards dashboard", exc_info=True)
            return response

    async def dashboard_health_refresh(request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        account_id = str(form.get("account_id") or "").strip() or None
        selector = str(form.get("dkim_selector") or "").strip() or None
        result = test_email_account(account_id=account_id, refresh=True, dkim_selector=selector)
        body = (
            "<!doctype html><meta charset=utf-8><title>Mail health</title>"
            "<main style='max-width:1100px;margin:30px auto;font-family:system-ui'>"
            "<p><a href='/'>← Dashboard</a></p><h1>Mail health diagnostics</h1>"
            f"<pre style='white-space:pre-wrap'>{escape(_safe_json(result))}</pre></main>"
        )
        return HTMLResponse(body)

    async def dashboard_suppress(request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        recipient = str(form.get("recipient") or "").strip()
        reason = str(form.get("reason") or "manual").strip()
        if reason not in {"manual", "unsubscribe"}:
            reason = "manual"
        try:
            reliability_store().suppress(recipient, reason=reason, source="webgui")
        except Exception as exc:
            return HTMLResponse(f"<p>Suppression failed: {escape(str(exc))}</p><p><a href='/'>Dashboard</a></p>", status_code=400)
        return RedirectResponse("/#mail-standards", status_code=303)

    async def dashboard_unsuppress(request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        recipient = str(form.get("recipient") or "").strip()
        reliability_store().unsuppress(recipient, source="webgui")
        return RedirectResponse("/#mail-standards", status_code=303)

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/":
            routes[index] = Route("/", dashboard_home, methods=["GET"])
    routes.extend([
        Route("/dashboard/mail-health/refresh", dashboard_health_refresh, methods=["POST"]),
        Route("/dashboard/suppression/suppress", dashboard_suppress, methods=["POST"]),
        Route("/dashboard/suppression/unsuppress", dashboard_unsuppress, methods=["POST"]),
    ])

    legacy_lifespan = app.router.lifespan_context

    @_contextlib.asynccontextmanager
    async def v950_lifespan(starlette_app: Any):
        async with legacy_lifespan(starlette_app):
            manager = idle_manager()
            if _env_bool("IMAP_IDLE_ENABLED", True):
                watchers: list[IMAPIdleWatcher] = []
                settings = IdleSettings(
                    reidle_seconds=max(30.0, float(os.getenv("IMAP_IDLE_REIDLE_SECONDS", "1500"))),
                    socket_timeout_seconds=max(5.0, float(os.getenv("IMAP_IDLE_SOCKET_TIMEOUT_SECONDS", "60"))),
                    poll_seconds=max(10.0, float(os.getenv("IMAP_POLL_SECONDS", "60"))),
                    reconnect_base_seconds=max(0.5, float(os.getenv("IMAP_IDLE_RECONNECT_BASE_SECONDS", "1"))),
                    reconnect_max_seconds=max(1.0, float(os.getenv("IMAP_IDLE_RECONNECT_MAX_SECONDS", "60"))),
                )
                for row in base.account_store().list_accounts(include_disabled=False):
                    account_id = str(row.get("id") or row.get("account_id") or "").strip()
                    if not account_id:
                        continue
                    cfg = base.account_store().settings(account_id)
                    def connect(cfg=cfg):
                        return _raw_imap_connect(cfg)
                    def on_change(current_account_id: str, event: Any):
                        try:
                            mail_client(current_account_id).process_inbound_changes()
                        except Exception:
                            base.logger.info("IMAP IDLE inbound processing failed for %s", current_account_id, exc_info=True)
                    def poll(current_account_id: str):
                        try:
                            mail_client(current_account_id).process_inbound_changes()
                        except Exception:
                            base.logger.info("IMAP polling inbound processing failed for %s", current_account_id, exc_info=True)
                    watchers.append(IMAPIdleWatcher(
                        account_id=account_id,
                        connect=connect,
                        on_change=on_change,
                        poll=poll,
                        settings=settings,
                    ))
                manager.start(watchers)
            try:
                yield
            finally:
                manager.stop(join_timeout=5.0)

    app.router.lifespan_context = v950_lifespan

    return dashboard_home, build_status, mail_client
