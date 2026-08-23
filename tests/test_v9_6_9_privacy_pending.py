from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster.pending_approval_v969 import PendingApprovalStore
from postmaster.privacy_cache_v969 import PassiveContentService, install_hashed_resource_keys


class _ProxyStore:
    def status(self):
        return {"enabled": True, "high_noise_decoy_enabled": True}


class _Proxy:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("sensitive-origin-detail:" + url)
        return {
            "status": 200,
            "content_type": "image/png",
            "body": b"png",
            "redirect_location": "",
            "error": "",
        }


class _Base:
    def __init__(self, cache, proxy):
        self.cache = cache
        self.proxy = proxy

    def mailbox_cache_store(self):
        return self.cache

    def privacy_proxy_store(self):
        return _ProxyStore()

    def privacy_proxy_client(self):
        return self.proxy


def _inventory():
    return {
        "urls": [
            {
                "url": "https://img.example/a.png",
                "source_type": "img src",
                "source_snippet": '<img src="https://img.example/a.png">',
                "classification": "remote image",
                "tracking_score": 5,
                "passive_resource": True,
            },
            {
                "url": "https://cdn.example/bg.png",
                "source_type": "style-block url()",
                "source_snippet": "background:url(https://cdn.example/bg.png)",
                "classification": "remote image",
                "tracking_score": 0,
                "passive_resource": False,
            },
            {
                "url": "https://example.test/action/reset?token=secret",
                "source_type": "a href",
                "source_snippet": '<a href="https://example.test/action/reset?token=secret">',
                "classification": "action URL",
                "tracking_score": 0,
                "passive_resource": False,
            },
        ]
    }


class PassiveContentV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / "mailbox.db")
        self.cache = MailboxCacheStore(self.db_path)
        install_hashed_resource_keys(self.cache)

    def test_semantic_passive_fetch_cache_refresh_and_noise_boundary(self):
        proxy = _Proxy()
        service = PassiveContentService(_Base(self.cache, proxy))
        noise = {
            "requests": 1,
            "events": [{"http_status": 204, "error_state": ""}],
        }
        with patch("postmaster.privacy_cache_v969.fetch_high_noise_decoys", return_value=noise) as decoys:
            first = service.fetch_inventory(
                _inventory(), account_id="acct-personal", mailbox="INBOX", uid="7"
            )
            second = service.fetch_inventory(
                _inventory(), account_id="acct-personal", mailbox="INBOX", uid="7"
            )
            refreshed = service.fetch_inventory(
                _inventory(), account_id="acct-personal", mailbox="INBOX", uid="7", refresh=True
            )

        self.assertEqual(first["diagnostics"]["discovered"], 2)
        self.assertEqual(first["diagnostics"]["genuine_attempted"], 2)
        self.assertEqual(first["diagnostics"]["excluded_navigation_action"], 1)
        self.assertFalse(first["cache_only"])
        self.assertTrue(second["cache_only"])
        self.assertEqual(second["diagnostics"]["genuine_attempted"], 0)
        self.assertEqual(second["diagnostics"]["decoy_attempted"], 0)
        self.assertEqual(refreshed["diagnostics"]["genuine_attempted"], 2)
        self.assertEqual(decoys.call_count, 2)
        self.assertEqual(len(proxy.calls), 4)
        self.assertNotIn("action/reset", "\n".join(proxy.calls))

    def test_resource_key_is_opaque_and_contains_no_message_identity(self):
        url = "https://sensitive.example/pixel?email=reader@example.test"
        digest = hashlib.sha256(url.encode()).hexdigest()
        key = self.cache.resource_key("acct-personal", "INBOX", "123", digest)
        self.assertRegex(key, r"^r_[0-9a-f]{64}$")
        for value in ("acct-personal", "INBOX", "123", "reader@example.test", "sensitive.example"):
            self.assertNotIn(value, key)

    def test_negative_tombstone_persists_and_error_state_is_type_only(self):
        proxy = _Proxy(fail=True)
        first_service = PassiveContentService(_Base(self.cache, proxy))
        inventory = {
            "urls": [{
                "url": "https://sensitive.example/pixel?email=reader@example.test",
                "source_type": "img src",
                "source_snippet": '<img src="https://sensitive.example/pixel?email=reader@example.test">',
                "classification": "tracking pixel",
                "tracking_score": 80,
                "passive_resource": True,
            }]
        }
        with patch("postmaster.privacy_cache_v969.fetch_high_noise_decoys", return_value={"requests": 0, "events": []}):
            first = first_service.fetch_inventory(
                inventory, account_id="acct", mailbox="INBOX", uid="9"
            )
            restarted_cache = MailboxCacheStore(self.db_path)
            install_hashed_resource_keys(restarted_cache)
            second = PassiveContentService(_Base(restarted_cache, proxy)).fetch_inventory(
                inventory, account_id="acct", mailbox="INBOX", uid="9"
            )

        self.assertEqual(first["render_state"], "failure")
        self.assertTrue(second["cache_only"])
        self.assertEqual(len(proxy.calls), 1)
        digest = hashlib.sha256(inventory["urls"][0]["url"].encode()).hexdigest()
        row = restarted_cache.get_resource(
            restarted_cache.resource_key("acct", "INBOX", "9", digest)
        )
        self.assertEqual(row["error_state"], "RuntimeError")
        self.assertNotIn("sensitive.example", row["error_state"])
        self.assertNotIn("reader@example.test", row["error_state"])


class PendingApprovalV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "pending.db"
        self.now = [1000.0]

    def store(self):
        return PendingApprovalStore(self.db_path, ttl_seconds=300, clock=lambda: self.now[0])

    def test_restart_exact_binding_optional_preview_id_and_replay(self):
        binding = {
            "operation": "pin-version",
            "target": "v9.6.8",
            "current_selector": "latest",
            "current_build": "secret-build-state",
        }
        preview_id = self.store().issue("runtime_version_change", binding)
        self.assertTrue(preview_id.startswith("pv_"))
        restarted = self.store()
        self.assertFalse(restarted.consume_matching(
            "runtime_version_change", {**binding, "target": "v9.6.7"}, preview_id=preview_id
        ))
        self.assertFalse(restarted.consume_matching(
            "runtime_version_change", {**binding, "operation": "rollback-version"}, preview_id=preview_id
        ))
        self.assertTrue(restarted.consume_matching("runtime_version_change", binding))
        self.assertFalse(restarted.consume_matching(
            "runtime_version_change", binding, preview_id=preview_id
        ))

    def test_expiry_and_schema_do_not_store_plaintext_binding(self):
        binding = {
            "operation": "pin-version",
            "target": "v9.6.8",
            "current_build": "do-not-store-this-build",
        }
        preview_id = self.store().issue("runtime_version_change", binding)
        self.now[0] = 1301.0
        self.assertFalse(self.store().consume_matching(
            "runtime_version_change", binding, preview_id=preview_id
        ))
        with sqlite3.connect(self.db_path) as conn:
            columns = [row[1] for row in conn.execute(
                "PRAGMA table_info(mcp_pending_approvals_v969)"
            ).fetchall()]
            values = conn.execute(
                "SELECT preview_id,scope,binding_digest,created_at,expires_at,consumed_at "
                "FROM mcp_pending_approvals_v969"
            ).fetchall()
        self.assertEqual(
            columns,
            ["preview_id", "scope", "binding_digest", "created_at", "expires_at", "consumed_at"],
        )
        rendered = repr(values)
        self.assertNotIn("do-not-store-this-build", rendered)
        self.assertNotIn("pin-version", rendered)
        self.assertNotIn("v9.6.8", rendered)


if __name__ == "__main__":
    unittest.main()
