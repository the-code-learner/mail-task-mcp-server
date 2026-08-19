from __future__ import annotations

import imaplib
import smtplib
import ssl
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from html import escape
from typing import Any

from .email_analytics import analytics_store
from .link_tracking import link_store
from .mail_bridge import MailBridgeError
from .mail_extensions import EnhancedMailClient, _plain_to_html

def _sent_clean_html(body_html: str, delivery: dict[str, Any]) -> str:
    """Render recipient-visible placeholders without retaining recipient telemetry URLs."""
    html = body_html or ""
    replacements = {
        "{{RECIPIENT}}": str(delivery["recipient"]),
        "{{CAMPAIGN_ID}}": str(delivery["campaign_id"]),
        "{{DELIVERY_ID}}": str(delivery["id"]),
        "{{AMP_STATUS_URL}}": "",
        "{{TRACKING_PIXEL_URL}}": "",
    }
    for key, value in replacements.items():
        html = html.replace(key, escape(value, quote=True))
    return html


def _synchronize_transport_headers(outbound: EmailMessage, sent_copy: EmailMessage, sender: str) -> None:
    if "Date" not in outbound:
        outbound["Date"] = format_datetime(datetime.now().astimezone())
    if "Message-ID" not in outbound:
        domain = sender.rsplit("@", 1)[-1]
        outbound["Message-ID"] = make_msgid(domain=domain)
    for header in ("Date", "Message-ID"):
        if header in sent_copy:
            del sent_copy[header]
        sent_copy[header] = str(outbound[header])


class LinkTrackingMailClient(EnhancedMailClient):
    """v9.4 delivery variant: tracked recipient MIME plus clean archived Sent MIME."""

    def _send_message_with_clean_sent(self, outbound: EmailMessage, sent_copy: EmailMessage, recipients: list[str]) -> dict[str, Any]:
        if not self.settings.enable_send:
            raise MailBridgeError(
                "Sending is disabled. Set ENABLE_SEND=true only when you are ready to allow SMTP writes."
            )
        _synchronize_transport_headers(outbound, sent_copy, self.settings.email_address)
        context = ssl.create_default_context()
        security = (self.settings.smtp_security or ("starttls" if self.settings.smtp_starttls else "ssl")).strip().lower()
        username = self.settings.smtp_username or self.settings.email_address
        password = self.settings.smtp_password or self.settings.email_password
        if security == "ssl":
            with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=30, context=context) as smtp:
                smtp.login(username, password)
                smtp.send_message(outbound, from_addr=self.settings.email_address, to_addrs=recipients)
        else:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if security == "starttls":
                    smtp.starttls(context=context)
                    smtp.ehlo()
                elif security != "plain":
                    raise MailBridgeError(f"Unsupported SMTP security mode: {security}")
                smtp.login(username, password)
                smtp.send_message(outbound, from_addr=self.settings.email_address, to_addrs=recipients)
        if hasattr(self, "_sent_addresses_cache"):
            delattr(self, "_sent_addresses_cache")
        sent_copy_saved = False
        sent_copy_error = None
        if self.settings.save_sent_copy:
            try:
                with self._imap() as conn:
                    typ, _ = conn.append(
                        self.settings.sent_mailbox, r"\Seen",
                        imaplib.Time2Internaldate(datetime.now().timestamp()),
                        sent_copy.as_bytes(policy=policy.SMTP),
                    )
                    sent_copy_saved = typ == "OK"
                    if not sent_copy_saved:
                        sent_copy_error = "IMAP APPEND returned non-OK"
            except Exception as exc:
                sent_copy_error = type(exc).__name__
        return {
            "sent": True, "from": self.settings.email_address, "to": recipients,
            "subject": str(outbound.get("Subject", "")), "message_id": str(outbound.get("Message-ID", "")),
            "sent_copy_saved": sent_copy_saved, "sent_copy_error": sent_copy_error,
            "sent_copy_tracking_sanitized": True,
        }

    def _send_individualized(
        self, *, to: list[str], subject: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None, body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None, track_opens: bool,
        campaign_id: str | None = None, in_reply_to: str = "", references: str = "",
    ) -> dict[str, Any]:
        amp_used = bool(body_amp)
        to_clean = self._validate_recipients(to)
        cc_clean = self._validate_recipients(cc or []) if cc else []
        bcc_clean = self._validate_recipients(bcc or []) if bcc else []
        recipient_roles: list[tuple[str, str]] = []
        seen: set[str] = set()
        for role, addresses in (("to", to_clean), ("cc", cc_clean), ("bcc", bcc_clean)):
            for address in addresses:
                key = address.lower()
                if key not in seen:
                    seen.add(key)
                    recipient_roles.append((address, role))
        analytics = analytics_store()
        links = link_store()
        if track_opens or amp_used:
            analytics.validate_public_base_url()
        campaign = analytics.create_campaign(
            account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
            sender=self.settings.email_address, subject=subject.strip(), track_opens=track_opens,
            amp_used=amp_used, campaign_id=campaign_id,
        )
        base_html = body_html if body_html is not None else _plain_to_html(body)
        delivery_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        attachment_meta: list[dict[str, Any]] = []
        for recipient, role in recipient_roles:
            delivery = analytics.create_delivery(
                campaign_id=campaign["id"],
                account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
                recipient=recipient, recipient_role=role,
            )
            recipient_html, recipient_amp = analytics.render_for_recipient(
                body_html=base_html, body_amp=body_amp, delivery=delivery, track_opens=track_opens,
            )
            link_meta: list[dict[str, Any]] = []
            if track_opens:
                recipient_html, link_meta = links.instrument_html(body_html=recipient_html, delivery=delivery)
            clean_html = _sent_clean_html(base_html, delivery)
            try:
                outbound, _, meta = self._build_message(
                    to=to_clean, cc=cc_clean, subject=subject, body=body, body_html=recipient_html,
                    body_amp=recipient_amp, attachments=attachments, allow_unlisted=False,
                    in_reply_to=in_reply_to, references=references,
                )
                sent_copy, _, _ = self._build_message(
                    to=to_clean, cc=cc_clean, subject=subject, body=body, body_html=clean_html,
                    body_amp=None, attachments=attachments, allow_unlisted=False,
                    in_reply_to=in_reply_to, references=references,
                )
                result = self._send_message_with_clean_sent(outbound, sent_copy, [recipient])
                analytics.mark_sent(delivery["id"], str(result.get("message_id", "")))
                links.mark_delivery_message(delivery["id"], str(result.get("message_id", "")))
                attachment_meta = meta
                delivery_results.append({
                    "delivery_id": delivery["id"], "recipient": recipient, "role": role,
                    "message_id": result.get("message_id", ""),
                    "sent_copy_saved": result.get("sent_copy_saved", False),
                    "sent_copy_tracking_sanitized": True, "link_tracking": bool(track_opens),
                    "links": link_meta,
                })
            except Exception as exc:
                errors.append({
                    "delivery_id": delivery["id"], "recipient": recipient, "role": role,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return {
            "sent": bool(delivery_results) and not errors, "partial": bool(delivery_results) and bool(errors),
            "from": self.settings.email_address, "subject": subject.strip(), "campaign_id": campaign["id"],
            "individualized": True, "visible_recipient_headers_preserved": True,
            "tracked": bool(track_opens), "link_tracking": bool(track_opens),
            "sent_copy_tracking_sanitized": True, "amp": amp_used,
            "amp_registered": bool(getattr(self.settings, "amp_registered", False)),
            "deliveries": delivery_results, "errors": errors, "attachments": attachment_meta,
            "tracking_note": (
                "Open and click events are fetch telemetry and may be affected by mail proxies, "
                "security scanners or prefetching; v9.4 does not classify human vs scanner clicks."
            ) if track_opens else "",
        }
