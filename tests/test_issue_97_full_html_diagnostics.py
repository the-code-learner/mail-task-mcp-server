"""Issue #97: Full HTML remote image diagnostics, self-hosted pixel and high noise."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from postmaster.email_privacy_v963 import PrivacyProxyClient
from postmaster.mail_bridge import MailBridgeError
from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster.privacy_cache_v969 import PassiveContentService, install_hashed_resource_keys
from postmaster.privacy_diagnostics_v98101 import (
    PrivacyFetchFailure, RemoteMessageLoadFailure, failure_category,
    is_postmaster_tracking_pixel, worker_failure_category,
)
from postmaster.runtime_v969 import install_runtime_v969_mcp
from postmaster.webgui_regressions_v971 import install_full_html_partial_success_v971

from test_v9_6_9_privacy_pending import _Base, _ProxyStore, _seed_message, _raw_message


SELF_PIXEL = "https://mail.example.test/track/open/do-not-log-the-tracking-token.gif"
HTML = '<html><body><p>Controlled email</p><img src="' + SELF_PIXEL + '" width="1" height="1"></body></html>'


class _Proxy:
    def __init__(self, *, result="ssrf_denied"):
        self.calls = []
        self.result = result

    def fetch(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.result in {"ok", "origin_403"} and kwargs["request_kind"] == "render":
            return {
                "status": 200 if self.result == "ok" else 403,
                "content_type": "image/gif" if self.result == "ok" else "text/plain",
                "body": b"GIF89a" if self.result == "ok" else b"",
                "error": "", "redirect_location": "",
            }
        raise PrivacyFetchFailure(self.result if self.result != "origin_403" else "origin_network_failed")


class Issue97FullHTMLTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"PUBLIC_EMAIL_BASE_URL": "https://mail.example.test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cache = MailboxCacheStore(str(Path(self.temp.name) / "mailbox.db"))
        install_hashed_resource_keys(self.cache)
        _seed_message(self.cache, account_id="recv", mailbox="INBOX", uid="7",
                      uidvalidity=7, raw=_raw_message("<issue97@sender.test>", HTML))

    def service(self, proxy):
        base = _Base(self.cache, proxy, high_noise=True)
        base.proxy_store.events = []
        base.proxy_store.record_decoy_event = lambda **kw: base.proxy_store.events.append(kw)
        return install_full_html_partial_success_v971(PassiveContentService(base))

    def test_same_postmaster_pixel_is_not_unconditionally_blocked(self):
        self.assertTrue(is_postmaster_tracking_pixel(
            SELF_PIXEL, "https://mail.example.test"))
        self.assertFalse(is_postmaster_tracking_pixel(
            SELF_PIXEL, "https://different.example.test"))
        self.assertFalse(is_postmaster_tracking_pixel(
            "https://mail.example.test/reset?token=sensitive",
            "https://mail.example.test"))
        self.assertEqual(worker_failure_category(422, "dns_resolved_to_blocked_address"), "ssrf_denied")

    def test_authorized_high_noise_self_hosted_denial_is_explained_without_tokens(self):
        proxy = _Proxy(result="ssrf_denied")
        service = self.service(proxy)
        with patch.object(service.base, "mailbox_cache_synchronizer") as synchronizer:
            synchronizer.return_value.ensure_body.return_value = {
                "body_html": HTML, "body": "Plain text",
            }
            fresh = service.fetch_message(account_id="recv", mailbox="INBOX", uid="7")
        diag = fresh["diagnostics"]
        self.assertEqual(fresh["render_state"], "partial")
        self.assertEqual(diag["genuine_failed"], 1)
        self.assertEqual(diag["decoy_attempted"], 2)
        self.assertEqual(diag["decoy_failed"], 2)
        self.assertEqual(diag["failure_categories"]["genuine_attempted"], {"ssrf_denied": 1})
        self.assertEqual(diag["failure_categories"]["decoy"], {"ssrf_denied": 2})
        self.assertEqual(diag["self_hosted_tracking_pixels_discovered"], 1)
        self.assertEqual(diag["self_hosted_tracking_pixels_failed"], 1)
        self.assertEqual(diag["resource_failures"][0]["category"], "ssrf_denied")
        self.assertEqual(len(proxy.calls), 3)
        self.assertNotIn("do-not-log-the-tracking-token", json.dumps(diag))
        self.assertNotIn("mail.example.test", json.dumps(diag))

        # A cache-only read and retry without refresh are both zero-origin-network.
        self.assertEqual(service.render_cached_message(
            account_id="recv", mailbox="INBOX", uid="7",
        )["diagnostics"]["failure_categories"]["cached"], {"ssrf_denied": 1})
        with patch.object(service.base, "mailbox_cache_synchronizer") as synchronizer:
            synchronizer.return_value.ensure_body.return_value = {
                "body_html": HTML, "body": "Plain text",
            }
            cached = service.fetch_message(account_id="recv", mailbox="INBOX", uid="7")
        self.assertEqual(cached["diagnostics"]["negative_cache_hits"], 1)
        self.assertEqual(cached["diagnostics"]["genuine_attempted"], 0)
        self.assertEqual(len(proxy.calls), 3)

    def test_explicit_refresh_retries_and_can_recover_a_denied_pixel(self):
        proxy = _Proxy(result="origin_network_failed")
        service = self.service(proxy)
        with patch.object(service.base, "mailbox_cache_synchronizer") as synchronizer:
            synchronizer.return_value.ensure_body.return_value = {
                "body_html": HTML, "body": "Plain text",
            }
            service.fetch_message(account_id="recv", mailbox="INBOX", uid="7")
            proxy.result = "ok"
            retried = service.fetch_message(
                account_id="recv", mailbox="INBOX", uid="7", refresh=True,
            )
        diag = retried["diagnostics"]
        self.assertEqual(retried["render_state"], "success")
        self.assertEqual(diag["genuine_succeeded"], 1)
        self.assertEqual(diag["self_hosted_tracking_pixels_failed"], 0)
        self.assertEqual(diag["failure_categories"]["cached"], {})
        self.assertIn('/dashboard/inbox/resource?key=', retried["rendered_html"])

    def test_origin_http_403_separate_from_worker_rejection(self):
        proxy = _Proxy(result="origin_403")
        service = self.service(proxy)
        with patch.object(service.base, "mailbox_cache_synchronizer") as synchronizer:
            synchronizer.return_value.ensure_body.return_value = {
                "body_html": HTML, "body": "Plain text",
            }
            result = service.fetch_message(account_id="recv", mailbox="INBOX", uid="7")
        self.assertEqual(result["diagnostics"]["failure_categories"]["cached"], {"origin_http_error": 1})
        self.assertEqual(result["diagnostics"]["resource_failures"][0]["origin_http_status"], 403)
        self.assertEqual(failure_category({"http_status": 403, "error_state": ""}), "origin_http_error")

    def test_proxy_client_classifies_worker_422_without_exposing_worker_message(self):
        store = SimpleNamespace(client_config=lambda: {
            "enabled": True, "high_noise_decoy_enabled": True, "tracking_obfuscation": True,
        })
        client = PrivacyProxyClient(store)
        with patch.object(client, "_post", return_value=httpx.Response(
            422, json={"error": "dns_resolved_to_blocked_address",
                       "secret": "do-not-expose"})):
            with self.assertRaises(PrivacyFetchFailure) as caught:
                client.fetch(SELF_PIXEL, classification="tracking pixel", tracking_score=90)
        self.assertEqual(caught.exception.category, "ssrf_denied")
        self.assertNotIn("do-not-expose", str(caught.exception))

    def test_initial_mailbridge_error_has_proven_zero_requests_and_safe_reason(self):
        base = _Base(self.cache, _Proxy(), high_noise=True)
        base.mailbox_cache_synchronizer = lambda: SimpleNamespace(
            ensure_body=lambda *args, **kwargs: (_ for _ in ()).throw(
                MailBridgeError("private imap URL and credentials")
            )
        )
        base.mail_client = lambda account: object()
        service = PassiveContentService(base)
        with self.assertRaises(RemoteMessageLoadFailure):
            service.fetch_message(account_id="recv", mailbox="INBOX", uid="7")
        core = SimpleNamespace(mcp=SimpleNamespace(
            remove_tool=lambda name: None,
            add_tool=lambda fn, **kwargs: None,
        ))
        base.passive_content_service_v969 = lambda: service
        with patch("postmaster.runtime_v969._install_runtime_v969_mcp", return_value={}):
            install_runtime_v969_mcp(base, core, lambda: {})
        failed = base.fetch_email_remote_content(
            "INBOX", "7", "recv", authorize_remote_fetch=True,
        )
        self.assertEqual(failed["network_requests_performed"], 0)
        self.assertEqual(failed["failure_stage"], "message_load")
        self.assertEqual(failed["error_category"], "message_load_failed")
        self.assertNotIn("private imap", json.dumps(failed))

        service.fetch_message = lambda **kw: (_ for _ in ()).throw(
            RuntimeError("error containing token")
        )
        unexpected = base.fetch_email_remote_content(
            "INBOX", "7", "recv", authorize_remote_fetch=True,
        )
        self.assertIsNone(unexpected["network_requests_performed"])
        self.assertEqual(unexpected["failure_stage"], "unknown")
        self.assertNotIn("error containing token", json.dumps(unexpected))

    def test_worker_keeps_public_target_ssrf_checks_and_no_raw_error_echo(self):
        worker = (Path(__file__).resolve().parents[1] /
                  "extras/cloudflare-email-privacy-proxy/src/index.js").read_text("utf-8")
        self.assertIn("await assertPublicTarget(original)", worker)
        self.assertIn("await assertPublicTarget(target)", worker)
        self.assertIn('redirect: "manual"', worker)
        self.assertIn('safeFetchFailure(error)', worker)
        self.assertIn('"origin_timeout"', worker)
        self.assertNotIn('return json({ error: String(error.message || error) }, 422)', worker)


if __name__ == "__main__":
    unittest.main()
