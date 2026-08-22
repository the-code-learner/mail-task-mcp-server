from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urljoin


class UnsubscribeError(RuntimeError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise UnsubscribeError("invalid unsubscribe token") from exc


def _canonical_public_base_url() -> str:
    raw = (os.getenv("PUBLIC_EMAIL_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    host = (os.getenv("PUBLIC_MCP_HOST") or "").strip().rstrip("/")
    if host:
        return f"https://{host}"
    return ""


class UnsubscribeManager:
    """Signed capability tokens for delivery-specific unsubscribe actions."""

    def __init__(self, *, key_path: str | None = None, public_base_url: str | None = None) -> None:
        self.key_path = Path(key_path or os.getenv("UNSUBSCRIBE_KEY_PATH", "/data/unsubscribe.key"))
        self.public_base_url = (
            public_base_url.strip().rstrip("/")
            if public_base_url is not None
            else _canonical_public_base_url()
        )
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            data = self.key_path.read_bytes().strip()
            if len(data) < 32:
                raise UnsubscribeError("unsubscribe signing key is too short")
            return data
        data = secrets.token_bytes(48)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(str(self.key_path), flags, 0o600)
        except FileExistsError:
            return self.key_path.read_bytes().strip()
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return data

    def _require_public_https(self) -> str:
        base = self.public_base_url
        if not base.lower().startswith("https://") or len(base) <= len("https://"):
            raise UnsubscribeError(
                "automatic unsubscribe requires PUBLIC_EMAIL_BASE_URL or PUBLIC_MCP_HOST with HTTPS"
            )
        return base

    def sign_delivery(self, delivery_id: str) -> str:
        delivery = (delivery_id or "").strip()
        if not delivery or len(delivery) > 240 or any(ch in delivery for ch in "\r\n/\\"):
            raise UnsubscribeError("invalid delivery id")
        payload = _b64encode(delivery.encode("utf-8"))
        signature = _b64encode(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())
        return payload + "." + signature

    def resolve(self, token: str) -> str:
        raw = (token or "").strip()
        if len(raw) > 600 or "." not in raw:
            raise UnsubscribeError("invalid unsubscribe token")
        payload, signature = raw.split(".", 1)
        expected = _b64encode(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise UnsubscribeError("invalid unsubscribe token")
        delivery = _b64decode(payload).decode("utf-8", errors="strict")
        if not delivery or len(delivery) > 240:
            raise UnsubscribeError("invalid unsubscribe token")
        return delivery

    def url_for_delivery(self, delivery_id: str) -> str:
        base = self._require_public_https()
        token = self.sign_delivery(delivery_id)
        return urljoin(base + "/", "unsubscribe/" + token)

    def placeholder_url(self) -> str:
        base = self._require_public_https()
        return urljoin(base + "/", "unsubscribe/{{DELIVERY_ID}}")
