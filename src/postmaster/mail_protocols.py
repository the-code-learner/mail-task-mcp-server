from __future__ import annotations

import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any, Iterable, Mapping


KNOWN_SMTP_EXTENSIONS = {
    "SIZE", "8BITMIME", "SMTPUTF8", "PIPELINING", "DSN", "STARTTLS", "AUTH",
}
KNOWN_IMAP_CAPABILITIES = {
    "IDLE", "MOVE", "UIDPLUS", "SPECIAL-USE", "NAMESPACE", "QUOTA",
    "CONDSTORE", "QRESYNC", "SORT", "THREAD",
}


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def parse_smtp_capabilities(features: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized: dict[str, str] = {}
    for key, value in (features or {}).items():
        name = _text(key).strip().upper()
        if name:
            normalized[name] = _text(value).strip()

    auth = normalized.get("AUTH", "")
    auth_mechanisms = sorted({
        token.upper()
        for token in re.split(r"[\s,]+", auth)
        if token.strip()
    })
    size_limit = None
    if "SIZE" in normalized:
        match = re.search(r"\d+", normalized["SIZE"])
        if match:
            size_limit = int(match.group(0))

    return {
        "raw": normalized,
        "extensions": sorted(normalized),
        "size": {"supported": "SIZE" in normalized, "limit": size_limit},
        "8bitmime": "8BITMIME" in normalized,
        "smtputf8": "SMTPUTF8" in normalized,
        "pipelining": "PIPELINING" in normalized,
        "dsn": "DSN" in normalized,
        "starttls": "STARTTLS" in normalized,
        "auth": {
            "supported": "AUTH" in normalized,
            "mechanisms": auth_mechanisms,
        },
        "unknown_extensions": sorted(set(normalized) - KNOWN_SMTP_EXTENSIONS),
    }


def parse_imap_capabilities(values: Iterable[Any] | None) -> dict[str, Any]:
    caps: set[str] = set()
    for raw in values or ():
        for token in _text(raw).split():
            token = token.strip().upper()
            if token:
                caps.add(token)
    return {
        "raw": sorted(caps),
        "idle": "IDLE" in caps,
        "move": "MOVE" in caps,
        "uidplus": "UIDPLUS" in caps,
        "special_use": "SPECIAL-USE" in caps,
        "namespace": "NAMESPACE" in caps,
        "quota": "QUOTA" in caps,
        "condstore": "CONDSTORE" in caps,
        "qresync": "QRESYNC" in caps,
        "sort": "SORT" in caps,
        "thread": any(x == "THREAD" or x.startswith("THREAD=") for x in caps),
        "unknown_capabilities": sorted(
            x for x in caps
            if x not in KNOWN_IMAP_CAPABILITIES and not x.startswith("THREAD=")
        ),
    }


_QUOTA_RE = re.compile(r"([A-Za-z0-9._-]+)\s+(\d+)\s+(\d+)")


def parse_imap_quota(data: Iterable[Any] | None) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in data or ():
        text = _text(item)
        for match in _QUOTA_RE.finditer(text):
            name, used_raw, limit_raw = match.groups()
            used = int(used_raw)
            limit = int(limit_raw)
            key = (name.upper(), used, limit)
            if key in seen:
                continue
            seen.add(key)
            percent = None if limit <= 0 else round((used / limit) * 100.0, 2)
            resources.append({
                "resource": name.upper(),
                "used": used,
                "limit": limit,
                "percent": percent,
                "unlimited_or_unknown_limit": limit <= 0,
            })
    return {"supported": bool(resources), "resources": resources}


def xtext(value: str) -> str:
    """RFC 3461 xtext encoding for ENVID/ORCPT parameter values."""
    out: list[str] = []
    for byte in (value or "").encode("utf-8"):
        if 33 <= byte <= 126 and byte not in (43, 61):
            out.append(chr(byte))
        else:
            out.append(f"+{byte:02X}")
    return "".join(out)


def build_dsn_options(*, envelope_id: str, recipient: str, notify_success: bool = False) -> tuple[list[str], list[str]]:
    notify = ["FAILURE", "DELAY"]
    if notify_success:
        notify.append("SUCCESS")
    mail_options = [f"ENVID={xtext(envelope_id)}"] if envelope_id else []
    rcpt_options = ["NOTIFY=" + ",".join(notify), f"ORCPT=rfc822;{xtext(recipient)}"]
    return mail_options, rcpt_options


def detect_auto_reply(msg: Message) -> dict[str, Any]:
    reasons: list[str] = []
    auto_submitted = _text(msg.get("Auto-Submitted")).strip().lower()
    precedence = _text(msg.get("Precedence")).strip().lower()
    suppress = _text(msg.get("X-Auto-Response-Suppress")).strip().lower()
    if auto_submitted and auto_submitted != "no":
        reasons.append(f"Auto-Submitted={auto_submitted}")
    if precedence in {"bulk", "junk", "list", "auto_reply"}:
        reasons.append(f"Precedence={precedence}")
    if suppress and suppress not in {"none", "no"}:
        reasons.append("X-Auto-Response-Suppress")
    subject = _text(msg.get("Subject")).casefold()
    heuristic = bool(re.search(r"\b(out[ -]?of[ -]?office|automatic reply|auto(?:matic)?[ -]?reply|vacation)\b", subject, flags=re.I))
    if heuristic and not auto_submitted:
        reasons.append("vacation-like subject heuristic")
    is_auto = bool(reasons)
    return {
        "is_auto_reply": is_auto,
        "classification": "auto_reply" if is_auto else "human_or_unknown",
        "confidence": "high" if auto_submitted and auto_submitted != "no" else ("medium" if reasons else "low"),
        "reasons": reasons,
        "observed_headers": {
            "auto_submitted": _text(msg.get("Auto-Submitted")),
            "precedence": _text(msg.get("Precedence")),
            "x_auto_response_suppress": _text(msg.get("X-Auto-Response-Suppress")),
        },
    }


def _status_classification(action: str, status: str, diagnostic: str) -> str:
    text = f"{action} {status} {diagnostic}".casefold()
    if "greylist" in text or "graylist" in text:
        return "greylisting"
    if "mailbox" in text and ("full" in text or "quota" in text):
        return "mailbox_full"
    if any(token in text for token in ("user unknown", "unknown user", "no such user", "recipient unknown")):
        return "user_unknown"
    if any(token in text for token in ("spam", "reputation", "blocklist", "blacklist")):
        return "spam_reputation_rejection"
    if any(token in text for token in ("policy", "prohibited", "not permitted")):
        return "policy_rejection"
    if action == "delayed":
        return "delayed"
    if action == "deferred":
        return "deferred"
    if status.startswith("5") or action == "failed":
        return "hard_bounce"
    if status.startswith("4"):
        return "soft_bounce"
    return "unknown"


def _message_text(msg: Message, limit: int = 20000) -> str:
    chunks: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() != "text":
            continue
        try:
            content = part.get_content()
            if isinstance(content, str):
                chunks.append(content)
        except Exception:
            payload = part.get_payload(decode=True) or b""
            chunks.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
        if sum(len(x) for x in chunks) >= limit:
            break
    return "\n".join(chunks)[:limit]


def _delivery_status_blocks(part: Message) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    payload = part.get_payload()
    if isinstance(payload, list):
        for block in payload:
            if isinstance(block, Message):
                blocks.append({str(k): _text(v) for k, v in block.items()})
    elif isinstance(payload, Message):
        blocks.append({str(k): _text(v) for k, v in payload.items()})
    elif isinstance(payload, str):
        current: dict[str, str] = {}
        for line in payload.replace("\r\n", "\n").split("\n"):
            if not line.strip():
                if current:
                    blocks.append(current)
                    current = {}
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                current[key.strip()] = value.strip()
        if current:
            blocks.append(current)
    return blocks


def _field(blocks: list[dict[str, str]], name: str) -> str:
    target = name.casefold()
    for block in blocks:
        for key, value in block.items():
            if key.casefold() == target and value:
                return value
    return ""


def parse_dsn_message(msg_or_raw: Message | bytes) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(msg_or_raw) if isinstance(msg_or_raw, (bytes, bytearray)) else msg_or_raw
    content_type = msg.get_content_type().casefold()
    report_type = _text(msg.get_param("report-type", header="content-type")).casefold()
    structured = content_type == "multipart/report" and report_type == "delivery-status"
    blocks: list[dict[str, str]] = []
    for part in msg.walk():
        if part.get_content_type().casefold() == "message/delivery-status":
            blocks.extend(_delivery_status_blocks(part))
            structured = True
    observed = {
        "final_recipient": _field(blocks, "Final-Recipient"),
        "original_recipient": _field(blocks, "Original-Recipient"),
        "action": _field(blocks, "Action").strip().lower(),
        "status": _field(blocks, "Status").strip(),
        "remote_mta": _field(blocks, "Remote-MTA"),
        "reporting_mta": _field(blocks, "Reporting-MTA"),
        "diagnostic_code": _field(blocks, "Diagnostic-Code"),
        "original_envelope_id": _field(blocks, "Original-Envelope-ID"),
        "original_message_id": _field(blocks, "Original-Message-ID"),
    }
    text = _message_text(msg)
    if not structured:
        status_match = re.search(r"\b([245]\.\d\.\d)\b", text)
        if status_match:
            observed["status"] = status_match.group(1)
        diagnostic_lines = [line.strip() for line in text.splitlines() if re.search(r"\b(?:4\d\d|5\d\d)\b", line)]
        if diagnostic_lines:
            observed["diagnostic_code"] = diagnostic_lines[0][:1000]
    classification = _status_classification(observed["action"], observed["status"], observed["diagnostic_code"] or text[:4000])
    textual_signal = classification != "unknown"
    recipient = observed["final_recipient"] or observed["original_recipient"]
    if ";" in recipient:
        recipient = recipient.split(";", 1)[1].strip()
    return {
        "is_dsn": structured or textual_signal,
        "structured": structured,
        "observed": observed,
        "derived": {"classification": classification, "recipient": recipient},
        "correlation": {
            "envelope_id": observed["original_envelope_id"],
            "message_id": observed["original_message_id"],
            "in_reply_to": _text(msg.get("In-Reply-To")),
            "references": _text(msg.get("References")),
        },
        "confidence": "high" if structured and observed["status"] else ("medium" if structured or textual_signal else "low"),
    }


def _auth_result_tokens(values: list[str]) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {"spf": [], "dkim": [], "dmarc": [], "arc": []}
    for value in values:
        low = value.casefold()
        for key in ("spf", "dkim", "dmarc", "arc"):
            for match in re.finditer(rf"\b{key}=([a-z0-9_-]+)", low):
                parsed[key].append(match.group(1))
    return parsed


def _part_structure(msg: Message) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, part in enumerate(msg.walk()):
        payload = part.get_payload(decode=True)
        rows.append({
            "index": index,
            "content_type": part.get_content_type(),
            "multipart": part.is_multipart(),
            "charset": part.get_content_charset(),
            "content_transfer_encoding": _text(part.get("Content-Transfer-Encoding")),
            "disposition": part.get_content_disposition(),
            "filename": part.get_filename() or "",
            "decoded_bytes": len(payload or b"") if not part.is_multipart() else None,
            "defects": [type(defect).__name__ for defect in getattr(part, "defects", ())],
        })
    return rows


def message_diagnostics(raw: bytes) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    auth_values = [_text(v) for v in msg.get_all("Authentication-Results", [])]
    arc_values: list[str] = []
    for name in ("ARC-Seal", "ARC-Message-Signature", "ARC-Authentication-Results"):
        arc_values.extend(_text(v) for v in msg.get_all(name, []))
    attachments: list[dict[str, Any]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if filename or part.get_content_disposition() == "attachment":
            blob = part.get_payload(decode=True) or b""
            attachments.append({"filename": filename or "", "content_type": part.get_content_type(), "size": len(blob)})
    list_headers = {name.lower(): _text(msg.get(name)) for name in ("List-Id", "List-Help", "List-Post", "List-Subscribe", "List-Unsubscribe", "List-Unsubscribe-Post") if msg.get(name) is not None}
    spam_headers = {name: _text(value) for name, value in msg.items() if any(token in name.casefold() for token in ("spam", "junk"))}
    return {
        "message_size": len(raw),
        "message_id": _text(msg.get("Message-ID")),
        "in_reply_to": _text(msg.get("In-Reply-To")),
        "references": _text(msg.get("References")),
        "return_path": _text(msg.get("Return-Path")),
        "received_chain": [_text(v) for v in msg.get_all("Received", [])],
        "authentication_results": auth_values,
        "authentication_summary": _auth_result_tokens(auth_values),
        "arc_headers": arc_values,
        "mime_structure": _part_structure(msg),
        "attachments": attachments,
        "list_headers": list_headers,
        "auto_reply": detect_auto_reply(msg),
        "recipient_addresses": [addr for _, addr in getaddresses([_text(msg.get("To")), _text(msg.get("Cc")), _text(msg.get("Delivered-To"))]) if addr],
        "provider_specific_spam_headers": spam_headers,
        "defects": [type(defect).__name__ for defect in getattr(msg, "defects", ())],
    }


def compare_message_diagnostics(sent_raw: bytes, received_raw: bytes) -> dict[str, Any]:
    sent = BytesParser(policy=policy.default).parsebytes(sent_raw)
    received = BytesParser(policy=policy.default).parsebytes(received_raw)
    def headers(message: Message) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, value in message.items():
            out.setdefault(name.casefold(), []).append(_text(value))
        return out
    left = headers(sent)
    right = headers(received)
    sent_diag = message_diagnostics(sent_raw)
    received_diag = message_diagnostics(received_raw)
    return {
        "headers_added": sorted(set(right) - set(left)),
        "headers_removed": sorted(set(left) - set(right)),
        "headers_changed": sorted(name for name in set(left) & set(right) if left[name] != right[name]),
        "message_id_preserved": sent_diag["message_id"] == received_diag["message_id"],
        "sent_size": len(sent_raw),
        "received_size": len(received_raw),
        "sent_mime_structure": sent_diag["mime_structure"],
        "received_mime_structure": received_diag["mime_structure"],
        "authentication_results_received": received_diag["authentication_results"],
        "dkim_observed_after_delivery": bool(received_diag["authentication_summary"]["dkim"]),
    }
