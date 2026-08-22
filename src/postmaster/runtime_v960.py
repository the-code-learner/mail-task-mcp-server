from __future__ import annotations

from functools import lru_cache
from typing import Any

from .mail_v960 import PostmasterV960MailClient
from .outbound_safety import OutboundSafetyStore
from .runtime_v950 import reliability_store
from .stored_file_delivery import stored_file_link_store


@lru_cache(maxsize=1)
def outbound_safety_store() -> OutboundSafetyStore:
    return OutboundSafetyStore()


def install_runtime_v960(
    app: Any,
    base: Any,
    core: Any,
    legacy_dashboard: Any,
    legacy_build_status: Any,
):
    """Install v9.6 additive contracts without changing MCP command names."""

    def authorize_stored_file(info: dict[str, Any]) -> bool:
        base._require_knowledge_scope(
            str(info.get("owner_id") or ""),
            str(info.get("project_id")) if info.get("project_id") else None,
        )
        return True

    def mail_client(account_id: str | None = None) -> PostmasterV960MailClient:
        return PostmasterV960MailClient(
            base.account_store().settings(account_id),
            file_store=base.file_store(),
            file_authorizer=authorize_stored_file,
            analytics=base.analytics_store(),
            tracking_store=stored_file_link_store(),
            reliability=reliability_store(),
            outbound_safety=outbound_safety_store(),
        )

    base.mail_client = mail_client
    core.mail_client = mail_client

    def build_status():
        status = legacy_build_status()
        if isinstance(status, dict):
            status.update(
                {
                    "outbound_idempotency_barrier": True,
                    "outbound_duplicate_guard": True,
                    "delivery_uncertain_auto_retry": False,
                    "inbound_static_privacy_inspection": True,
                    "inbound_inspection_network_requests": 0,
                    "safe_reader_html": True,
                    "imap_special_use_roles": True,
                    "seen_unseen_listing": True,
                    "new_mail_mcp_commands": 0,
                }
            )
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    core.build_status = build_status
    base.build_status = build_status

    def mailbox_status(account_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
        client = mail_client(account_id)
        result = base._safe_call(client.mailbox_health, refresh=refresh)
        if isinstance(result, dict) and result.get("ok"):
            account = base.account_store().get_account(account_id)
            result.update(
                {
                    "html_email": True,
                    "draft_mailbox": client.draft_mailbox,
                    "inbox_mailbox": client.inbox_mailbox,
                    "junk_mailbox": client.junk_mailbox,
                    "sent_mailbox": client.settings.sent_mailbox,
                    "mailbox_roles": base._safe_call(client.mailbox_catalog),
                    "attachment_download": True,
                    "attachment_text_read": True,
                    "mailbox_move": True,
                    "open_tracking": True,
                    "amp_email": bool(account.get("amp_enabled")),
                }
            )
        return result

    core.mcp.remove_tool("mailbox_status")
    core.mcp.add_tool(mailbox_status, name="mailbox_status")
    base.mailbox_status = mailbox_status

    def get_email(
        mailbox: str,
        uid: str,
        account_id: str | None = None,
        inspection: str | None = None,
        content_mode: str = "safe",
        acknowledge_unsanitized_content_risk: bool = False,
    ):
        """Read one email. Legacy calls keep original body_html; inspection-aware calls are safe by default."""
        return base._safe_call(
            mail_client(account_id).get_email,
            mailbox,
            uid,
            inspection=inspection,
            content_mode=content_mode,
            acknowledge_unsanitized_content_risk=acknowledge_unsanitized_content_risk,
        )

    core.mcp.remove_tool("get_email")
    core.mcp.add_tool(get_email, name="get_email")
    base.get_email = get_email

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
        idempotency_key: str | None = None,
        force_send: bool = False,
    ):
        """WRITE ACTION. v9.6 persistent idempotency + short-window duplicate protection."""
        return base._safe_call(
            mail_client(account_id).send_email,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            body_amp=body_amp,
            attachments=attachments,
            track_opens=track_opens,
            campaign_id=campaign_id,
            newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
            idempotency_key=idempotency_key,
            force_send=force_send,
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
        idempotency_key: str | None = None,
        force_send: bool = False,
    ):
        return base._safe_call(
            mail_client(account_id).reply_email,
            mailbox=mailbox,
            uid=uid,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            attachments=attachments,
            track_opens=track_opens,
            campaign_id=campaign_id,
            newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
            idempotency_key=idempotency_key,
            force_send=force_send,
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
        idempotency_key: str | None = None,
        force_send: bool = False,
    ):
        return base._safe_call(
            mail_client(account_id).follow_up_email,
            mailbox=mailbox,
            uid=uid,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            attachments=attachments,
            track_opens=track_opens,
            campaign_id=campaign_id,
            newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
            idempotency_key=idempotency_key,
            force_send=force_send,
        )

    core.mcp.remove_tool("follow_up_email")
    core.mcp.add_tool(follow_up_email, name="follow_up_email")
    core.follow_up_email = follow_up_email
    base.follow_up_email = follow_up_email

    return legacy_dashboard, build_status, mail_client
