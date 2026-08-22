from __future__ import annotations

from typing import Any

from .mail_bridge import MailBridgeError
from .mail_v950 import PostmasterV950MailClient, _DELIVERY_ID
from .mail_v960 import PostmasterV960MailClient
from .unsubscribe import UnsubscribeError, UnsubscribeManager


class PostmasterV960NewsletterMailClient(PostmasterV960MailClient):
    """Automatic delivery-scoped unsubscribe on top of the v9.6 safety client."""

    def __init__(
        self,
        settings: Any,
        *,
        unsubscribe_manager: UnsubscribeManager | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        self.unsubscribe_manager = unsubscribe_manager or UnsubscribeManager()

    def _build_message(self, **kwargs: Any):
        msg, recipients, meta = super()._build_message(**kwargs)
        value = str(msg.get("List-Unsubscribe") or "")
        if "{{DELIVERY_ID}}" in value:
            delivery_id = str(_DELIVERY_ID.get() or "")
            if not delivery_id:
                raise MailBridgeError(
                    "automatic unsubscribe requires an individualized delivery context"
                )
            try:
                actual = self.unsubscribe_manager.url_for_delivery(delivery_id)
            except UnsubscribeError as exc:
                raise MailBridgeError(str(exc)) from exc
            placeholder = self.unsubscribe_manager.placeholder_url()
            if "List-Unsubscribe" in msg:
                del msg["List-Unsubscribe"]
            msg["List-Unsubscribe"] = value.replace(placeholder, actual)
        return msg, recipients, meta

    def _send_automatic_newsletter(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        options = dict(kwargs)
        options.pop("automatic_unsubscribe", None)
        options.pop("newsletter_mode", None)
        options.pop("unsubscribe_url", None)
        options.pop("unsubscribe_email", None)
        requested_one_click = bool(options.pop("one_click_unsubscribe", False))
        dsn_notify_success = bool(options.pop("dsn_notify_success", False))
        try:
            placeholder = self.unsubscribe_manager.placeholder_url()
        except UnsubscribeError as exc:
            raise MailBridgeError(str(exc)) from exc

        with self._delivery_options(
            newsletter_mode=True,
            unsubscribe_url=placeholder,
            unsubscribe_email=None,
            one_click_unsubscribe=True,
            dsn_notify_success=dsn_notify_success,
        ):
            resolved_track = self._resolve_track_opens(options.get("track_opens"))
            if not resolved_track and not options.get("body_amp"):
                result = self._send_individualized(
                    to=options["to"],
                    subject=options["subject"],
                    body=options.get("body", ""),
                    cc=options.get("cc"),
                    bcc=options.get("bcc"),
                    body_html=options.get("body_html"),
                    body_amp=None,
                    attachments=options.get("attachments"),
                    track_opens=False,
                    campaign_id=options.get("campaign_id"),
                )
            else:
                # Skip PostmasterV950.send_email because the delivery options above
                # are already established with the per-delivery placeholder.
                v946 = super(PostmasterV950MailClient, self)
                result = v946.send_email(**options)
        result["newsletter_mode"] = True
        result["automatic_unsubscribe"] = True
        result["one_click_unsubscribe"] = True
        result["one_click_unsubscribe_requested"] = requested_one_click
        result["dsn_notify_success_requested"] = dsn_notify_success
        return result

    def send_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        automatic_unsubscribe: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        auto = (
            bool(automatic_unsubscribe)
            and bool(kwargs.get("newsletter_mode"))
            and not str(kwargs.get("unsubscribe_url") or "").strip()
            and not str(kwargs.get("unsubscribe_email") or "").strip()
        )
        if not auto:
            return super().send_email(
                idempotency_key=idempotency_key,
                force_send=force_send,
                **kwargs,
            )
        operation_payload = dict(kwargs)
        operation_payload["automatic_unsubscribe"] = True
        return self._safe_outbound(
            action="send_email",
            kwargs=operation_payload,
            callback=lambda: self._send_automatic_newsletter(operation_payload),
            idempotency_key=idempotency_key,
            force_send=force_send,
        )
