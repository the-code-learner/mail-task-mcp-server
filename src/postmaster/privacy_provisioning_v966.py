from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .email_privacy_v963 import PrivacyProxyStore


PROVISIONING_CLOCK_SKEW_SECONDS = 300
PROVISIONING_CONFIRM_TTL_SECONDS = 120
PREVIOUS_SECRET_GRACE_SECONDS = 120
MAX_PREVIOUS_SECRET_GRACE_SECONDS = 300
PROVISIONING_PATH = "/provision"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _origin(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme.casefold() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Privacy Proxy Worker URL must be an absolute HTTPS URL without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Privacy Proxy Worker URL must not contain a query string or fragment")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = parsed.port in (None, 443)
    netloc = host if default_port else f"{host}:{parsed.port}"
    return f"https://{netloc}"


def _endpoint(worker_url: str) -> tuple[str, str, str]:
    parsed = urlparse((worker_url or "").strip())
    origin = _origin(worker_url)
    base_path = (parsed.path or "").rstrip("/")
    path = f"{base_path}{PROVISIONING_PATH}" if base_path else PROVISIONING_PATH
    return origin, path, f"{origin}{path}"


def _assert_public_worker(worker_url: str) -> None:
    parsed = urlparse(worker_url)
    host = parsed.hostname or ""
    if host.casefold() in {"localhost", "localhost.localdomain"} or host.casefold().endswith(".localhost"):
        raise ValueError("Privacy Proxy Worker host is not public")
    try:
        direct = ipaddress.ip_address(host)
    except ValueError:
        direct = None
    if direct is not None:
        if not direct.is_global:
            raise ValueError("Privacy Proxy Worker host is not public")
        return
    answers = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("Privacy Proxy Worker host has no DNS address")
    for row in answers:
        address = str(row[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Privacy Proxy Worker DNS answer is invalid") from exc
        if not ip.is_global:
            raise ValueError("Privacy Proxy Worker DNS resolved to a non-public address")


def provisioning_canonical(
    *,
    method: str,
    path: str,
    origin: str,
    timestamp: str,
    nonce: str,
    body_digest: str,
    generation: int,
    operation: str,
    key_id: str,
) -> bytes:
    return "\n".join(
        [
            method.upper(),
            path,
            origin,
            timestamp,
            nonce,
            str(int(generation)),
            operation,
            key_id,
            body_digest,
        ]
    ).encode("utf-8")


class ConfirmationTokens:
    """Short-lived one-time tokens bound to an exact provisioning preview."""

    def __init__(self, ttl_seconds: int = PROVISIONING_CONFIRM_TTL_SECONDS) -> None:
        self.ttl_seconds = max(30, min(int(ttl_seconds), 300))
        self._lock = threading.RLock()
        self._tokens: dict[str, tuple[float, str]] = {}

    @staticmethod
    def _binding_digest(binding: dict[str, Any]) -> str:
        return hashlib.sha256(_json_bytes(binding)).hexdigest()

    def issue(self, binding: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        digest = self._binding_digest(binding)
        now = time.time()
        with self._lock:
            self._tokens = {
                key: value for key, value in self._tokens.items() if value[0] >= now
            }
            self._tokens[token] = (now + self.ttl_seconds, digest)
        return token

    def consume(self, token: str, binding: dict[str, Any]) -> bool:
        now = time.time()
        expected = self._binding_digest(binding)
        with self._lock:
            row = self._tokens.pop(str(token or ""), None)
        return bool(row and row[0] >= now and hmac.compare_digest(row[1], expected))


@dataclass(frozen=True)
class PendingSecret:
    generation: int
    operation: str
    secret: str


class PrivacyProxyProvisioning:
    """MCP-native Privacy Proxy provisioning without exposing private material."""

    def __init__(
        self,
        store: PrivacyProxyStore,
        *,
        timeout_seconds: float = 10.0,
        confirmations: ConfirmationTokens | None = None,
    ) -> None:
        self.store = store
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))
        self.confirmations = confirmations or ConfirmationTokens()
        self._init_db()

    def _init_db(self) -> None:
        with self.store._lock, self.store._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_proxy_provisioning (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    private_key_enc TEXT NOT NULL DEFAULT '',
                    public_key_b64 TEXT NOT NULL DEFAULT '',
                    key_id TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL DEFAULT '',
                    generation INTEGER NOT NULL DEFAULT 0,
                    provisioned INTEGER NOT NULL DEFAULT 0,
                    pending_secret_enc TEXT NOT NULL DEFAULT '',
                    pending_generation INTEGER,
                    pending_operation TEXT NOT NULL DEFAULT '',
                    pending_created_at INTEGER,
                    updated_at INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO privacy_proxy_provisioning(singleton) VALUES(1);
                """
            )

    def _row(self) -> dict[str, Any]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM privacy_proxy_provisioning WHERE singleton=1"
            ).fetchone()
        return dict(row or {})

    def public_status(self) -> dict[str, Any]:
        row = self._row()
        prepared = bool(row.get("private_key_enc") and row.get("public_key_b64") and row.get("key_id"))
        pending = bool(row.get("pending_secret_enc") and row.get("pending_generation"))
        provisioned = bool(row.get("provisioned"))
        if pending:
            phase = "pending"
        elif provisioned:
            phase = "active"
        elif prepared:
            phase = "prepared"
        else:
            phase = "unprepared"
        return {
            "mode": "mcp_native_ed25519",
            "phase": phase,
            "prepared": prepared,
            "public_key": str(row.get("public_key_b64") or ""),
            "key_id": str(row.get("key_id") or ""),
            "fingerprint": str(row.get("fingerprint") or ""),
            "generation": int(row.get("generation") or 0),
            "provisioned": provisioned,
            "pending": pending,
            "pending_generation": int(row.get("pending_generation") or 0) if pending else None,
            "pending_operation": str(row.get("pending_operation") or "") if pending else "",
            "confirmation_ttl_seconds": self.confirmations.ttl_seconds,
            "previous_secret_grace_seconds": PREVIOUS_SECRET_GRACE_SECONDS,
            "legacy_secret_fallback_supported": True,
        }

    def _prepare_key(self) -> dict[str, Any]:
        row = self._row()
        if row.get("private_key_enc") and row.get("public_key_b64"):
            return self.public_status()
        private_key = Ed25519PrivateKey.generate()
        private_raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_raw = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        digest = hashlib.sha256(public_raw).hexdigest()
        public_key = _b64url(public_raw)
        key_id = f"pm-ed25519-{digest[:16]}"
        fingerprint = f"sha256:{digest}"
        private_enc = self.store._fernet.encrypt(private_raw).decode("ascii")
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                """
                UPDATE privacy_proxy_provisioning
                SET private_key_enc=?,public_key_b64=?,key_id=?,fingerprint=?,updated_at=?
                WHERE singleton=1
                """,
                (private_enc, public_key, key_id, fingerprint, int(time.time())),
            )
        return self.public_status()

    def _private_key(self) -> Ed25519PrivateKey:
        row = self._row()
        encoded = str(row.get("private_key_enc") or "")
        if not encoded:
            raise RuntimeError("provisioning_signing_key_not_prepared")
        try:
            raw = self.store._fernet.decrypt(encoded.encode("ascii"))
            return Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise RuntimeError("provisioning_signing_key_unavailable") from exc

    def _pending(self) -> PendingSecret | None:
        row = self._row()
        encoded = str(row.get("pending_secret_enc") or "")
        generation = int(row.get("pending_generation") or 0)
        operation = str(row.get("pending_operation") or "")
        if not encoded or generation <= 0 or operation not in {"provision", "rotate"}:
            return None
        try:
            secret = self.store._fernet.decrypt(encoded.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("pending_proxy_secret_unavailable") from exc
        return PendingSecret(generation=generation, operation=operation, secret=secret)

    def _save_pending(self, generation: int, operation: str, secret: str) -> None:
        encoded = self.store._fernet.encrypt(secret.encode("utf-8")).decode("ascii")
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                """
                UPDATE privacy_proxy_provisioning
                SET pending_secret_enc=?,pending_generation=?,pending_operation=?,
                    pending_created_at=?,updated_at=?
                WHERE singleton=1
                """,
                (encoded, int(generation), operation, int(time.time()), int(time.time())),
            )

    def _promote(self, pending: PendingSecret) -> dict[str, Any]:
        cfg = self.store.status()
        self.store.configure(
            secret=pending.secret,
            enabled=bool(cfg.get("enabled")),
        )
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                """
                UPDATE privacy_proxy_provisioning
                SET generation=?,provisioned=1,pending_secret_enc='',pending_generation=NULL,
                    pending_operation='',pending_created_at=NULL,updated_at=?
                WHERE singleton=1
                """,
                (pending.generation, int(time.time())),
            )
        return self.public_status()

    def _finish_deprovision(self, generation: int) -> dict[str, Any]:
        self.store.configure(secret="", enabled=False)
        with self.store._lock, self.store._connect() as conn:
            conn.execute(
                """
                UPDATE privacy_proxy_provisioning
                SET generation=?,provisioned=0,pending_secret_enc='',pending_generation=NULL,
                    pending_operation='',pending_created_at=NULL,updated_at=?
                WHERE singleton=1
                """,
                (int(generation), int(time.time())),
            )
        return self.public_status()

    def _binding(self, action: str, worker_url: str | None = None) -> dict[str, Any]:
        state = self.public_status()
        normalized_url = (worker_url if worker_url is not None else self.store.status().get("worker_url") or "").strip()
        origin = ""
        if normalized_url:
            origin = _origin(normalized_url)
        action = action.strip().lower()
        if action == "prepare_provisioning":
            generation = state["generation"]
        elif action == "provision":
            if not state["prepared"]:
                raise ValueError("prepare_provisioning must complete before provision")
            if state["pending"]:
                raise ValueError("pending provisioning exists; use reconcile")
            if state["provisioned"]:
                raise ValueError("Privacy Proxy is already provisioned; use rotate")
            if not normalized_url:
                raise ValueError("Privacy Proxy Worker URL is required before provision")
            generation = int(state["generation"]) + 1
        elif action == "rotate":
            if not state["prepared"] or not state["provisioned"]:
                raise ValueError("Privacy Proxy must be active before rotate")
            if state["pending"]:
                raise ValueError("pending rotation exists; use reconcile")
            if not normalized_url:
                raise ValueError("Privacy Proxy Worker URL is required before rotate")
            generation = int(state["generation"]) + 1
        elif action == "reconcile":
            if not state["pending"]:
                raise ValueError("no pending provisioning operation to reconcile")
            if not normalized_url:
                raise ValueError("Privacy Proxy Worker URL is required before reconcile")
            generation = int(state["pending_generation"] or 0)
        elif action == "deprovision":
            if state["pending"]:
                raise ValueError("pending provisioning exists; reconcile it before deprovision")
            if not state["prepared"] or not state["provisioned"]:
                raise ValueError("Privacy Proxy is not actively provisioned")
            if not normalized_url:
                raise ValueError("Privacy Proxy Worker URL is required before deprovision")
            generation = int(state["generation"]) + 1
        else:
            raise ValueError(f"unsupported Privacy Proxy action: {action}")
        return {
            "action": action,
            "worker_origin": origin,
            "generation": generation,
            "key_id": state["key_id"],
            "fingerprint": state["fingerprint"],
            "pending_operation": state["pending_operation"],
            "pending_generation": state["pending_generation"],
        }

    def preview(self, action: str, *, worker_url: str | None = None) -> dict[str, Any]:
        binding = self._binding(action, worker_url)
        token = self.confirmations.issue(binding)
        return {
            "ok": True,
            "privacy_proxy_action": action,
            "action_preview": binding,
            "approval_required": True,
            "action_applied": False,
            "confirmation_token": token,
            "confirmation_expires_in_seconds": self.confirmations.ttl_seconds,
            "next_step": (
                "Show this exact Privacy Proxy operation, Worker origin, key fingerprint and generation "
                "to the user and obtain explicit approval in the active chat. Then retry the same action "
                "with this one-time confirmation token."
            ),
            "privacy_proxy_provisioning": self.public_status(),
        }

    def _sign_headers(
        self,
        *,
        worker_url: str,
        operation: str,
        generation: int,
        body: bytes,
    ) -> tuple[str, dict[str, str]]:
        state = self.public_status()
        key_id = str(state.get("key_id") or "")
        if not key_id:
            raise RuntimeError("provisioning_signing_key_not_prepared")
        origin, path, endpoint = _endpoint(worker_url)
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        digest = hashlib.sha256(body).hexdigest()
        canonical = provisioning_canonical(
            method="POST",
            path=path,
            origin=origin,
            timestamp=timestamp,
            nonce=nonce,
            body_digest=digest,
            generation=generation,
            operation=operation,
            key_id=key_id,
        )
        signature = _b64url(self._private_key().sign(canonical))
        headers = {
            "Content-Type": "application/json",
            "X-Postmaster-Provisioning-Timestamp": timestamp,
            "X-Postmaster-Provisioning-Nonce": nonce,
            "X-Postmaster-Provisioning-Key-Id": key_id,
            "X-Postmaster-Provisioning-Generation": str(int(generation)),
            "X-Postmaster-Provisioning-Operation": operation,
            "X-Postmaster-Provisioning-Body-SHA256": digest,
            "X-Postmaster-Provisioning-Signature": signature,
            "User-Agent": "Postmaster-MCP-Privacy-Proxy/9.6.6",
        }
        return endpoint, headers

    def _post_provision(
        self,
        *,
        worker_url: str,
        operation: str,
        generation: int,
        secret: str | None,
    ) -> tuple[bool, int | None, str]:
        try:
            _assert_public_worker(worker_url)
        except Exception:
            return False, None, "worker_target_not_public_or_unresolvable"
        payload: dict[str, Any] = {
            "generation": int(generation),
            "operation": operation,
        }
        if secret is not None:
            payload["secret"] = secret
            payload["previous_secret_grace_seconds"] = PREVIOUS_SECRET_GRACE_SECONDS
        body = _json_bytes(payload)
        try:
            endpoint, headers = self._sign_headers(
                worker_url=worker_url,
                operation=operation,
                generation=generation,
                body=body,
            )
        except Exception:
            return False, None, "worker_provisioning_signing_failed"
        try:
            response = httpx.post(
                endpoint,
                content=body,
                headers=headers,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except Exception:
            return False, None, "worker_provisioning_network_error"
        if response.status_code not in {200, 204}:
            return False, int(response.status_code), "worker_provisioning_rejected"
        return True, int(response.status_code), ""

    @staticmethod
    def _hmac_headers(secret: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"{timestamp}\n{nonce}\n{digest}".encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Postmaster-Timestamp": timestamp,
            "X-Postmaster-Nonce": nonce,
            "X-Postmaster-Signature": signature,
            "User-Agent": "Postmaster-MCP-Privacy-Proxy/9.6.6",
        }

    def _verify_health(self, *, worker_url: str, secret: str) -> tuple[bool, int | None]:
        body = b"{}"
        url = worker_url.rstrip("/") + "/health"
        try:
            response = httpx.post(
                url,
                content=body,
                headers=self._hmac_headers(secret, body),
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except Exception:
            return False, None
        return response.status_code == 200, int(response.status_code)

    def _push_pending(self, worker_url: str, pending: PendingSecret) -> dict[str, Any]:
        ok, status_code, error = self._post_provision(
            worker_url=worker_url,
            operation=pending.operation,
            generation=pending.generation,
            secret=pending.secret,
        )
        if not ok:
            return {
                "ok": False,
                "privacy_proxy_action": pending.operation,
                "action_applied": False,
                "phase": "pending",
                "reconcile_required": True,
                "worker_http_status": status_code,
                "error": error,
                "privacy_proxy_provisioning": self.public_status(),
            }
        verified, health_status = self._verify_health(worker_url=worker_url, secret=pending.secret)
        if not verified:
            return {
                "ok": False,
                "privacy_proxy_action": pending.operation,
                "action_applied": False,
                "phase": "pending",
                "reconcile_required": True,
                "worker_http_status": health_status,
                "error": "worker_health_verification_failed",
                "privacy_proxy_provisioning": self.public_status(),
            }
        status = self._promote(pending)
        self.store.record_test(True, "")
        return {
            "ok": True,
            "privacy_proxy_action": pending.operation,
            "action_applied": True,
            "phase": "active",
            "health_verified": True,
            "generation": pending.generation,
            "privacy_proxy_provisioning": status,
            "privacy_proxy": self.store.status(),
        }

    def execute(
        self,
        action: str,
        *,
        confirmation_token: str,
        worker_url: str | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        try:
            binding = self._binding(action, worker_url)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action_applied": False}
        if not self.confirmations.consume(confirmation_token, binding):
            return {
                "ok": False,
                "privacy_proxy_action": action,
                "approval_required": True,
                "action_applied": False,
                "error": (
                    "missing, expired, already-used, or mismatched Privacy Proxy confirmation; "
                    "request a new preview and obtain new explicit user approval"
                ),
                "privacy_proxy_provisioning": self.public_status(),
            }

        effective_url = (worker_url if worker_url is not None else self.store.status().get("worker_url") or "").strip()
        if action == "prepare_provisioning":
            try:
                if worker_url is not None:
                    self.store.configure(worker_url=worker_url, enabled=False)
                status = self._prepare_key()
            except Exception:
                return {"ok": False, "error": "prepare_provisioning_failed", "action_applied": False}
            return {
                "ok": True,
                "privacy_proxy_action": action,
                "action_applied": True,
                "approval_required": False,
                "privacy_proxy_provisioning": status,
                "public_material_only": True,
            }

        if action in {"provision", "rotate"}:
            generation = int(binding["generation"])
            secret = secrets.token_urlsafe(48)
            self._save_pending(generation, action, secret)
            return self._push_pending(effective_url, self._pending() or PendingSecret(generation, action, secret))

        if action == "reconcile":
            pending = self._pending()
            if pending is None:
                return {"ok": False, "error": "no_pending_provisioning_operation", "action_applied": False}
            result = self._push_pending(effective_url, pending)
            result["privacy_proxy_action"] = "reconcile"
            return result

        if action == "deprovision":
            generation = int(binding["generation"])
            ok, status_code, error = self._post_provision(
                worker_url=effective_url,
                operation="deprovision",
                generation=generation,
                secret=None,
            )
            if not ok:
                return {
                    "ok": False,
                    "privacy_proxy_action": action,
                    "action_applied": False,
                    "worker_http_status": status_code,
                    "error": error,
                    "privacy_proxy_provisioning": self.public_status(),
                }
            status = self._finish_deprovision(generation)
            return {
                "ok": True,
                "privacy_proxy_action": action,
                "action_applied": True,
                "generation": generation,
                "privacy_proxy_provisioning": status,
                "privacy_proxy": self.store.status(),
            }

        return {"ok": False, "error": f"unsupported Privacy Proxy action: {action}", "action_applied": False}


__all__ = [
    "ConfirmationTokens",
    "MAX_PREVIOUS_SECRET_GRACE_SECONDS",
    "PREVIOUS_SECRET_GRACE_SECONDS",
    "PrivacyProxyProvisioning",
    "PROVISIONING_CLOCK_SKEW_SECONDS",
    "PROVISIONING_CONFIRM_TTL_SECONDS",
    "provisioning_canonical",
]
