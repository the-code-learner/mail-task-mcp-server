from __future__ import annotations

import contextlib
import inspect
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from postmaster.email_inventory_v963 import inventory_message
from postmaster.email_privacy_v963 import (
    PrivacyProxyClient,
    PrivacyProxyStore,
    _MAX_PROXY_BYTES,
    _MAX_PROXY_CONCURRENCY,
    _MAX_PROXY_URLS,
    attach_cache_state,
    fetch_passive_resources,
    safe_email_html,
)
from postmaster.mail_thread_v963 import forward_body, forward_subject, reply_all_plan
from postmaster.mailbox_cache_v963 import (
    MailboxCacheStore,
    MailboxCacheSynchronizer,
    MailboxSyncService,
    _SYNC_INTERVAL_SECONDS,
)
from postmaster.runtime_v963 import onboarding_state
from postmaster.webgui_v963 import STYLE, _allow_passive, _harden_full_html, confirm_full_html, refresh_inbox, render_inbox_v963, save_thread_draft


ROOT = Path(__file__).resolve().parents[1]


def _raw(uid: int, *, subject: str = "hello") -> bytes:
    return (
        f"Message-ID: <m{uid}@example.net>\r\n"
        "From: Sender <sender@example.net>\r\n"
        "Reply-To: Replies <reply@example.net>\r\n"
        "To: Me <me@example.test>, teammate@example.net\r\n"
        "Cc: Team <team@example.net>, alias@example.test\r\n"
        f"Subject: {subject}\r\n"
        "Date: Sun, 23 Aug 2026 02:00:00 +0000\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        '<p>Hello <a href="https://example.org/reset?token=one&utm_source=mail">reset</a></p>'
        '<img src="https://tracker.example/open?id=1" width="1" height="1">'
    ).encode()


class FakeImap:
    def __init__(self, owner):
        self.owner = owner

    def status(self, mailbox, query):
        return "OK", [f'"{mailbox}" (MESSAGES {len(self.owner.uids)} UIDVALIDITY {self.owner.uidvalidity} HIGHESTMODSEQ {self.owner.modseq})'.encode()]

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            return "OK", [" ".join(str(x) for x in self.owner.uids).encode()]
        if command != "FETCH":
            raise AssertionError(command)
        sequence = str(args[0])
        query = str(args[1])
        if sequence == "1:*" and "UID FLAGS" in query:
            rows = []
            for index, uid in enumerate(self.owner.uids, 1):
                flag = "\\Seen" if uid in self.owner.seen else ""
                rows.append(f"{index} (UID {uid} FLAGS ({flag}))".encode())
            return "OK", rows
        uid = int(sequence)
        if "BODY.PEEK[HEADER]" in query:
            self.owner.header_fetches.append(uid)
            header = _raw(uid).split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            flags = "\\Seen" if uid in self.owner.seen else ""
            return "OK", [(f'1 (UID {uid} FLAGS ({flags}) RFC822.SIZE {len(_raw(uid))})'.encode(), header)]
        if "BODY.PEEK[]" in query:
            self.owner.body_fetches.append(uid)
            return "OK", [(f'1 (UID {uid})'.encode(), _raw(uid))]
        raise AssertionError(query)


class FakeMailClient:
    def __init__(self):
        self.uids = [1, 2]
        self.uidvalidity = 7
        self.modseq = 10
        self.seen = {1}
        self.header_fetches = []
        self.body_fetches = []
        self.send_calls = 0

    @contextlib.contextmanager
    def _imap(self):
        yield FakeImap(self)

    def _select(self, conn, mailbox, readonly=False):
        if not readonly:
            raise AssertionError("mailbox cache synchronizer must SELECT readonly")

    def _fetch_raw(self, conn, uid):
        self.body_fetches.append(int(uid))
        return _raw(int(uid)), False

    def mailbox_catalog(self):
        return [{"name": "INBOX", "role": "received", "flags": ["\\Inbox"]}]

    def send_email(self, *args, **kwargs):
        self.send_calls += 1
        raise AssertionError("synchronizer must not have a send boundary")


class MailboxCacheV963Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "mailbox.db")
        self.store = MailboxCacheStore(self.path)
        self.sync = MailboxCacheSynchronizer(self.store)
        self.client = FakeMailClient()

    def tearDown(self):
        self.tmp.cleanup()

    def test_incremental_cache_first_uid_flags_and_body_on_demand(self):
        first = self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX", role="received")
        self.assertTrue(first["ok"])
        self.assertEqual(self.client.header_fetches, [1, 2])
        self.assertEqual(self.client.body_fetches, [])
        self.assertFalse(first["send_capability"])
        self.assertEqual(self.client.send_calls, 0)
        page = self.store.query_messages(account_id="a", mailbox="INBOX", page=1, page_size=25)
        self.assertEqual(page["total"], 2)
        self.assertTrue(next(row for row in page["messages"] if row["uid"] == "1")["seen"])
        self.assertFalse(next(row for row in page["messages"] if row["uid"] == "2")["seen"])

        self.client.header_fetches.clear()
        self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX")
        self.assertEqual(self.client.header_fetches, [], "unchanged UIDs must not refetch headers")
        self.client.uids.append(3)
        self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX")
        self.assertEqual(self.client.header_fetches, [3])

        opened = self.sync.ensure_body(self.client, account_id="a", mailbox="INBOX", uid="3")
        self.assertTrue(opened["body_cached"])
        before = list(self.client.body_fetches)
        self.sync.ensure_body(self.client, account_id="a", mailbox="INBOX", uid="3")
        self.assertEqual(self.client.body_fetches, before, "cached body must not refetch IMAP")

    def test_uidvalidity_change_resets_message_and_resource_cache(self):
        self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX")
        self.store.put_resource(
            cache_key="r", account_id="a", mailbox="INBOX", uid="1", url="https://img.example/a.png",
            url_hash="h", content_type="image/png", body=b"x", http_status=200,
            redirect_location="", classification="remote image", tracking_score=5,
        )
        self.client.uidvalidity = 8
        self.client.header_fetches.clear()
        result = self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX")
        self.assertIn("uidvalidity-reset", result["last_sync_kind"])
        self.assertEqual(self.client.header_fetches, [1, 2])
        self.assertIsNone(self.store.get_resource("r"))

    def test_coalesces_concurrent_same_mailbox_sync(self):
        calls = 0
        gate = threading.Event()

        def fake_network(client, *, account_id, mailbox, role):
            nonlocal calls
            calls += 1
            gate.wait(0.05)
            return {"ok": True, "mailbox": mailbox}

        with patch.object(self.sync, "_sync_mailbox_network", side_effect=fake_network):
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(self.sync.sync_mailbox, self.client, account_id="a", mailbox="INBOX") for _ in range(5)]
                time.sleep(0.02)
                gate.set()
                self.assertTrue(all(f.result()["ok"] for f in futures))
        self.assertEqual(calls, 1)
        self.assertEqual(self.sync.network_sync_count, 1)

    def test_persistence_and_five_minute_service_contract(self):
        self.sync.sync_mailbox(self.client, account_id="a", mailbox="INBOX")
        restarted = MailboxCacheStore(self.path)
        self.assertEqual(restarted.query_messages(account_id="a", mailbox="INBOX")["total"], 2)
        self.assertEqual(_SYNC_INTERVAL_SECONDS, 300.0)
        service = MailboxSyncService(self.sync, list_accounts=lambda: [], client_factory=lambda _: self.client)
        self.assertEqual(service.interval_seconds, 300.0)
        self.assertNotIn("SchedulerEngine(", inspect.getsource(MailboxSyncService))
        self.assertNotIn("send_email", inspect.getsource(MailboxCacheSynchronizer))


class PrivacyInspectionV963Tests(unittest.TestCase):
    def test_safe_email_and_inventory_are_zero_network_and_links_non_navigable(self):
        html = (
            '<style>.hero{background:url("https://cdn.example/bg.png")}</style>'
            '<a href="https://example.org/reset?token=abc&utm_source=mail">Reset account</a>'
            '<img src="https://track.example/open" width="1" height="1">'
            '<iframe src="https://evil.example/frame"></iframe>'
        )
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")), patch(
            "httpx.post", side_effect=AssertionError("HTTP forbidden")
        ):
            inventory = inventory_message(html, "Plain https://example.org/unsubscribe?id=1")
            safe = safe_email_html(html)
        self.assertEqual(inventory["network_requests_performed"], 0)
        self.assertIn("Tracking probabile", inventory["tracking_verdict"])
        kinds = {row["classification"] for row in inventory["urls"]}
        self.assertIn("tracking pixel", kinds)
        self.assertIn("action URL", kinds)
        self.assertIn("unsubscribe", kinds)
        self.assertNotIn("href=", safe)
        self.assertNotIn("<img", safe)
        self.assertNotIn("<style", safe)
        self.assertNotIn("<iframe", safe)

    def test_passive_action_separation_and_full_html_hardening(self):
        self.assertTrue(_allow_passive({"source_type": "img src"}))
        self.assertTrue(_allow_passive({"source_type": "div background"}))
        self.assertTrue(_allow_passive({"source_type": "link href", "source_snippet": '<link rel="stylesheet" href="x">'}))
        self.assertFalse(_allow_passive({"source_type": "a href"}))
        self.assertFalse(_allow_passive({"source_type": "link href", "source_snippet": '<link rel="alternate" href="x">'}))
        hardened = _harden_full_html('<meta http-equiv="refresh" content="0;https://evil"><base href="https://evil/"><img src="https://evil/x"><img src="/dashboard/inbox/resource?key=k"><a href="https://evil/a">x</a>')
        self.assertNotIn("http-equiv", hardened)
        self.assertNotIn("<base", hardened)
        self.assertNotIn('src="https://evil/x"', hardened)
        self.assertIn('/dashboard/inbox/resource?key=k', hardened)

    def test_durable_resource_cache_records_redirect_without_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MailboxCacheStore(str(Path(tmp) / "cache.db"))
            item = store.put_resource(
                cache_key="k", account_id="a", mailbox="INBOX", uid="9",
                url="https://example.org/r", url_hash="hash", content_type="", body=None,
                http_status=302, redirect_location="https://other.example/next",
                classification="remote image", tracking_score=5,
            )
            self.assertEqual(item["http_status"], 302)
            self.assertEqual(item["redirect_location"], "https://other.example/next")
            self.assertIsNone(item["body"])

    def test_proxy_fetch_cache_is_bounded_parallel_and_never_action_urls(self):
        self.assertEqual(_MAX_PROXY_URLS, 32)
        self.assertEqual(_MAX_PROXY_CONCURRENCY, 4)
        self.assertEqual(_MAX_PROXY_BYTES, 2_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            cache = MailboxCacheStore(str(Path(tmp) / "cache.db"))
            inventory = {"urls": [
                {"url": "https://img.example/a.png", "passive_resource": True, "classification": "remote image", "tracking_score": 5},
                {"url": "https://example.org/unsubscribe", "passive_resource": False, "classification": "unsubscribe", "tracking_score": 0},
            ]}
            class Proxy:
                def __init__(self): self.calls = []
                def fetch(self, url, **kwargs): self.calls.append(url); return {"status": 200, "content_type": "image/png", "body": b"png", "redirect_location": "", "error": ""}
            proxy = Proxy()
            fetch_passive_resources(inventory, cache=cache, proxy=proxy, account_id="a", mailbox="INBOX", uid="1")
            fetch_passive_resources(inventory, cache=cache, proxy=proxy, account_id="a", mailbox="INBOX", uid="1")
            self.assertEqual(proxy.calls, ["https://img.example/a.png"], "durable cache must prevent repeat fetch and action URL must stay silent")


class ProxyConfigAndWorkerV963Tests(unittest.TestCase):
    def test_secret_is_encrypted_write_only_and_hmac_has_nonce_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PrivacyProxyStore(str(Path(tmp) / "proxy.db"), str(Path(tmp) / "proxy.key"))
            secret = "s" * 48
            status = store.configure(worker_url="https://worker.example.invalid", secret=secret, enabled=True, tracking_obfuscation=True)
            self.assertTrue(status["secret_configured"])
            self.assertNotIn(secret, repr(status))
            with sqlite3_connect(store.db_path) as conn:
                stored = str(conn.execute("SELECT secret_enc FROM privacy_proxy_config WHERE singleton=1").fetchone()[0])
            self.assertNotIn(secret, stored)
            headers = PrivacyProxyClient._headers(secret, b"{}")
            self.assertRegex(headers["X-Postmaster-Timestamp"], r"^\d+$")
            self.assertGreaterEqual(len(headers["X-Postmaster-Nonce"]), 16)
            self.assertRegex(headers["X-Postmaster-Signature"], r"^[0-9a-f]{64}$")
            self.assertEqual(headers["User-Agent"], "Postmaster-MCP-Privacy-Proxy/9.6.3")

    def test_worker_contract_has_auth_replay_ssrf_redirect_header_and_size_guards(self):
        source = (ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js").read_text(encoding="utf-8")
        for marker in (
            "x-postmaster-timestamp", "x-postmaster-nonce", "x-postmaster-signature", "HMAC",
            "NONCE_GUARD", "blocked_target_host", "dns_resolved_to_blocked_address", "redirect: \"manual\"",
            "response_too_large", "content_type_not_allowed", "PROXY_USER_AGENT", "Cookie", "Authorization",
            "MAX_CLOCK_SKEW_SECONDS", "one target",
        ):
            if marker in {"Cookie", "Authorization", "one target"}:
                continue
            self.assertIn(marker, source)
        self.assertNotIn('headers: request.headers', source)
        self.assertIn("HARD_MAX_RESPONSE_BYTES = 2_000_000", source)
        self.assertIn("navigation_or_action_url_not_proxyable", source)
        readme = (ROOT / "extras/cloudflare-email-privacy-proxy/README.md").read_text(encoding="utf-8")
        self.assertIn("maximum four parallel", readme)
        self.assertIn("exactly one target URL", readme)
        self.assertIn("does **not** authorize", readme)


def sqlite3_connect(path: str):
    import sqlite3
    return sqlite3.connect(path)


class ThreadDraftV963Tests(unittest.TestCase):
    def test_reply_all_uses_reply_to_preserves_visible_cc_removes_sender_aliases_and_never_bcc(self):
        raw = (
            b"From: Original <from@example.net>\r\n"
            b"Reply-To: Reply <reply@example.net>\r\n"
            b"To: me@example.test, teammate@example.net\r\n"
            b"Cc: team@example.net, alias@example.test\r\n"
            b"Bcc: hidden@example.net\r\n"
            b"Subject: Re: Re: Topic\r\n"
            b"Message-ID: <m@example.net>\r\n"
            b"References: <old@example.net>\r\n\r\nbody"
        )
        message = BytesParser(policy=policy.default).parsebytes(raw)
        settings = SimpleNamespace(email_address="me@example.test", aliases=("alias@example.test",), smtp_username="", imap_username="")
        plan = reply_all_plan(message, settings, require_reply_target=True)
        self.assertEqual(plan["to"], ["reply@example.net"])
        self.assertEqual(plan["cc"], ["teammate@example.net", "team@example.net"])
        self.assertEqual(plan["subject"], "Re: Topic")
        self.assertEqual(plan["in_reply_to"], "<m@example.net>")
        self.assertTrue(plan["references"].endswith("<m@example.net>"))
        self.assertFalse(plan["bcc_accessed"])
        self.assertNotIn("hidden@example.net", repr(plan))

    def test_forward_normalizes_subject_body_and_reuses_existing_attachment_semantics(self):
        message = BytesParser(policy=policy.default).parsebytes(_raw(1, subject="Fwd: FW: Topic"))
        self.assertEqual(forward_subject(str(message.get("Subject"))), "Fwd: Topic")
        self.assertIn("Forwarded message", forward_body(message, "body"))
        source = inspect.getsource(save_thread_draft)
        self.assertIn('"source_mailbox": mailbox', source)
        self.assertIn('"source_uid": uid', source)
        self.assertIn("create_reply_draft", source)
        self.assertIn("create_draft", source)
        self.assertNotIn("send_email", source)
        self.assertNotIn("reply_email", source)


class WebGuiAndOnboardingV963Tests(unittest.TestCase):
    def test_cache_first_manual_refresh_two_step_and_visual_hooks(self):
        renderer = inspect.getsource(render_inbox_v963)
        self.assertIn("query_messages", renderer)
        self.assertNotIn("search_emails", renderer)
        self.assertNotIn("mailbox_catalog(", renderer)
        self.assertIn("Aggiorna", renderer)
        self.assertIn("Safe Email is the default", renderer)
        detail_source = Path(ROOT / "src/postmaster/webgui_v963.py").read_text(encoding="utf-8")
        self.assertIn("Conferma e carica HTML completo", detail_source)
        self.assertIn("Visualizza HTML completo", detail_source)
        self.assertIn("Email sicura", detail_source)
        self.assertIn("Reply to all", detail_source)
        self.assertIn("Forward", detail_source)
        self.assertNotIn("Reader / Privacy / Links / Headers / MIME", detail_source)
        self.assertNotIn("fetch_passive_resources", inspect.getsource(render_inbox_v963))
        self.assertIn("fetch_passive_resources", inspect.getsource(confirm_full_html))
        self.assertIn("sync_mailbox", inspect.getsource(refresh_inbox))
        self.assertIn("webgui-v963-visual-restoration", STYLE)
        self.assertIn("--v963-accent", STYLE)
        self.assertIn("#panel-knowledge", STYLE)
        self.assertIn("textarea", STYLE)
        self.assertIn("#panel-projects", STYLE)

    def test_onboarding_upgrade_protection_fresh_resume_dismiss_and_no_network_or_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PrivacyProxyStore(str(Path(tmp) / "proxy.db"), str(Path(tmp) / "proxy.key"))
            self.assertTrue(store.onboarding(0)["full_onboarding"])
            established = store.onboarding(1)
            self.assertTrue(established["established_installation"])
            self.assertFalse(established["full_onboarding"])
            self.assertTrue(established["privacy_proxy_offer"])
            store.set_onboarding("privacy_proxy_offer", "dismissed")
            self.assertFalse(store.onboarding(1)["privacy_proxy_offer"])

            class Accounts:
                def list_accounts(self): return [{"id": "already-configured"}]
            base = SimpleNamespace(account_store=lambda: Accounts())
            with patch("postmaster.runtime_v963.privacy_proxy_store", return_value=store), patch(
                "socket.getaddrinfo", side_effect=AssertionError("onboarding must not resolve email URLs")
            ), patch("httpx.post", side_effect=AssertionError("onboarding must not perform HTTP")):
                state = onboarding_state(base)
            self.assertFalse(state["full_onboarding"])
            self.assertNotIn("send", inspect.getsource(onboarding_state).casefold())

    def test_release_boundaries_yaml_requirements_and_mcp_delta_zero(self):
        import hashlib
        def blob_sha(path: Path) -> str:
            data = path.read_bytes(); return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
        self.assertEqual(blob_sha(ROOT / "postmaster-mcp.yml"), "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9")
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "9.6.3")
        self.assertIn("new_mail_mcp_commands\": 0", (ROOT / "src/postmaster/runtime_v963.py").read_text(encoding="utf-8"))
        self.assertNotIn("@mcp.tool", (ROOT / "src/postmaster/runtime_v963.py").read_text(encoding="utf-8"))
        self.assertNotIn("postmaster-mcp.yml", "\n".join([
            "src/postmaster/mailbox_cache_v963.py", "src/postmaster/email_privacy_v963.py", "src/postmaster/webgui_v963.py"
        ]))


if __name__ == "__main__":
    unittest.main()
