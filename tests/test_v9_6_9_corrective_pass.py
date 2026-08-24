from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from postmaster.email_inventory_v963 import inventory_message
from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster.privacy_cache_v969 import install_hashed_resource_keys
from postmaster.privacy_css_cache_v969 import CacheAwareBoundedPassiveContentService
from postmaster import webgui_v969


CSS_URL = "https://remote.example/mail.css"
HERO_URL = "https://remote.example/hero.png"
IMPORT_URL = "https://remote.example/imported.css"
ACTION_URL = "https://example.com/action"


class _ProxyStore:
    def status(self):
        return {
            "enabled": True,
            "tracking_obfuscation": True,
            "high_noise_decoy_enabled": False,
        }


class _Proxy:
    def __init__(self):
        self.calls: list[str] = []
        self.fail_urls: set[str] = set()

    def fetch(self, url, **kwargs):
        value = str(url)
        self.calls.append(value)
        if value in self.fail_urls:
            raise RuntimeError("synthetic failure")
        if value == CSS_URL:
            return {
                "status": 200,
                "content_type": "text/css",
                "body": (
                    b'@import url("https://remote.example/imported.css");\n'
                    b'.hero{background-image:url("./hero.png")}\n'
                ),
                "redirect_location": "",
                "error": "",
            }
        if value == HERO_URL:
            return {
                "status": 200,
                "content_type": "image/png",
                "body": b"png",
                "redirect_location": "",
                "error": "",
            }
        raise AssertionError("unexpected origin fetch: " + value)


class _Base:
    def __init__(self, cache: MailboxCacheStore, proxy: _Proxy):
        self.cache = cache
        self.proxy = proxy
        self.proxy_store = _ProxyStore()

    def mailbox_cache_store(self):
        return self.cache

    def privacy_proxy_store(self):
        return self.proxy_store

    def privacy_proxy_client(self):
        return self.proxy


def _html() -> str:
    return (
        f'<link rel="stylesheet" href="{CSS_URL}">'
        '<div class="hero">Hello</div>'
        f'<a href="{ACTION_URL}">Action</a>'
    )


def _raw_message(body_html: str) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = "<css-corrective@example.test>"
    msg["From"] = "sender@example.test"
    msg["To"] = "reader@example.test"
    msg["Date"] = "Mon, 24 Aug 2026 00:00:00 +0000"
    msg["Subject"] = "CSS corrective"
    msg.set_content("plain")
    msg.add_alternative(body_html, subtype="html")
    return msg.as_bytes()


def _seed_message(cache: MailboxCacheStore, body_html: str) -> None:
    raw = _raw_message(body_html)
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    cache.upsert_header(
        account_id="acct",
        mailbox="INBOX",
        uid="7",
        uidvalidity=10,
        row={
            "message_id": str(parsed.get("Message-ID") or ""),
            "in_reply_to": "",
            "references": "",
            "date": str(parsed.get("Date") or ""),
            "from": str(parsed.get("From") or ""),
            "from_addresses": ["sender@example.test"],
            "to": str(parsed.get("To") or ""),
            "to_addresses": ["reader@example.test"],
            "cc": "",
            "cc_addresses": [],
            "subject": str(parsed.get("Subject") or ""),
        },
        flags=[],
        size_bytes=len(raw),
        header_bytes=raw,
    )
    cache.store_body("acct", "INBOX", "7", raw, truncated=False)


class NestedCssCorrectiveV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = MailboxCacheStore(str(Path(self.temp.name) / "mailbox.db"))
        install_hashed_resource_keys(self.cache)
        _seed_message(self.cache, _html())
        self.proxy = _Proxy()
        self.service = CacheAwareBoundedPassiveContentService(_Base(self.cache, self.proxy))
        self.inventory = inventory_message(_html(), "plain")

    def _css_body(self) -> str:
        digest = hashlib.sha256(CSS_URL.encode()).hexdigest()
        key = self.cache.resource_key("acct", "INBOX", "7", digest)
        row = self.cache.get_resource(key)
        self.assertIsNotNone(row)
        return bytes(row["body"] or b"").decode("utf-8", errors="replace")

    def test_nested_css_first_open_cache_reopen_and_explicit_refresh(self):
        first = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
        )
        self.assertEqual(first["render_state"], "success")
        self.assertEqual(first["diagnostics"]["genuine_attempted"], 2)
        self.assertEqual(first["diagnostics"]["nested_attempted"], 1)
        self.assertEqual(self.proxy.calls, [CSS_URL, HERO_URL])
        self.assertNotIn(IMPORT_URL, self.proxy.calls)
        self.assertNotIn(ACTION_URL, self.proxy.calls)
        css = self._css_body()
        self.assertNotIn(HERO_URL, css)
        self.assertNotIn(IMPORT_URL, css)
        self.assertIn("/dashboard/inbox/resource?key=r_", css)
        self.assertIn("/dashboard/inbox/resource?", first["rendered_html"])

        before = len(self.proxy.calls)
        second = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
        )
        self.assertEqual(len(self.proxy.calls), before)
        self.assertEqual(second["network_requests_performed"], 0)
        self.assertTrue(second["cache_only"])
        self.assertEqual(second["diagnostics"]["decoy_attempted"], 0)

        refreshed = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
            refresh=True,
        )
        self.assertEqual(refreshed["render_state"], "success")
        self.assertEqual(self.proxy.calls, [CSS_URL, HERO_URL, CSS_URL, HERO_URL])

    def test_nested_failure_tombstone_no_retry_then_refresh_replaces_it(self):
        self.proxy.fail_urls.add(HERO_URL)
        first = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
        )
        self.assertEqual(first["render_state"], "partial")
        self.assertEqual(first["diagnostics"]["nested_failed"], 1)
        self.assertEqual(self.proxy.calls, [CSS_URL, HERO_URL])
        css = self._css_body()
        self.assertNotIn(HERO_URL, css)
        self.assertIn("postmaster-negative:r_", css)

        before = len(self.proxy.calls)
        reopen = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
        )
        self.assertEqual(len(self.proxy.calls), before)
        self.assertEqual(reopen["render_state"], "partial")
        self.assertEqual(reopen["network_requests_performed"], 0)
        self.assertGreaterEqual(reopen["diagnostics"]["negative_cache_hits"], 1)

        cached = self.service.render_cached_message(
            account_id="acct", mailbox="INBOX", uid="7"
        )
        self.assertEqual(cached["render_state"], "partial")
        self.assertEqual(cached["network_requests_performed"], 0)
        self.assertGreaterEqual(cached["diagnostics"]["negative_cache_hits"], 1)

        self.proxy.fail_urls.clear()
        refreshed = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
            refresh=True,
        )
        self.assertEqual(refreshed["render_state"], "success")
        self.assertEqual(self.proxy.calls, [CSS_URL, HERO_URL, CSS_URL, HERO_URL])
        css = self._css_body()
        self.assertNotIn("postmaster-negative:", css)
        self.assertIn("/dashboard/inbox/resource?key=r_", css)

        final_count = len(self.proxy.calls)
        final_reopen = self.service.fetch_inventory(
            self.inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="7",
            body_html=_html(),
        )
        self.assertEqual(len(self.proxy.calls), final_count)
        self.assertEqual(final_reopen["network_requests_performed"], 0)


class _FailureService:
    def __init__(self):
        self.fetch_refresh_values: list[bool] = []
        self.fetch_results = [
            {
                "ok": False,
                "render_state": "failure",
                "cache_only": True,
                "diagnostics": {
                    "discovered": 1,
                    "genuine_attempted": 0,
                    "genuine_succeeded": 0,
                    "cached_succeeded": 0,
                    "negative_cache_hits": 1,
                    "decoy_attempted": 0,
                    "decoy_succeeded": 0,
                },
            },
            {
                "ok": True,
                "render_state": "success",
                "cache_only": False,
                "diagnostics": {
                    "discovered": 1,
                    "genuine_attempted": 1,
                    "genuine_succeeded": 1,
                    "cached_succeeded": 1,
                    "negative_cache_hits": 0,
                    "decoy_attempted": 0,
                    "decoy_succeeded": 0,
                },
            },
        ]
        self.index = 0

    def render_cached_message(self, **kwargs):
        return {
            "ok": False,
            "render_state": "failure",
            "cache_only": True,
            "network_requests_performed": 0,
            "diagnostics": {"negative_cache_hits": 1},
        }

    def fetch_message(self, *, refresh=False, **kwargs):
        self.fetch_refresh_values.append(bool(refresh))
        value = self.fetch_results[self.index]
        self.index += 1
        return value


class _RouteBase:
    def __init__(self, forms):
        self.forms = list(forms)

    async def _verified_form(self, request):
        return self.forms.pop(0), None

    def privacy_proxy_store(self):
        return SimpleNamespace(status=lambda: {"enabled": True})

    def _csrf_value(self):
        return "csrf"

    def mailbox_cache_store(self):
        return SimpleNamespace(raw_message=lambda *args: None)


class WebGuiFailureRefreshV969Tests(unittest.TestCase):
    @staticmethod
    def _request(query: bytes = b"") -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": query,
            }
        )

    def test_failure_safe_email_exposes_explicit_refresh_without_network_reopen(self):
        service = _FailureService()
        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: (
                '<span class="v963-chip ok">Email sicura · default</span>'
                '<div class="v963-safe-email">safe</div>'
            ),
        )
        base = _RouteBase([])
        webgui_v969._install_webgui_v969(base, fake_v963, service)
        rendered = fake_v963._detail(
            base, {}, "acct", "INBOX", "received", "7", self._request()
        )
        self.assertIn("Riprova contenuti remoti", rendered)
        self.assertIn('name="refresh_remote" value="1"', rendered)
        self.assertIn("La riapertura normale non effettua nuove richieste", rendered)
        self.assertEqual(service.fetch_refresh_values, [])

    def test_failure_route_then_explicit_refresh_passes_refresh_true(self):
        service = _FailureService()
        fake_v963 = SimpleNamespace(
            confirm_full_html=lambda *a, **k: None,
            _detail=lambda *a, **k: '<span class="v963-chip ok">Email sicura · default</span>',
        )
        forms = [
            {"account_id": "acct", "mailbox": "INBOX", "uid": "7"},
            {
                "account_id": "acct",
                "mailbox": "INBOX",
                "uid": "7",
                "refresh_remote": "1",
            },
        ]
        base = _RouteBase(forms)
        webgui_v969._install_webgui_v969(base, fake_v963, service)
        failed = asyncio.run(fake_v963.confirm_full_html(base, object()))
        self.assertIn("remote_fetch_failed=1", failed.headers["location"])
        self.assertNotIn("full_html=1", failed.headers["location"])
        refreshed = asyncio.run(fake_v963.confirm_full_html(base, object()))
        self.assertIn("full_html=1", refreshed.headers["location"])
        self.assertEqual(service.fetch_refresh_values, [False, True])


if __name__ == "__main__":
    unittest.main()
