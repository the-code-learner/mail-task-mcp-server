from __future__ import annotations

from contextvars import ContextVar
from email.message import EmailMessage
from typing import Any

from .mail_v950 import PostmasterV950MailClient
from .mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from .outbound_operations_v969 import outbound_operation_store

_ARCHIVE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("postmaster_v969_archive_context", default=None)
_ORIGINAL_SAVE_SENT_COPY = PostmasterV950MailClient._save_sent_copy

def _install_outbound_archive_boundary() -> None:
    current_save = PostmasterV950MailClient._save_sent_copy
    if not getattr(current_save, "_postmaster_v969_archive_gate", False):

        def _save_sent_copy(self: Any, msg: EmailMessage):
            context = _ARCHIVE_CONTEXT.get()
            if context is not None:
                if context.get("canonical_message") is None:
                    context["canonical_message"] = msg
                return False, None
            return _ORIGINAL_SAVE_SENT_COPY(self, msg)

        _save_sent_copy._postmaster_v969_archive_gate = True  # type: ignore[attr-defined]
        PostmasterV950MailClient._save_sent_copy = _save_sent_copy  # type: ignore[assignment]

    current = PostmasterV960NewsletterMailClient._send_individualized
    if getattr(current, "_postmaster_v969_logical_sent", False):
        return

    def _send_individualized(self: Any, *args: Any, **kwargs: Any):
        context: dict[str, Any] = {"canonical_message": None}
        token = _ARCHIVE_CONTEXT.set(context)
        try:
            result = current(self, *args, **kwargs)
        finally:
            _ARCHIVE_CONTEXT.reset(token)

        if not isinstance(result, dict):
            return result
        deliveries = list(result.get("deliveries") or [])
        canonical = context.get("canonical_message")
        archived = False
        archive_error: str | None = None
        canonical_message_id = ""
        if deliveries and isinstance(canonical, EmailMessage):
            canonical_message_id = str(canonical.get("Message-ID") or "")
            archived, archive_error = _ORIGINAL_SAVE_SENT_COPY(self, canonical)

        for delivery in deliveries:
            if isinstance(delivery, dict):
                is_canonical = bool(
                    canonical_message_id
                    and str(delivery.get("message_id") or "") == canonical_message_id
                )
                delivery["sent_copy_saved"] = bool(archived and is_canonical)
                delivery["canonical_sent_archive"] = bool(archived and is_canonical)

        operation_id = str(result.get("campaign_id") or "")
        account_id = str(
            getattr(self.settings, "account_id", "")
            or getattr(self.settings, "email_address", "")
            or ""
        )
        try:
            to_values = self._validate_recipients(kwargs.get("to") or [])
            cc_values = self._validate_recipients(kwargs.get("cc") or []) if kwargs.get("cc") else []
            bcc_values = self._validate_recipients(kwargs.get("bcc") or []) if kwargs.get("bcc") else []
        except Exception:
            to_values = [str(v) for v in kwargs.get("to") or []]
            cc_values = [str(v) for v in kwargs.get("cc") or []]
            bcc_values = [str(v) for v in kwargs.get("bcc") or []]

        metadata_saved = False
        if operation_id and deliveries:
            try:
                outbound_operation_store().record_operation(
                    operation_id=operation_id,
                    account_id=account_id,
                    canonical_message_id=canonical_message_id,
                    canonical_sent_archived=archived,
                    to=to_values,
                    cc=cc_values,
                    bcc=bcc_values,
                    deliveries=deliveries,
                )
                metadata_saved = True
            except Exception:
                metadata_saved = False

        result.update(
            {
                "logical_outbound_operation_id": operation_id,
                "canonical_sent_copy_saved": bool(archived),
                "canonical_sent_message_id": canonical_message_id,
                "canonical_sent_error": archive_error or "",
                "sender_private_recipient_metadata_saved": metadata_saved,
                "sent_append_count": 1 if archived else 0,
            }
        )
        return result

    _send_individualized._postmaster_v969_logical_sent = True  # type: ignore[attr-defined]
    PostmasterV960NewsletterMailClient._send_individualized = _send_individualized  # type: ignore[assignment]
