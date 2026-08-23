from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Iterable

from .mail_bridge import MailBridgeError
from .mail_v950 import PostmasterV950MailClient, _DELIVERY_ID
from .mail_v960 import PostmasterV960MailClient
from .thread_recipients import merge_thread_cc, sender_identity_addresses
from .unsubscribe import UnsubscribeError, UnsubscribeManager


_MANUAL_RECIPIENT_POLICY = contextvars.ContextVar(
    "postmaster_v964_manual_recipient_policy",
    default=False,
)
_SUPPRESSION_AUTHORIZATIONS = contextvars.ContextVar(
    "postmaster_v964_suppression_authorizations",
    default=frozenset(),
)


def _normalized_addresses(values: Iterable[str] | None) -> frozenset[str]:
    if not values:
        return frozenset()
    return frozenset(str(value).strip().casefold() for value in values if str(value).strip())


class _ChannelAwareReliability:
    """Delegate reliability state while enforcing per-call suppression authorization."""

    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def blocked_recipients(self, recipients: list[str]) -> list[dict[str, Any]]:
        blocked = self.wrapped.blocked_recipients(recipients)
        if not blocked:
            return []
        authorized = _SUPPRESSION_AUTHORIZATIONS.get()
        unauthorized = [
            row
            for row in blocked
            if str(row.get("recipient") or "").strip().casefold() not in authorized
        ]
        if unauthorized:
            summary = ", ".join(
                f"{row['recipient']} ({row['reason']})" for row in unauthorized
            )
            raise MailBridgeError(
                "Suppressed recipient authorization required for this send: "
                + summary
                + ". Ask the user for explicit authorization for these suppressed recipient(s), "
                "then retry this send with confirm_suppressed_recipients containing only the "
                "addresses the user explicitly approved. Authorization is per-send and is not persisted."
            )
        # All active suppressions in this transport call were explicitly authorized for this call.
        return []


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
        self._suppression_store = self.reliability
        self.reliability = _ChannelAwareReliability(self.reliability)

    def _validate_recipients(self, recipients: Iterable[str]) -> list[str]:
        if _MANUAL_RECIPIENT_POLICY.get():
            # WebGUI manual sends validate syntax/deduplication but do not consult the
            # automated-send allowlist. This context is ephemeral and never mutates policy.
            return self._clean_unlisted_recipients(recipients)
        return super()._validate_recipients(recipients)

    @contextlib.contextmanager
    def _suppression_authorization(self, recipients: Iterable[str] | None = None):
        current = _SUPPRESSION_AUTHORIZATIONS.get()
        requested = _normalized_addresses(
            self._clean_unlisted_recipients(recipients or []) if recipients else []
        )
        token = _SUPPRESSION_AUTHORIZATIONS.set(frozenset(set(current) | set(requested)))
        try:
            yield
        finally:
            _SUPPRESSION_AUTHORIZATIONS.reset(token)

    @contextlib.contextmanager
    def manual_webgui_send(
        self,
        *,
        authorized_suppressed_recipients: Iterable[str] | None = None,
    ):
        """One-operation manual policy: no automated allowlist mutation or future bypass."""
        manual_token = _MANUAL_RECIPIENT_POLICY.set(True)
        try:
            with self._suppression_authorization(authorized_suppressed_recipients):
                yield self
        finally:
            _MANUAL_RECIPIENT_POLICY.reset(manual_token)

    def suppressed_recipients(self, recipients: Iterable[str]) -> list[dict[str, Any]]:
        cleaned = self._clean_unlisted_recipients(recipients)
        return self._suppression_store.blocked_recipients(cleaned)

    def _preflight_suppression(self, recipients: Iterable[str]) -> None:
        cleaned = self._clean_unlisted_recipients(recipients)
        # The wrapper raises a channel-aware authorization error before the outbound
        # idempotency/duplicate barrier reserves an operation.
        self.reliability.blocked_recipients(cleaned)

    def _preflight_explicit_send(self, kwargs: dict[str, Any]) -> None:
        recipients: list[str] = []
        recipients.extend(kwargs.get("to") or [])
        recipients.extend(kwargs.get("cc") or [])
        recipients.extend(kwargs.get("bcc") or [])
        self._preflight_suppression(recipients)

    def _preflight_thread_send(self, mode: str, kwargs: dict[str, Any]) -> None:
        mailbox = str(kwargs.get("mailbox") or "").strip()
        uid = str(kwargs.get("uid") or "").strip()
        resolved = self.resolve_thread_recipients(mailbox, uid, mode=mode)
        identities = sender_identity_addresses(self.settings)
        cc_clean = merge_thread_cc(
            resolved["to"],
            resolved["cc"],
            kwargs.get("cc"),
            sender_identities=identities,
        )
        bcc_clean = self._clean_unlisted_recipients(kwargs.get("bcc") or []) if kwargs.get("bcc") else []
        self._preflight_suppression([*resolved["to"], *cc_clean, *bcc_clean])

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
        confirm_suppressed_recipients: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self._suppression_authorization(confirm_suppressed_recipients):
            self._preflight_explicit_send(kwargs)
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

    def reply_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        confirm_suppressed_recipients: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self._suppression_authorization(confirm_suppressed_recipients):
            self._preflight_thread_send("reply", kwargs)
            return super().reply_email(
                idempotency_key=idempotency_key,
                force_send=force_send,
                **kwargs,
            )

    def follow_up_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        confirm_suppressed_recipients: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self._suppression_authorization(confirm_suppressed_recipients):
            self._preflight_thread_send("follow_up", kwargs)
            return super().follow_up_email(
                idempotency_key=idempotency_key,
                force_send=force_send,
                **kwargs,
            )
