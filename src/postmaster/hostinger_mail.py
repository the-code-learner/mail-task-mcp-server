from __future__ import annotations

import imaplib
import os
import re
import smtplib
import ssl
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Iterable, Iterator


class MailBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    email_address: str
    email_password: str
    imap_host: str = "imap.hostinger.com"
    imap_port: int = 993
    smtp_host: str = "smtp.hostinger.com"
    smtp_port: int = 465
    smtp_starttls: bool = False
    enable_send: bool = False
    max_message_bytes: int = 2_000_000
    search_candidate_limit: int = 500
    sent_mailbox: str = "Sent"
    save_sent_copy: bool = True
    send_recipient_allowlist: tuple[str, ...] = ()
    allow_previous_sent_recipients: bool = True
    contact_history_limit: int = 5000
    account_id: str = ""
    imap_security: str = "ssl"
    imap_username: str = ""
    imap_password: str = ""
    smtp_security: str = "ssl"
    smtp_username: str = ""
    smtp_password: str = ""
    draft_mailbox: str = "Drafts"
    inbox_mailbox: str = "INBOX"
    junk_mailbox: str = "Junk"
    amp_enabled: bool = False
    amp_registered: bool = False
    tracking_default: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        address = os.getenv("HOSTINGER_EMAIL", "").strip()
        password = os.getenv("HOSTINGER_PASSWORD", "")
        if not address or not password:
            raise MailBridgeError(
                "HOSTINGER_EMAIL and HOSTINGER_PASSWORD must be set in the environment."
            )
        allowlist = tuple(
            x.strip().lower()
            for x in os.getenv("SEND_RECIPIENT_ALLOWLIST", "").split(",")
            if x.strip()
        )
        smtp_starttls = _env_bool("SMTP_STARTTLS", False)
        return cls(
            email_address=address,
            email_password=password,
            imap_host=os.getenv("IMAP_HOST", "imap.hostinger.com").strip(),
            imap_port=int(os.getenv("IMAP_PORT", "993")),
            smtp_host=os.getenv("SMTP_HOST", "smtp.hostinger.com").strip(),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_starttls=smtp_starttls,
            enable_send=_env_bool("ENABLE_SEND", False),
            max_message_bytes=int(os.getenv("MAX_MESSAGE_BYTES", "2000000")),
            search_candidate_limit=int(os.getenv("SEARCH_CANDIDATE_LIMIT", "500")),
            sent_mailbox=os.getenv("SENT_MAILBOX", "Sent").strip() or "Sent",
            save_sent_copy=_env_bool("SAVE_SENT_COPY", True),
            send_recipient_allowlist=allowlist,
            allow_previous_sent_recipients=_env_bool("ALLOW_PREVIOUS_SENT_RECIPIENTS", True),
            contact_history_limit=max(1, min(int(os.getenv("CONTACT_HISTORY_LIMIT", "5000")), 20000)),
            account_id="legacy-env",
            imap_security="ssl",
            imap_username=address,
            imap_password=password,
            smtp_security="starttls" if smtp_starttls else "ssl",
            smtp_username=address,
            smtp_password=password,
            draft_mailbox=os.getenv("DRAFT_MAILBOX", "Drafts").strip() or "Drafts",
            inbox_mailbox=os.getenv("INBOX_MAILBOX", "INBOX").strip() or "INBOX",
            junk_mailbox=os.getenv("JUNK_MAILBOX", "Junk").strip() or "Junk",
        )

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


class _HTMLTextExtractor(HTMLParser):
    """Small stdlib HTML-to-text converter that preserves link targets."""

    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "div", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._links: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br" or tag in self._BLOCK_TAGS:
            self._parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._links.append((unescape(href).strip(), len(self._parts)))
        elif tag == "img":
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                self._parts.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._links:
            href, start = self._links.pop()
            visible = "".join(self._parts[start:]).strip()
            if href and href not in visible:
                self._parts.append(f" ({href})")
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        text = "".join(self._parts).replace("\x00", "")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
        return parser.text()
    except Exception:
        # Keep a conservative fallback for malformed real-world email HTML.
        text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
        text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _decode_text_part(part: Message) -> str:
    try:
        return str(part.get_content())
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")


def _meaningful_length(text: str) -> int:
    return sum(1 for char in text if char.isalnum())


def _normalized_probe(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _forwarded_headers(msg: Message) -> str:
    rows: list[str] = ["Forwarded message"]
    for label, header in (("From", "From"), ("To", "To"), ("Cc", "Cc"), ("Subject", "Subject"), ("Date", "Date")):
        value = _decode_header(msg.get(header))
        if value:
            rows.append(f"{label}: {value}")
    return "\n".join(rows)


def _collect_body_parts(
    msg: Message,
    plain_parts: list[str],
    html_parts: list[str],
    forwarded_headers: list[str],
    *,
    nested: bool = False,
) -> None:
    content_type = msg.get_content_type().casefold()

    if content_type == "message/rfc822":
        payload = msg.get_payload()
        nested_messages: list[Message] = []
        if isinstance(payload, list):
            nested_messages.extend(item for item in payload if isinstance(item, Message))
        elif isinstance(payload, Message):
            nested_messages.append(payload)
        elif isinstance(payload, bytes):
            nested_messages.append(BytesParser(policy=policy.default).parsebytes(payload))
        elif isinstance(payload, str) and payload.strip():
            nested_messages.append(BytesParser(policy=policy.default).parsebytes(payload.encode("utf-8", errors="replace")))
        for child in nested_messages:
            forwarded_headers.append(_forwarded_headers(child))
            _collect_body_parts(child, plain_parts, html_parts, forwarded_headers, nested=True)
        return

    if msg.is_multipart():
        payload = msg.get_payload()
        if isinstance(payload, list):
            for part in payload:
                if not isinstance(part, Message):
                    continue
                # Ordinary attachments are not body text. A message/rfc822 part is
                # intentionally traversed even when an email client labels it as an attachment.
                if part.get_content_disposition() == "attachment" and part.get_content_type().casefold() != "message/rfc822":
                    continue
                _collect_body_parts(part, plain_parts, html_parts, forwarded_headers, nested=nested)
        return

    if msg.get_content_disposition() == "attachment":
        return
    if content_type == "text/plain":
        value = _decode_text_part(msg).replace("\x00", "").strip()
        if value:
            plain_parts.append(value)
    elif content_type == "text/html":
        value = _decode_text_part(msg).replace("\x00", "").strip()
        if value:
            html_parts.append(value)


def _extract_body_content(msg: Message) -> dict[str, str | int]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    forwarded_headers: list[str] = []
    _collect_body_parts(msg, plain_parts, html_parts, forwarded_headers)

    plain_text = "\n\n".join(x for x in plain_parts if x).strip()
    html_raw = "\n\n<!-- postmaster:mime-part -->\n\n".join(html_parts).strip()
    html_text = "\n\n".join(_strip_html(x) for x in html_parts if x).strip()
    plain_score = _meaningful_length(plain_text)
    html_score = _meaningful_length(html_text)

    # Do not blindly prefer text/plain. Some forwarding clients (notably Libero
    # Mail) put only their tiny signature in text/plain while the complete
    # forwarded message remains in text/html.
    html_is_materially_richer = bool(html_text) and (
        not plain_text
        or (plain_score < 160 and html_score >= max(100, plain_score * 2.5))
        or (html_score >= plain_score * 2.5 and html_score - plain_score >= 300)
    )

    if html_is_materially_richer:
        body = html_text
        source = "html-rich" if plain_text else "html"
        probe_plain = _normalized_probe(plain_text)
        probe_html = _normalized_probe(html_text)
        # Preserve a short outer forwarding note when it is not already present
        # in the HTML body, while avoiding duplication of equivalent alternatives.
        if plain_text and plain_score <= 300 and probe_plain and probe_plain not in probe_html:
            body = f"{plain_text}\n\n{body}".strip()
    elif plain_text:
        body = plain_text
        source = "plain"
    elif html_text:
        body = html_text
        source = "html"
    else:
        body = ""
        source = "none"

    if forwarded_headers:
        header_text = "\n\n".join(forwarded_headers).strip()
        if header_text and _normalized_probe(header_text) not in _normalized_probe(body):
            body = f"{header_text}\n\n{body}".strip()

    return {
        "body": body.replace("\x00", "").strip(),
        "body_html": html_raw,
        "body_source": source,
        "plain_significant_chars": plain_score,
        "html_significant_chars": html_score,
        "forwarded_message_count": len(forwarded_headers),
    }


def _extract_body(msg: Message) -> str:
    """Backwards-compatible internal helper returning the selected text body."""
    return str(_extract_body_content(msg)["body"])


def _attachment_names(msg: Message) -> list[str]:
    result: list[str] = []
    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            result.append(_decode_header(filename))
    return result


def _parsed_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return value


def _address_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _, addr in getaddresses([value]) if addr]


def _message_to_dict(
    msg: Message,
    *,
    uid: str,
    mailbox: str,
    include_body: bool,
    truncated: bool = False,
) -> dict:
    extracted = _extract_body_content(msg) if include_body else {
        "body": "",
        "body_html": "",
        "body_source": "headers-only" if truncated else "none",
        "plain_significant_chars": 0,
        "html_significant_chars": 0,
        "forwarded_message_count": 0,
    }
    return {
        "mailbox": mailbox,
        "uid": uid,
        "message_id": msg.get("Message-ID", ""),
        "in_reply_to": msg.get("In-Reply-To", ""),
        "references": msg.get("References", ""),
        "date": _parsed_date(msg.get("Date")),
        "from": _decode_header(msg.get("From")),
        "from_addresses": _address_list(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "to_addresses": _address_list(msg.get("To")),
        "cc": _decode_header(msg.get("Cc")),
        "cc_addresses": _address_list(msg.get("Cc")),
        "subject": _decode_header(msg.get("Subject")),
        "body": extracted["body"],
        "body_html": extracted["body_html"],
        "body_source": extracted["body_source"],
        "body_plain_significant_chars": extracted["plain_significant_chars"],
        "body_html_significant_chars": extracted["html_significant_chars"],
        "forwarded_message_count": extracted["forwarded_message_count"],
        "attachments": _attachment_names(msg),
        "content_truncated": truncated,
    }


class HostingerMailClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def _imap(self) -> Iterator[imaplib.IMAP4]:
        context = ssl.create_default_context()
        security = (self.settings.imap_security or "ssl").strip().lower()
        username = self.settings.imap_username or self.settings.email_address
        password = self.settings.imap_password or self.settings.email_password

        if security == "ssl":
            conn = imaplib.IMAP4_SSL(
                self.settings.imap_host,
                self.settings.imap_port,
                ssl_context=context,
            )
        else:
            conn = imaplib.IMAP4(self.settings.imap_host, self.settings.imap_port)
            if security == "starttls":
                typ, _ = conn.starttls(ssl_context=context)
                if typ != "OK":
                    raise MailBridgeError("IMAP STARTTLS failed")
            elif security != "plain":
                raise MailBridgeError(f"Unsupported IMAP security mode: {security}")
        try:
            typ, _ = conn.login(username, password)
            if typ != "OK":
                raise MailBridgeError("IMAP login failed")
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def ping(self) -> dict:
        with self._imap() as conn:
            typ, _ = conn.noop()
            if typ != "OK":
                raise MailBridgeError("IMAP NOOP failed")
            return {
                "ok": True,
                "account_id": self.settings.account_id,
                "account": self.settings.email_address,
                "imap_host": self.settings.imap_host,
                "imap_security": self.settings.imap_security,
                "smtp_host": self.settings.smtp_host,
                "smtp_security": self.settings.smtp_security,
                "send_enabled": self.settings.enable_send,
            }

    def test_connections(self) -> dict:
        """Authenticate to IMAP and SMTP without sending a message."""
        result = self.ping()
        context = ssl.create_default_context()
        security = (
            self.settings.smtp_security
            or ("starttls" if self.settings.smtp_starttls else "ssl")
        ).strip().lower()
        username = self.settings.smtp_username or self.settings.email_address
        password = self.settings.smtp_password or self.settings.email_password

        if security == "ssl":
            smtp = smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
        try:
            smtp.ehlo()
            if security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            elif security not in {"plain", "ssl"}:
                raise MailBridgeError(f"Unsupported SMTP security mode: {security}")
            smtp.login(username, password)
            result["smtp_login"] = True
            return result
        finally:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass

    def list_mailboxes(self) -> list[str]:
        with self._imap() as conn:
            typ, data = conn.list()
            if typ != "OK":
                raise MailBridgeError("Could not list IMAP mailboxes")
            result: list[str] = []
            for raw in data or []:
                if not raw:
                    continue
                line = raw.decode(errors="replace")
                # Last token is normally the quoted mailbox name.
                match = re.search(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*$', line)
                if match:
                    name = match.group(1).replace(r'\"', '"')
                else:
                    name = line.rsplit(" ", 1)[-1].strip('"')
                result.append(name)
            return result

    def _select(self, conn: imaplib.IMAP4_SSL, mailbox: str, readonly: bool = True) -> None:
        typ, _ = conn.select(mailbox, readonly=readonly)
        if typ != "OK":
            raise MailBridgeError(f"Could not select mailbox: {mailbox}")

    def _fetch_raw(self, conn: imaplib.IMAP4_SSL, uid: str) -> tuple[bytes, bool]:
        typ, size_data = conn.uid("FETCH", uid, "(RFC822.SIZE)")
        if typ != "OK" or not size_data:
            raise MailBridgeError(f"Could not fetch size for UID {uid}")
        size = None
        for item in size_data:
            if isinstance(item, bytes):
                match = re.search(rb"RFC822\.SIZE\s+(\d+)", item)
                if match:
                    size = int(match.group(1))
                    break
        if size is not None and size > self.settings.max_message_bytes:
            typ, header_data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
            if typ != "OK" or not header_data:
                raise MailBridgeError(f"Could not fetch headers for UID {uid}")
            for item in header_data:
                if isinstance(item, tuple) and isinstance(item[1], bytes):
                    return item[1], True
            raise MailBridgeError(f"Could not parse header fetch for UID {uid}")

        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data:
            raise MailBridgeError(f"Could not fetch UID {uid}")
        for item in data:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                return item[1], False
        raise MailBridgeError(f"Could not parse UID {uid}")

    def _fetch_headers(self, conn: imaplib.IMAP4_SSL, uid: str) -> bytes:
        typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not data:
            raise MailBridgeError(f"Could not fetch headers for UID {uid}")
        for item in data:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                return item[1]
        raise MailBridgeError(f"Could not parse headers for UID {uid}")

    def get_email(self, mailbox: str, uid: str) -> dict:
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            raw, truncated = self._fetch_raw(conn, uid)
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            return _message_to_dict(
                msg,
                uid=uid,
                mailbox=mailbox,
                include_body=not truncated,
                truncated=truncated,
            )

    def search_emails(
        self,
        *,
        mailbox: str = "INBOX",
        from_address: str | None = None,
        to_address: str | None = None,
        subject: str | None = None,
        text: str | None = None,
        since_days: int = 90,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        if not 1 <= limit <= 100:
            raise MailBridgeError("limit must be between 1 and 100")
        since_days = max(0, min(since_days, 3650))
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            criteria = ["ALL"]
            if unread_only:
                criteria.append("UNSEEN")
            typ, data = conn.uid("SEARCH", None, *criteria)
            if typ != "OK" or not data:
                raise MailBridgeError("IMAP search failed")
            uids = data[0].decode().split()
            # Most recent UIDs first. Only headers are fetched until a candidate matches.
            uids = list(reversed(uids[-self.settings.search_candidate_limit :]))

            filters = {
                "from": (from_address or "").casefold().strip(),
                "to": (to_address or "").casefold().strip(),
                "subject": (subject or "").casefold().strip(),
                "text": (text or "").casefold().strip(),
            }
            results: list[dict] = []
            for uid in uids:
                header_raw = self._fetch_headers(conn, uid)
                header_msg = BytesParser(policy=policy.default).parsebytes(header_raw)
                header_row = _message_to_dict(
                    header_msg,
                    uid=uid,
                    mailbox=mailbox,
                    include_body=False,
                    truncated=False,
                )

                date_value = header_row.get("date")
                if date_value:
                    try:
                        dt = datetime.fromisoformat(date_value)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            continue
                    except Exception:
                        pass

                if filters["from"] and filters["from"] not in header_row["from"].casefold():
                    continue
                if filters["to"] and filters["to"] not in header_row["to"].casefold():
                    continue
                if filters["subject"] and filters["subject"] not in header_row["subject"].casefold():
                    continue

                # Fetch the body only for header-matched candidates, keeping normal searches cheap.
                raw, truncated = self._fetch_raw(conn, uid)
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                row = _message_to_dict(
                    msg,
                    uid=uid,
                    mailbox=mailbox,
                    include_body=not truncated,
                    truncated=truncated,
                )

                if filters["text"]:
                    haystack = "\n".join(
                        [row["subject"], row["from"], row["to"], row["body"]]
                    ).casefold()
                    if filters["text"] not in haystack:
                        continue

                body = row.pop("body", "")
                row["snippet"] = re.sub(r"\s+", " ", body).strip()[:600]
                results.append(row)
                if len(results) >= limit:
                    break

            return results

    def _historical_sent_addresses(self) -> set[str]:
        # Exact addresses previously used in Sent become a dynamic, local allowlist.
        # This avoids having to hard-code every historical sponsorship contact.
        cached = getattr(self, "_sent_addresses_cache", None)
        if cached is not None:
            return cached

        addresses: set[str] = set()
        with self._imap() as conn:
            self._select(conn, self.settings.sent_mailbox, readonly=True)
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK" or not data:
                self._sent_addresses_cache = addresses
                return addresses
            uids = data[0].decode().split()[-self.settings.contact_history_limit :]
            for uid in uids:
                try:
                    header_raw = self._fetch_headers(conn, uid)
                    msg = BytesParser(policy=policy.default).parsebytes(header_raw)
                    for header in ("To", "Cc", "Bcc"):
                        for _, addr in getaddresses([msg.get(header, "")]):
                            addr = addr.strip().lower()
                            if addr and "@" in addr:
                                addresses.add(addr)
                except Exception:
                    continue

        self._sent_addresses_cache = addresses
        return addresses

    def list_known_contacts(self) -> dict:
        addresses = sorted(self._historical_sent_addresses())
        own = self.settings.email_address.strip().lower()
        addresses = [a for a in addresses if a != own]
        domains = sorted({a.rsplit("@", 1)[1] for a in addresses if "@" in a})
        return {
            "addresses": addresses,
            "domains": domains,
            "address_count": len(addresses),
            "domain_count": len(domains),
            "history_limit": self.settings.contact_history_limit,
        }

    def _validate_recipients(self, recipients: Iterable[str]) -> list[str]:
        cleaned: list[str] = []
        historical = None
        for value in recipients:
            _, addr = parseaddr(value)
            if not addr or "@" not in addr:
                raise MailBridgeError(f"Invalid recipient address: {value}")
            addr = addr.strip()
            addr_lc = addr.lower()
            domain = addr_lc.rsplit("@", 1)[1]

            domain_allowed = any(
                domain == allowed or domain.endswith("." + allowed)
                for allowed in self.settings.send_recipient_allowlist
            )

            previous_allowed = False
            if not domain_allowed and self.settings.allow_previous_sent_recipients:
                if historical is None:
                    historical = self._historical_sent_addresses()
                previous_allowed = addr_lc in historical

            if self.settings.send_recipient_allowlist or self.settings.allow_previous_sent_recipients:
                if not domain_allowed and not previous_allowed:
                    raise MailBridgeError(
                        f"Recipient {addr} is neither in SEND_RECIPIENT_ALLOWLIST nor in historical Sent recipients"
                    )
            cleaned.append(addr)
        if not cleaned:
            raise MailBridgeError("At least one recipient is required")
        return cleaned

    def _send_message(self, msg: EmailMessage, recipients: list[str]) -> dict:
        if not self.settings.enable_send:
            raise MailBridgeError(
                "Sending is disabled. Set ENABLE_SEND=true only when you are ready to allow SMTP writes."
            )

        if "Date" not in msg:
            msg["Date"] = format_datetime(datetime.now().astimezone())
        if "Message-ID" not in msg:
            domain = self.settings.email_address.rsplit("@", 1)[-1]
            msg["Message-ID"] = make_msgid(domain=domain)

        context = ssl.create_default_context()
        security = (
            self.settings.smtp_security
            or ("starttls" if self.settings.smtp_starttls else "ssl")
        ).strip().lower()
        username = self.settings.smtp_username or self.settings.email_address
        password = self.settings.smtp_password or self.settings.email_password

        if security == "ssl":
            with smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
                context=context,
            ) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg, from_addr=self.settings.email_address, to_addrs=recipients)
        else:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                if security == "starttls":
                    smtp.starttls(context=context)
                    smtp.ehlo()
                elif security != "plain":
                    raise MailBridgeError(f"Unsupported SMTP security mode: {security}")
                smtp.login(username, password)
                smtp.send_message(msg, from_addr=self.settings.email_address, to_addrs=recipients)

        # The recipient set may have changed after this send.
        if hasattr(self, "_sent_addresses_cache"):
            delattr(self, "_sent_addresses_cache")

        sent_copy_saved = False
        sent_copy_error = None
        if self.settings.save_sent_copy:
            try:
                with self._imap() as conn:
                    typ, _ = conn.append(
                        self.settings.sent_mailbox,
                        r"\Seen",
                        imaplib.Time2Internaldate(datetime.now().timestamp()),
                        msg.as_bytes(policy=policy.SMTP),
                    )
                    sent_copy_saved = typ == "OK"
                    if not sent_copy_saved:
                        sent_copy_error = "IMAP APPEND returned non-OK"
            except Exception as exc:
                # SMTP has already succeeded; do not report the send as failed.
                sent_copy_error = type(exc).__name__

        return {
            "sent": True,
            "from": self.settings.email_address,
            "to": recipients,
            "subject": str(msg.get("Subject", "")),
            "message_id": str(msg.get("Message-ID", "")),
            "sent_copy_saved": sent_copy_saved,
            "sent_copy_error": sent_copy_error,
        }

    def send_email(
        self,
        *,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
    ) -> dict:
        to_clean = self._validate_recipients(to)
        cc_clean = self._validate_recipients(cc or []) if cc else []
        all_recipients = list(dict.fromkeys(to_clean + cc_clean))

        msg = EmailMessage()
        msg["From"] = self.settings.email_address
        msg["To"] = ", ".join(to_clean)
        if cc_clean:
            msg["Cc"] = ", ".join(cc_clean)
        msg["Subject"] = subject.strip()
        msg.set_content(body)
        return self._send_message(msg, all_recipients)

    def reply_email(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str,
        cc: list[str] | None = None,
    ) -> dict:
        original = self.get_email(mailbox, uid)
        from_addresses = original.get("from_addresses") or []
        if not from_addresses:
            raise MailBridgeError("Original email has no valid From address")
        recipient = from_addresses[0]
        to_clean = self._validate_recipients([recipient])
        cc_clean = self._validate_recipients(cc or []) if cc else []

        subject = original.get("subject", "") or ""
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = EmailMessage()
        msg["From"] = self.settings.email_address
        msg["To"] = to_clean[0]
        if cc_clean:
            msg["Cc"] = ", ".join(cc_clean)
        msg["Subject"] = subject
        if original.get("message_id"):
            msg["In-Reply-To"] = original["message_id"]
            refs = original.get("references", "").strip()
            msg["References"] = (refs + " " + original["message_id"]).strip()
        msg.set_content(body)
        return self._send_message(msg, list(dict.fromkeys(to_clean + cc_clean)))
