from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import bleach
import httpx
from cryptography.fernet import Fernet, InvalidToken

from .inbound_inspection_urls import inspect_url
from .mailbox_cache_v963 import MailboxCacheStore


_CSS_URL_RE = re.compile(r'''(?is)url\(\s*(['"]?)(.*?)\1\s*\)''')
_URL_ATTRS = {"src", "href", "background", "action", "poster", "data", "srcset"}
_PASSIVE_SOURCES = {
    "img src", "background", "style url()", "style-block url()", "link href", "source src",
    "video poster", "body background", "table background", "td background",
}
_TRACKING_NAMES = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid",
    "mc_cid", "mc_eid", "mkt_tok", "vero_id", "oly_anon_id", "oly_enc_id",
}
_ALLOWED_PROXY_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif",
    "text/css", "font/woff", "font/woff2", "application/font-woff", "application/font-woff2",
    "application/vnd.ms-fontobject", "font/ttf", "font/otf",
}
_MAX_PROXY_BYTES = 2_000_000
_MAX_PROXY_URLS = 32
_MAX_PROXY_CONCURRENCY = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remote(url: str) -> bool:
    return urlparse(unescape(url or "").strip()).scheme.casefold() in {"http", "https"}


def _declared_dimension(value: str | None) -> float | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$", value or "", re.I)
    return float(match.group(1)) if match else None


def _hidden_style(style: str) -> bool:
    compact = re.sub(r"\s+", "", (style or "").casefold())
    return any(marker in compact for marker in (
        "display:none", "visibility:hidden", "opacity:0", "width:0", "height:0",
        "max-height:0", "font-size:0",
    ))


def _classification(record: dict[str, Any]) -> tuple[str, int, list[str], list[str]]:
    url = str(record.get("url") or "")
    source = str(record.get("source_type") or "")
    inspected = inspect_url(url, visible_text=str(record.get("anchor_text") or ""))
    parsed = urlparse(url)
    haystack = " ".join([parsed.hostname or "", parsed.path or "", parsed.query or ""]).casefold()
    width = _declared_dimension(str(record.get("width") or ""))
    height = _declared_dimension(str(record.get("height") or ""))
    reasons: list[str] = []
    observed: list[str] = []
    score = 0

    if inspected.get("tracker_hint"):
        score += 30
        reasons.append("tracker host/path hint")
        observed.append("tracker-like host/path")
    tracking_parameters = list(inspected.get("tracking_parameters") or [])
    if tracking_parameters:
        score += min(25, 8 + 4 * len(tracking_parameters))
        reasons.append("tracking query parameters")
        observed.append("query parameters: " + ", ".join(tracking_parameters[:8]))
    if inspected.get("redirector"):
        score += 12
        reasons.append("redirector-shaped URL")
        observed.append("redirect target embedded in query")
    if inspected.get("anchor_href_mismatch"):
        score += 12
        reasons.append("visible host differs from href host")
        observed.append("anchor text/href host mismatch")

    is_image = source in {"img src", "background", "body background", "table background", "td background", "style url()", "style-block url()"}
    pixel = source == "img src" and (
        (width is not None and width <= 2) or (height is not None and height <= 2) or bool(record.get("hidden"))
    )
    if pixel:
        score += 60
        reasons.append("tiny or hidden image")
        observed.append("declared image dimensions/style are tiny or hidden")

    lowered = haystack
    if source == "a href":
        if any(token in lowered for token in ("unsubscribe", "optout", "opt-out", "list-unsubscribe")):
            kind = "unsubscribe"
        elif inspected.get("redirector"):
            kind = "redirector"
        elif any(token in lowered for token in ("reset", "magic", "login", "signin", "verify", "confirm", "approve", "accept", "activate", "token=", "action=")):
            kind = "action URL"
        elif tracking_parameters or inspected.get("tracker_hint"):
            kind = "analytics link"
        else:
            kind = "normal link"
    elif pixel:
        kind = "tracking pixel"
    elif is_image and _remote(url):
        kind = "remote image"
        score = max(score, 5)
    else:
        kind = "unknown"
    return kind, min(100, score), reasons, observed


class _InventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.rows: list[dict[str, Any]] = []
        self._anchors: list[dict[str, Any]] = []
        self._style_depth = 0
        self._style_parts: list[str] = []

    def _append(
        self,
        url: str,
        source_type: str,
        *,
        snippet: str = "",
        anchor_text: str = "",
        width: str = "",
        height: str = "",
        hidden: bool = False,
    ) -> None:
        value = unescape(url or "").strip()
        if not value:
            return
        # srcset contains multiple URL + descriptor pairs.
        values = [value]
        if source_type.endswith(" srcset"):
            values = [part.strip().split()[0] for part in value.split(",") if part.strip()]
        for candidate in values:
            self.rows.append(
                {
                    "url": candidate,
                    "source_type": source_type,
                    "source_snippet": snippet[:1500],
                    "anchor_text": anchor_text,
                    "width": width,
                    "height": height,
                    "hidden": bool(hidden),
                }
            )

    def _css(self, css: str, source_type: str, snippet: str) -> None:
        for _, value in _CSS_URL_RE.findall(css or ""):
            self._append(value, source_type, snippet=snippet)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        data = {str(k).casefold(): str(v or "") for k, v in attrs}
        snippet = self.get_starttag_text() or ""
        style = data.get("style", "")
        if style:
            self._css(style, "style url()", snippet)
        background = data.get("background", "")
        if background:
            self._append(background, f"{tag} background", snippet=snippet)
        if tag == "style":
            self._style_depth += 1
            return
        if tag == "a":
            self._anchors.append({"href": data.get("href", ""), "text": [], "snippet": snippet})
        for name in _URL_ATTRS:
            value = data.get(name, "")
            if not value or (tag == "a" and name == "href") or name == "background":
                continue
            source = f"{tag} {name}"
            if tag == "link" and name == "href":
                source = "link href"
            elif tag == "img" and name == "src":
                source = "img src"
            elif tag == "source" and name == "src":
                source = "source src"
            elif tag == "video" and name == "poster":
                source = "video poster"
            self._append(
                value,
                source,
                snippet=snippet,
                width=data.get("width", ""),
                height=data.get("height", ""),
                hidden=("hidden" in data or _hidden_style(style)),
            )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            if self._style_depth == 0:
                css = "".join(self._style_parts)
                self._css(css, "style-block url()", css[:1500])
                self._style_parts.clear()
        elif tag == "a" and self._anchors:
            anchor = self._anchors.pop()
            self._append(
                str(anchor.get("href") or ""),
                "a href",
                snippet=str(anchor.get("snippet") or ""),
                anchor_text="".join(anchor.get("text") or []).strip(),
            )

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self._style_parts.append(data)
        if self._anchors:
            self._anchors[-1]["text"].append(data)


def inventory_html(html: str) -> dict[str, Any]:
    """Inventory message URLs statically. This function performs no DNS or HTTP requests."""
    parser = _InventoryParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(parser.rows):
        row = dict(raw)
        inspected = inspect_url(str(row.get("url") or ""), visible_text=str(row.get("anchor_text") or ""))
        kind, score, reasons, observed = _classification(row)
        row.update(
            {
                "index": index,
                "domain": str(inspected.get("host") or ""),
                "scheme": str(inspected.get("scheme") or ""),
                "classification": kind,
                "tracking_score": score,
                "tracking_reasons": reasons,
                "observed_evidence": observed,
                "inference": "heuristic classification; not a certainty",
                "passive_resource": row.get("source_type") in _PASSIVE_SOURCES,
                "network_contacted": False,
                "redirector_hint": bool(inspected.get("redirector")),
                "redirect_target_hint": str(inspected.get("redirect_target") or ""),
            }
        )
        rows.append(row)
    remote = [row for row in rows if row.get("scheme") in {"http", "https"}]
    domains = sorted({str(row.get("domain") or "") for row in remote if row.get("domain")})
    remote_images = [row for row in remote if row.get("classification") in {"remote image", "tracking pixel"}]
    probable_pixels = [row for row in remote if row.get("classification") == "tracking pixel"]
    message_score = max([int(row.get("tracking_score") or 0) for row in rows] or [0])
    if message_score >= 65:
        verdict = "Tracking probabile"
    elif message_score >= 25:
        verdict = "Tracking possibile"
    else:
        verdict = "Nessun tracking evidente"
    return {
        "static_only": True,
        "network_requests_performed": 0,
        "urls": rows,
        "url_count": len(rows),
        "external_url_count": len(remote),
        "external_domains": domains,
        "remote_image_count": len(remote_images),
        "tracking_pixel_count": len(probable_pixels),
        "tracking_score": message_score,
        "tracking_verdict": verdict,
        "tracking_estimate_notice": "Stima euristica 0–100, non una certezza.",
        "warning": {
            "remote_images": len(remote_images),
            "possible_tracking_pixels": len(probable_pixels),
            "external_domains": len(domains),
        },
    }


def safe_email_html(html: str) -> str:
    """Strict Safe Email HTML: no active or remote resources and no navigable message URLs."""
    tags = [
        "abbr", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6",
        "hr", "i", "li", "ol", "p", "pre", "span", "strong", "table", "tbody", "td", "th", "thead",
        "tr", "u", "ul", "a",
    ]
    attrs = {
        "a": ["title"],
        "abbr": ["title"],
        "td": ["colspan", "rowspan"],
        "th": ["colspan", "rowspan"],
    }
    return bleach.clean(html or "", tags=tags, attributes=attrs, protocols=[], strip=True, strip_comments=True)


class PrivacyProxyStore:
    """Encrypted local Privacy Proxy configuration. Read APIs never reveal the shared secret."""

    def __init__(self, db_path: str | None = None, key_path: str | None = None) -> None:
        self.db_path = db_path or os.getenv("PRIVACY_PROXY_DB_PATH", "/data/privacy_proxy.db")
        self.key_path = key_path or os.getenv("PRIVACY_PROXY_KEY_PATH", "/data/privacy_proxy.key")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.key_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fernet = Fernet(self._load_key())
        self._init_db()

    def _load_key(self) -> bytes:
        path = Path(self.key_path)
        if path.exists():
            key = path.read_bytes().strip()
            Fernet(key)
            return key
        key = Fernet.generate_key()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(key + b"\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS privacy_proxy_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    worker_url TEXT NOT NULL DEFAULT '',
                    secret_enc TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    tracking_obfuscation INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    last_test_at TEXT NOT NULL DEFAULT '',
                    last_test_ok INTEGER,
                    last_test_error TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO privacy_proxy_config(singleton) VALUES(1);
                CREATE TABLE IF NOT EXISTS privacy_onboarding_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def configure(
        self,
        *,
        worker_url: str | None = None,
        secret: str | None = None,
        enabled: bool | None = None,
        tracking_obfuscation: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM privacy_proxy_config WHERE singleton=1").fetchone()
            current = dict(row or {})
            url = str(current.get("worker_url") or "") if worker_url is None else worker_url.strip()
            if url:
                parsed = urlparse(url)
                if parsed.scheme.casefold() != "https" or not parsed.netloc or parsed.username or parsed.password:
                    raise ValueError("Privacy Proxy Worker URL must be an absolute HTTPS URL without embedded credentials")
                url = url.rstrip("/")
            secret_enc = str(current.get("secret_enc") or "")
            if secret is not None:
                value = secret.strip()
                if value and len(value) < 32:
                    raise ValueError("Privacy Proxy shared secret must contain at least 32 characters")
                secret_enc = self._fernet.encrypt(value.encode("utf-8")).decode("ascii") if value else ""
            enabled_value = bool(current.get("enabled")) if enabled is None else bool(enabled)
            obfuscation_value = bool(current.get("tracking_obfuscation")) if tracking_obfuscation is None else bool(tracking_obfuscation)
            if enabled_value and (not url or not secret_enc):
                raise ValueError("Privacy Proxy cannot be enabled until Worker URL and shared secret are configured")
            conn.execute(
                """
                UPDATE privacy_proxy_config SET worker_url=?,secret_enc=?,enabled=?,tracking_obfuscation=?,updated_at=?
                WHERE singleton=1
                """,
                (url, secret_enc, int(enabled_value), int(obfuscation_value), _now()),
            )
        return self.status()

    def _secret(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT secret_enc FROM privacy_proxy_config WHERE singleton=1").fetchone()
        encoded = str(row[0] or "") if row else ""
        if not encoded:
            return ""
        try:
            return self._fernet.decrypt(encoded.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Privacy Proxy secret cannot be decrypted with the persistent key") from exc

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM privacy_proxy_config WHERE singleton=1").fetchone()
        value = dict(row or {})
        secret_configured = bool(value.get("secret_enc"))
        configured = bool(value.get("worker_url") and secret_configured)
        return {
            "configured": configured,
            "worker_url": str(value.get("worker_url") or ""),
            "secret_configured": secret_configured,
            "secret": "configured" if secret_configured else "not configured",
            "enabled": bool(value.get("enabled")) and configured,
            "tracking_obfuscation": bool(value.get("tracking_obfuscation")),
            "updated_at": str(value.get("updated_at") or ""),
            "last_test_at": str(value.get("last_test_at") or ""),
            "last_test_ok": None if value.get("last_test_ok") is None else bool(value.get("last_test_ok")),
            "last_test_error": str(value.get("last_test_error") or ""),
        }

    def client_config(self) -> dict[str, Any]:
        status = self.status()
        return {**status, "secret_value": self._secret()}

    def record_test(self, ok: bool, error: str = "") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE privacy_proxy_config SET last_test_at=?,last_test_ok=?,last_test_error=? WHERE singleton=1",
                (_now(), int(bool(ok)), (error or "")[:500]),
            )
        return self.status()

    def set_onboarding(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO privacy_onboarding_state(key,value,updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
                """,
                (key, value, _now()),
            )

    def onboarding(self, account_count: int) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key,value FROM privacy_onboarding_state").fetchall()
        state = {str(row["key"]): str(row["value"]) for row in rows}
        established = int(account_count) > 0
        # Upgrade safety invariant: any configured email account makes this an established install.
        return {
            "established_installation": established,
            "full_onboarding": not established,
            "privacy_proxy_offer": established and state.get("privacy_proxy_offer") != "dismissed" and not self.status()["configured"],
            "fresh_install_resumable": not established,
            "privacy_proxy_offer_dismissed": state.get("privacy_proxy_offer") == "dismissed",
        }


class PrivacyProxyClient:
    """Authenticated client that contacts only the configured Worker, never message target URLs directly."""

    def __init__(self, store: PrivacyProxyStore, *, timeout_seconds: float = 10.0) -> None:
        self.store = store
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))

    @staticmethod
    def _headers(secret: str, body: bytes) -> dict[str, str]:
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
            "User-Agent": "Postmaster-MCP-Privacy-Proxy/9.6.3",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        cfg = self.store.client_config()
        if not cfg.get("configured"):
            raise RuntimeError("Privacy Proxy is not configured")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        url = str(cfg["worker_url"]).rstrip("/") + path
        return httpx.post(
            url,
            content=body,
            headers=self._headers(str(cfg["secret_value"]), body),
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )

    def test_connection(self) -> dict[str, Any]:
        try:
            response = self._post("/health", {})
            ok = response.status_code == 200
            error = "" if ok else f"Worker health returned HTTP {response.status_code}"
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
        self.store.record_test(ok, error)
        return {"ok": ok, "error": error, "status": self.store.status()}

    def fetch(self, url: str, *, classification: str, tracking_score: int) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute HTTP(S) passive resource URLs may be proxied")
        cfg = self.store.client_config()
        if not cfg.get("enabled"):
            raise RuntimeError("Privacy Proxy is disabled")
        payload = {
            "url": url,
            "classification": classification,
            "tracking_score": int(tracking_score),
            "tracking_obfuscation": bool(cfg.get("tracking_obfuscation")),
            "max_response_bytes": _MAX_PROXY_BYTES,
        }
        response = self._post("/fetch", payload)
        if response.status_code != 200:
            raise RuntimeError(f"Privacy Proxy request failed with HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Privacy Proxy returned an invalid response")
        content_type = str(data.get("content_type") or "").split(";", 1)[0].strip().casefold()
        body = b""
        encoded = data.get("body_base64")
        if encoded:
            body = base64.b64decode(str(encoded), validate=True)
        if len(body) > _MAX_PROXY_BYTES:
            raise RuntimeError("Privacy Proxy response exceeded the local response-size limit")
        status = int(data.get("status") or 0)
        if body and content_type not in _ALLOWED_PROXY_TYPES:
            raise RuntimeError(f"Privacy Proxy returned disallowed content type: {content_type or 'unknown'}")
        return {
            "status": status,
            "content_type": content_type,
            "body": body,
            "redirect_location": str(data.get("redirect_location") or ""),
            "error": str(data.get("error") or ""),
        }


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def attach_cache_state(inventory: dict[str, Any], cache: MailboxCacheStore, *, account_id: str, mailbox: str, uid: str) -> dict[str, Any]:
    result = dict(inventory)
    rows: list[dict[str, Any]] = []
    for row in inventory.get("urls") or []:
        item = dict(row)
        digest = _url_hash(str(item.get("url") or ""))
        key = cache.resource_key(account_id, mailbox, uid, digest)
        cached = cache.get_resource(key)
        item["cache_key"] = key
        item["url_hash"] = digest
        item["cache_status"] = "cached" if cached else "not fetched"
        item["proxy_status"] = "cached" if cached else "not contacted"
        item["redirect_status"] = int(cached.get("http_status") or 0) if cached and cached.get("redirect_location") else None
        item["redirect_location"] = str(cached.get("redirect_location") or "") if cached else ""
        item["error_state"] = str(cached.get("error_state") or "") if cached else ""
        rows.append(item)
    result["urls"] = rows
    return result


def fetch_passive_resources(
    inventory: dict[str, Any],
    *,
    cache: MailboxCacheStore,
    proxy: PrivacyProxyClient,
    account_id: str,
    mailbox: str,
    uid: str,
) -> dict[str, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in inventory.get("urls") or []:
        row = dict(raw)
        url = str(row.get("url") or "")
        if not row.get("passive_resource") or not _remote(url) or url in seen:
            continue
        seen.add(url)
        candidates.append(row)
        if len(candidates) >= _MAX_PROXY_URLS:
            break

    def one(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        url = str(row.get("url") or "")
        digest = _url_hash(url)
        key = cache.resource_key(account_id, mailbox, uid, digest)
        existing = cache.get_resource(key)
        if existing:
            return url, existing
        try:
            fetched = proxy.fetch(
                url,
                classification=str(row.get("classification") or "unknown"),
                tracking_score=int(row.get("tracking_score") or 0),
            )
            status = int(fetched.get("status") or 0)
            body = bytes(fetched.get("body") or b"") if status == 200 else None
            result = cache.put_resource(
                cache_key=key,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                url=url,
                url_hash=digest,
                content_type=str(fetched.get("content_type") or ""),
                body=body,
                http_status=status,
                redirect_location=str(fetched.get("redirect_location") or ""),
                classification=str(row.get("classification") or "unknown"),
                tracking_score=int(row.get("tracking_score") or 0),
                error_state=str(fetched.get("error") or ""),
            )
        except Exception as exc:
            result = cache.put_resource(
                cache_key=key,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                url=url,
                url_hash=digest,
                content_type="",
                body=None,
                http_status=None,
                redirect_location="",
                classification=str(row.get("classification") or "unknown"),
                tracking_score=int(row.get("tracking_score") or 0),
                error_state=f"{type(exc).__name__}: {exc}"[:500],
            )
        return url, result

    results: dict[str, dict[str, Any]] = {}
    if not candidates:
        return results
    with ThreadPoolExecutor(max_workers=_MAX_PROXY_CONCURRENCY, thread_name_prefix="postmaster-privacy-proxy") as pool:
        futures = [pool.submit(one, row) for row in candidates]
        for future in as_completed(futures):
            url, value = future.result()
            results[url] = value
    return results


class _FullHtmlRewriter(HTMLParser):
    _DROP_CONTENT = {"script", "iframe", "object", "embed", "form", "button", "input", "textarea", "select", "option"}
    _VOID = {"br", "hr", "img", "meta", "link", "source"}

    def __init__(self, resource_urls: dict[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self.resource_urls = resource_urls
        self.parts: list[str] = []
        self.drop_depth = 0
        self.style_depth = 0

    def _css(self, value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            target = unescape(match.group(2) or "").strip()
            local = self.resource_urls.get(target)
            return f'url("{local}")' if local else 'url("")'
        return _CSS_URL_RE.sub(repl, value or "")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._DROP_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        if tag == "style":
            self.style_depth += 1
            self.parts.append("<style>")
            return
        data = {str(k).casefold(): str(v or "") for k, v in attrs}
        out: list[tuple[str, str]] = []
        for name, value in data.items():
            if name.startswith("on") or name in {"srcset", "action", "formaction"}:
                continue
            if tag == "a" and name == "href":
                # Navigation/action URLs remain silent even in Full HTML. A future explicit
                # navigation action may consume the inventory, but this rendering never does.
                continue
            if name == "style":
                out.append((name, self._css(value)))
                continue
            if name in {"src", "background", "poster"} and _remote(value):
                local = self.resource_urls.get(unescape(value).strip())
                if local:
                    out.append((name, local))
                continue
            if tag == "link" and name == "href" and _remote(value):
                local = self.resource_urls.get(unescape(value).strip())
                if local:
                    out.append((name, local))
                continue
            if name in {"href", "src", "background", "poster"} and value.casefold().startswith(("javascript:", "file:")):
                continue
            out.append((name, value))
        rendered = "".join(f' {escape(name, quote=True)}="{escape(value, quote=True)}"' for name, value in out)
        self.parts.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._DROP_CONTENT:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth:
            return
        if tag == "style":
            if self.style_depth:
                self.style_depth -= 1
            self.parts.append("</style>")
            return
        if tag not in self._VOID:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.drop_depth:
            return
        self.parts.append(self._css(data) if self.style_depth else data)

    def handle_entityref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return


def rewrite_full_html(html: str, resource_urls: dict[str, str]) -> str:
    parser = _FullHtmlRewriter(resource_urls)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return safe_email_html(html)
    return "".join(parser.parts)


__all__ = [
    "PrivacyProxyStore", "PrivacyProxyClient", "inventory_html", "safe_email_html",
    "attach_cache_state", "fetch_passive_resources", "rewrite_full_html",
    "_MAX_PROXY_BYTES", "_MAX_PROXY_URLS", "_MAX_PROXY_CONCURRENCY",
]
