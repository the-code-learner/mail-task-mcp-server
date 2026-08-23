from __future__ import annotations

from typing import Any


def install_runtime_v964(base: Any, core: Any, legacy_build_status: Any):
    """v9.6.4 mail-policy overlay. Existing tool names only."""

    def build_status():
        status = legacy_build_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status.update({
            "version_capability": "9.6.4",
            "outbound_historical_tracking_normalized": True,
            "manual_webgui_recipient_policy": True,
            "suppression_confirmation_per_send": True,
            "new_mail_mcp_commands": 0,
            "mcp_command_count_expected": 90,
        })
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    base.build_status = build_status
    core.build_status = build_status

    def send_email(
        to: list[str], subject: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        body_amp: str | None = None, attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None, campaign_id: str | None = None,
        account_id: str | None = None, newsletter_mode: bool = False,
        unsubscribe_url: str | None = None, unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False, automatic_unsubscribe: bool = True,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Send email through the existing outbound pipeline.

        Suppressed recipients are blocked by default. If a call reports that suppression
        authorization is required, ask the user to explicitly approve the exact suppressed
        address(es) for this specific send before retrying. Only after that approval may
        `confirm_suppressed_recipients` contain those exact addresses. The confirmation is
        ephemeral and does not alter recipient authorization or the suppression list.
        """
        return base._safe_call(
            base.mail_client(account_id).send_email,
            to=to, subject=subject, body=body, cc=cc, bcc=bcc, body_html=body_html,
            body_amp=body_amp, attachments=attachments, track_opens=track_opens,
            campaign_id=campaign_id, newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            automatic_unsubscribe=automatic_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("send_email")
    core.mcp.add_tool(send_email, name="send_email")
    base.send_email = send_email
    core.send_email = send_email

    def reply_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Reply in-thread through the existing outbound pipeline.

        Suppressed recipients require explicit user approval for this single reply before their
        exact addresses may be passed in `confirm_suppressed_recipients`. Never infer or persist
        that approval; retry only after the user has approved the addresses named by the block.
        """
        return base._safe_call(
            base.mail_client(account_id).reply_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc, body_html=body_html,
            attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("reply_email")
    core.mcp.add_tool(reply_email, name="reply_email")
    base.reply_email = reply_email
    core.reply_email = reply_email

    def follow_up_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Follow up on an outbound message through the existing pipeline.

        Suppressed recipients require explicit user approval for this single follow-up before
        their exact addresses may be passed in `confirm_suppressed_recipients`. The approval is
        per-send only and must not be inferred from prior sends or stored as future authorization.
        """
        return base._safe_call(
            base.mail_client(account_id).follow_up_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc, body_html=body_html,
            attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("follow_up_email")
    core.mcp.add_tool(follow_up_email, name="follow_up_email")
    base.follow_up_email = follow_up_email
    core.follow_up_email = follow_up_email
    return build_status


__all__ = ["install_runtime_v964"]
