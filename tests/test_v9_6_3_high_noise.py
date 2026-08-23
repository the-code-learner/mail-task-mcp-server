from __future__ import annotations

import inspect
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from postmaster.email_privacy_v963 import (
    PrivacyProxyStore,
    _DECOY_TIMEOUT_SECONDS,
    _MAX_DECOY_CONCURRENCY,
    _MAX_DECOY_EXECUTION_SECONDS,
    _MAX_DECOY_REQUESTS_PER_DOMAIN,
    _MAX_DECOY_REQUESTS_PER_MESSAGE,
    _MAX_DECOY_RESPONSE_BYTES,
    _MAX_DECOY_TOTAL_BYTES,
    fetch_high_noise_decoys,
)
from postmaster.webgui_v963_high_noise import install_webgui_v963_high_noise


ROOT = Path(__file__).resolve().parents[1]


class FakeProxy:
    def __init__(self, *, body_size: int = 4, redirect: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.body_size = body_size
        self.redirect = redirect

    def fetch(self, url: str, **kwargs):
        self.calls.append((url, dict(kwargs)))
        if self.redirect:
            return {
                "status": 302,
                "content_type": "",
                "body": b"",
                "redirect_location": "https://redirect.example/next",
                "error": "",
            }
        return {
            "status": 200,
            "content_type": "image/png",
            "body": b"x" * self.body_size,
            "redirect_location": "",
            "error": "",
        }


def _inventory() -> dict:
    return {
        "urls": [
            {
                "url": "https://img.example/a.png?utm_source=mail&id=one",
                "source_type": "img src",
                "source_snippet": '<img src="https://img.example/a.png">',
                "passive_resource": True,
                "classification": "remote image",
                "tracking_score": 30,
            },
            {
                "url": "https://img.example/b.png?id=two",
                "source_type": "img src",
                "source_snippet": '<img src="https://img.example/b.png">',
                "passive_resource": True,
                "classification": "remote image",
                "tracking_score": 10,
            },
            {
                "url": "https://cdn.example/theme.css?campaign=x",
                "source_type": "link href",
                "source_snippet": '<link rel="stylesheet" href="https://cdn.example/theme.css">',
                "passive_resource": True,
                "classification": "unknown",
                "tracking_score": 0,
            },
            {
                "url": "https://example.org/reset?token=one",
                "source_type": "a href",
                "source_snippet": '<a href="https://example.org/reset?token=one">reset</a>',
                "passive_resource": False,
                "classification": "action URL",
                "tracking_score": 0,
            },
            {
                "url": "https://example.org/unsubscribe?token=two",
                "source_type": "a href",
                "source_snippet": '<a href="https://example.org/unsubscribe?token=two">unsubscribe</a>',
                "passive_resource": False,
                "classification": "unsubscribe",
                "tracking_score": 0,
            },
            {
                "url": "https://redirect.example/click?id=three",
                "source_type": "a href",
                "source_snippet": '<a href="https://redirect.example/click?id=three">open</a>',
                "passive_resource": False,
                "classification": "redirector",
                "tracking_score": 10,
            },
        ]
    }


class HighNoiseConfigV963Tests(unittest.TestCase):
    def test_default_off_supported_combinations_persistence_and_secret_masking(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "proxy.db")
            key = str(Path(tmp) / "proxy.key")
            store = PrivacyProxyStore(db, key)
            self.assertFalse(store.status()["high_noise_decoy_enabled"])

            secret = "s" * 48
            off_off = store.configure(
                worker_url="https://worker.example.invalid",
                secret=secret,
                enabled=True,
                tracking_obfuscation=False,
                high_noise_decoy_enabled=False,
            )
            self.assertFalse(off_off["tracking_obfuscation"])
            self.assertFalse(off_off["high_noise_decoy_enabled"])
            self.assertNotIn(secret, repr(off_off))

            on_off = store.configure(tracking_obfuscation=True, high_noise_decoy_enabled=False)
            self.assertTrue(on_off["tracking_obfuscation"])
            self.assertFalse(on_off["high_noise_decoy_enabled"])

            on_on = store.configure(tracking_obfuscation=True, high_noise_decoy_enabled=True)
            self.assertTrue(on_on["tracking_obfuscation"])
            self.assertTrue(on_on["high_noise_decoy_enabled"])
            with self.assertRaisesRegex(ValueError, "requires tracking obfuscation"):
                store.configure(tracking_obfuscation=False, high_noise_decoy_enabled=True)

            reopened = PrivacyProxyStore(db, key)
            self.assertTrue(reopened.status()["high_noise_decoy_enabled"])
            self.assertNotIn("secret_value", reopened.status())
            self.assertNotIn("secret_enc", reopened.status())

    def test_legacy_store_migrates_high_noise_to_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "proxy.db")
            key = str(Path(tmp) / "proxy.key")
            with sqlite3.connect(db) as conn:
                conn.executescript(
                    """
                    CREATE TABLE privacy_proxy_config (
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
                    INSERT INTO privacy_proxy_config(singleton,tracking_obfuscation) VALUES(1,1);
                    """
                )
            store = PrivacyProxyStore(db, key)
            self.assertTrue(store.status()["tracking_obfuscation"])
            self.assertFalse(store.status()["high_noise_decoy_enabled"])
            with sqlite3.connect(db) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(privacy_proxy_config)")}
            self.assertIn("high_noise_decoy_enabled", cols)


class HighNoiseExecutionV963Tests(unittest.TestCase):
    def _configured_store(self, tmp: str, *, enabled: bool = True, high_noise: bool = True) -> PrivacyProxyStore:
        store = PrivacyProxyStore(str(Path(tmp) / "proxy.db"), str(Path(tmp) / "proxy.key"))
        store.configure(
            worker_url="https://worker.example.invalid",
            secret="s" * 48,
            enabled=enabled,
            tracking_obfuscation=True,
            high_noise_decoy_enabled=high_noise,
        )
        return store

    def test_high_noise_off_is_zero_decoy_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._configured_store(tmp, high_noise=False)
            proxy = FakeProxy()
            result = fetch_high_noise_decoys(
                _inventory(), store=store, proxy=proxy,
                account_id="a", mailbox="INBOX", uid="1",
            )
            self.assertEqual(result["requests"], 0)
            self.assertEqual(proxy.calls, [])

    def test_high_noise_is_passive_only_bounded_and_never_navigation(self):
        self.assertEqual(_MAX_DECOY_REQUESTS_PER_MESSAGE, 4)
        self.assertEqual(_MAX_DECOY_REQUESTS_PER_DOMAIN, 2)
        self.assertEqual(_MAX_DECOY_CONCURRENCY, 2)
        self.assertEqual(_MAX_DECOY_RESPONSE_BYTES, 64_000)
        self.assertEqual(_MAX_DECOY_TOTAL_BYTES, 256_000)
        self.assertEqual(_DECOY_TIMEOUT_SECONDS, 3.0)
        self.assertEqual(_MAX_DECOY_EXECUTION_SECONDS, 7.0)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._configured_store(tmp)
            proxy = FakeProxy(body_size=_MAX_DECOY_RESPONSE_BYTES)
            result = fetch_high_noise_decoys(
                _inventory(), store=store, proxy=proxy,
                account_id="a", mailbox="INBOX", uid="7",
            )
            self.assertLessEqual(len(proxy.calls), _MAX_DECOY_REQUESTS_PER_MESSAGE)
            domains = Counter(url.split("/", 3)[2] for url, _ in proxy.calls)
            self.assertTrue(domains)
            self.assertTrue(all(count <= _MAX_DECOY_REQUESTS_PER_DOMAIN for count in domains.values()))
            called_urls = [url for url, _ in proxy.calls]
            self.assertFalse(any("reset" in url for url in called_urls))
            self.assertFalse(any("unsubscribe" in url for url in called_urls))
            self.assertFalse(any("/click" in url for url in called_urls))
            for _, kwargs in proxy.calls:
                self.assertEqual(kwargs["request_kind"], "decoy")
                self.assertEqual(kwargs["max_response_bytes"], _MAX_DECOY_RESPONSE_BYTES)
                self.assertLessEqual(kwargs["timeout_seconds"], _DECOY_TIMEOUT_SECONDS)
            self.assertLessEqual(result["response_bytes"], _MAX_DECOY_TOTAL_BYTES)

            events = store.decoy_events(account_id="a", mailbox="INBOX", uid="7", limit=20)
            self.assertEqual(len(events), len(proxy.calls))
            self.assertTrue(all("url_hash" in event for event in events))
            self.assertTrue(all("url" not in event for event in events))

    def test_decoy_redirect_is_recorded_not_followed_and_not_render_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._configured_store(tmp)
            proxy = FakeProxy(redirect=True)
            fetch_high_noise_decoys(
                _inventory(), store=store, proxy=proxy,
                account_id="a", mailbox="INBOX", uid="9",
            )
            events = store.decoy_events(account_id="a", mailbox="INBOX", uid="9", limit=20)
            self.assertTrue(events)
            self.assertTrue(all(event["http_status"] == 302 for event in events))
            self.assertTrue(all(event["redirect_location"] == "https://redirect.example/next" for event in events))
            with sqlite3.connect(store.db_path) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("privacy_proxy_decoy_events", names)
            self.assertNotIn("mailbox_cache_remote_resources", names)


class HighNoiseSurfaceAndWorkerV963Tests(unittest.TestCase):
    def test_mcp_surface_reuses_existing_command_and_read_status_is_masked(self):
        source = (ROOT / "src/postmaster/runtime_v963.py").read_text(encoding="utf-8")
        self.assertIn("high_noise_decoy_enabled", source)
        self.assertIn('core.mcp.add_tool(set_amp_account_state, name="set_amp_account_state")', source)
        self.assertIn('"mcp_command_count_expected": 90', source)
        self.assertNotIn("@mcp.tool", source)
        self.assertNotIn('result["privacy_proxy_secret"]', source)

    def test_webgui_setup_status_and_second_confirmation_hook(self):
        source = inspect.getsource(install_webgui_v963_high_noise)
        self.assertIn("High-noise decoy traffic", source)
        self.assertIn("default Off", source)
        self.assertIn("High-noise:", source)
        self.assertIn("fetch_high_noise_decoys", source)
        self.assertIn("fetch_passive_resources", source)
        self.assertNotIn("send_email", source)
        original = (ROOT / "src/postmaster/webgui_v963.py").read_text(encoding="utf-8")
        detail = original[original.index("def _detail"):original.index("def render_inbox_v963")]
        self.assertNotIn("fetch_high_noise_decoys", detail)
        self.assertIn("Prima conferma: nessuna risorsa è stata caricata", detail)
        self.assertIn("Conferma e carica HTML completo", detail)

    def test_worker_decoy_uses_same_authenticated_fetch_and_safety_guards(self):
        source = (ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js").read_text(encoding="utf-8")
        verify_pos = source.index("verifyRequest(request, env, bodyBytes)")
        route_pos = source.index('if (path !== "/fetch")')
        self.assertLess(verify_pos, route_pos)
        self.assertIn('requestKind === "decoy"', source)
        self.assertIn("decoy_requires_tracking_obfuscation", source)
        self.assertIn("decoy_target_scope_violation", source)
        self.assertIn("navigation_or_action_url_not_proxyable", source)
        self.assertIn('"redirector"', source)
        self.assertIn('redirect: "manual"', source)
        self.assertIn("assertPublicTarget(target)", source)
        self.assertIn("response_too_large", source)
        self.assertIn("HARD_MAX_RESPONSE_BYTES", source)
        self.assertIn("NONCE_GUARD", source)
        self.assertIn("PROXY_USER_AGENT", source)
        self.assertNotIn('headers: request.headers', source)
        self.assertNotIn('redirect: "follow"', source)

    def test_browser_full_html_remains_local_cache_only(self):
        source = (ROOT / "src/postmaster/webgui_v963.py").read_text(encoding="utf-8")
        self.assertIn("connect-src 'none'", source)
        self.assertIn("/dashboard/inbox/resource?", source)
        self.assertIn("referrerpolicy=\"no-referrer\"", source)
        self.assertNotIn("window.fetch", source)
        self.assertNotIn("XMLHttpRequest", source)


if __name__ == "__main__":
    unittest.main()
