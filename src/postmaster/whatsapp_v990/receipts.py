from __future__ import annotations

from dataclasses import dataclass


class ReceiptPolicyError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ReceiptDecision:
    emit: bool
    receipt_type: str | None
    reason: str


def receipt_for_event(*, event: str, outbound_action: bool = False, reply_to_message: bool = False) -> ReceiptDecision:
    """Enforce Postmaster's asymmetric WhatsApp receipt contract.

    Merely displaying/reading/syncing a message never emits a read receipt. The only permitted
    read receipt path is coupled to an explicit outbound send/reply flow.
    """
    normalized = str(event or "").strip().lower()
    if normalized in {"display", "read", "sync", "history", "inspect"}:
        return ReceiptDecision(False, None, "local_read_is_private")
    if normalized in {"send", "reply"}:
        if outbound_action and (normalized == "reply" or reply_to_message):
            return ReceiptDecision(True, "read", "coupled_to_explicit_outbound_reply")
        return ReceiptDecision(False, None, "outbound_send_without_reply_does_not_imply_read")
    return ReceiptDecision(False, None, "no_receipt_for_event")


def assert_can_emit_read_receipt(*, outbound_action: bool, reply_to_message: bool) -> None:
    decision = receipt_for_event(event="reply", outbound_action=outbound_action, reply_to_message=reply_to_message)
    if not decision.emit:
        raise ReceiptPolicyError("WhatsApp read receipt is allowed only during an explicit outbound reply flow")
