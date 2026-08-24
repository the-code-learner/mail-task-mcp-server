from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from postmaster.email_inventory_v963 import inventory_message
from postmaster.mailbox_cache_v963 import MailboxCacheStore
from postmaster.pending_approval_v969 import PendingApprovalStore
from postmaster.privacy_cache_v969 import (
    PassiveContentService,
    install_hashed_resource_keys,
)


class _ProxyStore:
    def __init__(self, *, high_noise: bool = True):
        self.high_noise = high_noise

    def status(self):
        return {
            "enabled": True,
            "tracking_obfuscation": True,
            "high_noise_decoy_enabled": self.high_noise,
        }


class _Proxy:
    def __init__(self, *, fail: bool = False, fail_urls: set[str] | None = None):
        self.fail = fail
        self.fail_urls = set(fail_urls or set())
        self.calls: list[str] = []

    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if self.fail or url in self.fail_urls:
            raise RuntimeError("sensitive-origin-detail:" + url)
        content_type = "text/css" if str(url).endswith(".css") else "image/png"
        body = b"body{background:url(https://nested.example/leak.png)}" if content_type == "text/css" else b"png"
        return {
            "status": 200,
            "content_type": content_type,
            "body": body,
            "redirect_location": "",
            "error": "",
        }


class _Base:
    def __init__(self, cache, proxy, *, high_noise: bool = True):
        self.cache = cache
        self.proxy = proxy
        self.proxy_store = _ProxyStore(high_noise=high_noise)

    def mailbox_cache_store(self):
        return self.cache

    def privacy_proxy_store(self):
        return self.proxy_store

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


def _raw_message(message_id: str, body_html: str, *, subject: str = "Subject") -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "sender@example.test"
    msg["To"] = "reader@example.test"
    msg["Date"] = "Mon, 24 Aug 2026 00:00:00 +0000"
    msg["Subject"] = subject
    msg.set_content("plain fallback")
    msg.add_alternative(body_html, subtype="html")
    return msg.as_bytes()


def _seed_message(
    cache: MailboxCacheStore,
    *,
    account_id: str,
    mailbox: str,
    uid: str,
    uidvalidity: int,
    raw: bytes,
) -> None:
    parsed = EmailMessage()
    # Header fields are duplicated explicitly because the store's stable identity deliberately
    # does not rely on Message-ID alone.
    from email import policy
    from email.parser import BytesParser

    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    row = {
        "message_id": str(parsed.get("Message-ID") or ""),
        "in_reply_to": str(parsed.get("In-Reply-To") or ""),
        "references": str(parsed.get("References") or ""),
        "date": str(parsed.get("Date") or ""),
        "from": str(parsed.get("From") or ""),
        "from_addresses": ["sender@example.test"],
        "to": str(parsed.get("To") or ""),
        "to_addresses": ["reader@example.test"],
        "cc": str(parsed.get("Cc") or ""),
        "cc_addresses": [],
        "subject": str(parsed.get("Subject") or ""),
    }
    cache.upsert_header(
        account_id=account_id,
        mailbox=mailbox,
        uid=uid,
        uidvalidity=uidvalidity,
        row=row,
        flags=[],
        size_bytes=len(raw),
        header_bytes=raw,
    )
    cache.store_body(account_id, mailbox, uid, raw, truncated=False)


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
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value=noise,
        ) as decoys:
            first = service.fetch_inventory(
                _inventory(), account_id="acct-personal", mailbox="INBOX", uid="7"
            )
            second = service.fetch_inventory(
                _inventory(), account_id="acct-personal", mailbox="INBOX", uid="7"
            )
            refreshed = service.fetch_inventory(
                _inventory(),
                account_id="acct-personal",
                mailbox="INBOX",
                uid="7",
                refresh=True,
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
        for value in (
            "acct-personal",
            "INBOX",
            "123",
            "reader@example.test",
            "sensitive.example",
        ):
            self.assertNotIn(value, key)

    def test_cache_identity_survives_mailbox_move_and_new_uid(self):
        body_html = '<img src="https://img.example/a.png">'
        raw = _raw_message("<move-stable@example.test>", body_html)
        _seed_message(
            self.cache,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            uidvalidity=10,
            raw=raw,
        )
        proxy = _Proxy()
        service = PassiveContentService(_Base(self.cache, proxy))
        inventory = inventory_message(body_html, "plain")
        first = service.fetch_inventory(
            inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            body_html=body_html,
        )
        self.assertEqual(first["diagnostics"]["genuine_attempted"], 1)
        self.assertEqual(len(proxy.calls), 1)

        # Simulate the same RFC822 message after MOVE/COPY: mailbox and UID changed, raw MIME did not.
        _seed_message(
            self.cache,
            account_id="acct",
            mailbox="Archive",
            uid="900",
            uidvalidity=77,
            raw=raw,
        )
        second = service.fetch_inventory(
            inventory,
            account_id="acct",
            mailbox="Archive",
            uid="900",
            body_html=body_html,
        )
        self.assertTrue(second["cache_only"])
        self.assertEqual(second["diagnostics"]["genuine_attempted"], 0)
        self.assertEqual(second["diagnostics"]["cache_hits"], 1)
        self.assertEqual(len(proxy.calls), 1)

        digest = hashlib.sha256("https://img.example/a.png".encode()).hexdigest()
        old_key = self.cache.resource_key("acct", "INBOX", "44", digest)
        moved_key = self.cache.resource_key("acct", "Archive", "900", digest)
        self.assertEqual(old_key, moved_key)
        rebound = self.cache.get_resource(moved_key)
        self.assertEqual(rebound["mailbox"], "Archive")
        self.assertEqual(str(rebound["uid"]), "900")

    def test_uid_reuse_for_different_message_does_not_inherit_old_cache(self):
        url = "https://img.example/a.png"
        old_html = f'<img src="{url}">'
        old_raw = _raw_message("<old@example.test>", old_html, subject="Old")
        _seed_message(
            self.cache,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            uidvalidity=10,
            raw=old_raw,
        )
        proxy = _Proxy()
        service = PassiveContentService(_Base(self.cache, proxy))
        inventory = inventory_message(old_html, "plain")
        service.fetch_inventory(
            inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            body_html=old_html,
        )
        digest = hashlib.sha256(url.encode()).hexdigest()
        old_key = self.cache.resource_key("acct", "INBOX", "44", digest)
        self.assertEqual(len(proxy.calls), 1)

        # Keep the old resource row in place, but replace UID 44 with a different RFC822 message
        # and UIDVALIDITY. Correct identity must compute a different key and perform a new fetch.
        new_raw = _raw_message("<new@example.test>", old_html, subject="Different")
        _seed_message(
            self.cache,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            uidvalidity=11,
            raw=new_raw,
        )
        new_key = self.cache.resource_key("acct", "INBOX", "44", digest)
        self.assertNotEqual(old_key, new_key)
        second = service.fetch_inventory(
            inventory,
            account_id="acct",
            mailbox="INBOX",
            uid="44",
            body_html=old_html,
        )
        self.assertFalse(second["cache_only"])
        self.assertEqual(second["diagnostics"]["genuine_attempted"], 1)
        self.assertEqual(len(proxy.calls), 2)

    def test_negative_tombstone_persists_and_error_state_is_type_only(self):
        proxy = _Proxy(fail=True)
        first_service = PassiveContentService(_Base(self.cache, proxy))
        inventory = {
            "urls": [
                {
                    "url": "https://sensitive.example/pixel?email=reader@example.test",
                    "source_type": "img src",
                    "source_snippet": '<img src="https://sensitive.example/pixel?email=reader@example.test">',
                    "classification": "tracking pixel",
                    "tracking_score": 80,
                    "passive_resource": True,
                }
            ]
        }
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value={"requests": 0, "events": []},
        ) as decoys:
            first = first_service.fetch_inventory(
                inventory, account_id="acct", mailbox="INBOX", uid="9"
            )
            restarted_cache = MailboxCacheStore(self.db_path)
            install_hashed_resource_keys(restarted_cache)
            second = PassiveContentService(
                _Base(restarted_cache, proxy)
            ).fetch_inventory(
                inventory, account_id="acct", mailbox="INBOX", uid="9"
            )

        self.assertEqual(first["render_state"], "failure")
        self.assertTrue(second["cache_only"])
        self.assertEqual(second["diagnostics"]["negative_cache_hits"], 1)
        self.assertEqual(second["diagnostics"]["decoy_attempted"], 0)
        self.assertEqual(len(proxy.calls), 1)
        self.assertEqual(decoys.call_count, 1)
        digest = hashlib.sha256(inventory["urls"][0]["url"].encode()).hexdigest()
        row = restarted_cache.get_resource(
            restarted_cache.resource_key("acct", "INBOX", "9", digest)
        )
        self.assertEqual(row["error_state"], "RuntimeError")
        self.assertNotIn("sensitive.example", row["error_state"])
        self.assertNotIn("reader@example.test", row["error_state"])

    def test_genuine_outcomes_are_not_changed_by_decoy_outcomes(self):
        first_url = "https://img.example/one.png"
        second_url = "https://img.example/two.png"
        inventory = {
            "urls": [
                {
                    "url": first_url,
                    "source_type": "img src",
                    "classification": "remote image",
                    "tracking_score": 5,
                    "passive_resource": True,
                },
                {
                    "url": second_url,
                    "source_type": "img src",
                    "classification": "remote image",
                    "tracking_score": 5,
                    "passive_resource": True,
                },
            ]
        }

        all_ok = PassiveContentService(_Base(self.cache, _Proxy()))
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value={"requests": 1, "events": [{"http_status": 0, "error_state": "TimeoutError"}]},
        ):
            success = all_ok.fetch_inventory(
                inventory,
                account_id="acct-success",
                mailbox="INBOX",
                uid="1",
            )
        self.assertEqual(success["render_state"], "success")
        self.assertEqual(success["diagnostics"]["genuine_succeeded"], 2)
        self.assertEqual(success["diagnostics"]["decoy_failed"], 1)

        mixed_proxy = _Proxy(fail_urls={second_url})
        mixed_service = PassiveContentService(_Base(self.cache, mixed_proxy))
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value={"requests": 0, "events": []},
        ):
            partial = mixed_service.fetch_inventory(
                inventory,
                account_id="acct-partial",
                mailbox="INBOX",
                uid="1",
            )
        self.assertEqual(partial["render_state"], "partial")
        self.assertEqual(partial["diagnostics"]["genuine_succeeded"], 1)
        self.assertEqual(partial["diagnostics"]["genuine_failed"], 1)

        failed_service = PassiveContentService(_Base(self.cache, _Proxy(fail=True)))
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value={"requests": 1, "events": [{"http_status": 204, "error_state": ""}]},
        ):
            failed = failed_service.fetch_inventory(
                inventory,
                account_id="acct-failed",
                mailbox="INBOX",
                uid="1",
            )
        self.assertEqual(failed["render_state"], "failure")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["diagnostics"]["genuine_succeeded"], 0)
        self.assertEqual(failed["diagnostics"]["decoy_succeeded"], 1)

    def test_full_rewrite_localizes_passive_resources_and_preserves_navigation(self):
        img = "https://img.example/a.png"
        bg = "https://cdn.example/bg.png"
        css = "https://cdn.example/mail.css"
        action = "https://example.test/action/reset?token=secret"
        body_html = (
            f'<link rel="stylesheet" href="{css}">'
            f'<img src="{img}">'
            f'<div style="background-image:url({bg})">Hello</div>'
            f'<a href="{action}">Reset password</a>'
        )
        inventory = inventory_message(body_html, "Hello")
        proxy = _Proxy()
        service = PassiveContentService(_Base(self.cache, proxy))
        with patch(
            "postmaster.privacy_cache_v969.fetch_high_noise_decoys",
            return_value={"requests": 0, "events": []},
        ):
            result = service.fetch_inventory(
                inventory,
                account_id="acct-render",
                mailbox="INBOX",
                uid="1",
                body_html=body_html,
            )
        self.assertEqual(result["render_state"], "success")
        rendered = result["rendered_html"]
        self.assertNotIn(img, rendered)
        self.assertNotIn(bg, rendered)
        self.assertNotIn(css, rendered)
        self.assertIn("/dashboard/inbox/resource?", rendered)
        self.assertIn(action, rendered)
        self.assertNotIn(action, "\n".join(proxy.calls))
        self.assertEqual(set(proxy.calls), {img, bg, css})


class PendingApprovalV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "pending.db"
        self.now = [1000.0]

    def store(self):
        return PendingApprovalStore(
            self.db_path,
            ttl_seconds=300,
            clock=lambda: self.now[0],
        )

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
        self.assertFalse(
            restarted.consume_matching(
                "runtime_version_change",
                {**binding, "target": "v9.6.7"},
                preview_id=preview_id,
            )
        )
        self.assertFalse(
            restarted.consume_matching(
                "runtime_version_change",
                {**binding, "operation": "rollback-version"},
                preview_id=preview_id,
            )
        )
        self.assertTrue(restarted.consume_matching("runtime_version_change", binding))
        self.assertFalse(
            restarted.consume_matching(
                "runtime_version_change",
                binding,
                preview_id=preview_id,
            )
        )

    def test_expiry_and_schema_do_not_store_plaintext_binding(self):
        binding = {
            "operation": "pin-version",
            "target": "v9.6.8",
            "current_build": "do-not-store-this-build",
        }
        preview_id = self.store().issue("runtime_version_change", binding)
        self.now[0] = 1301.0
        self.assertFalse(
            self.store().consume_matching(
                "runtime_version_change",
                binding,
                preview_id=preview_id,
            )
        )
        with sqlite3.connect(self.db_path) as conn:
            columns = [
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(mcp_pending_approvals_v969)"
                ).fetchall()
            ]
            values = conn.execute(
                "SELECT preview_id,scope,binding_digest,created_at,expires_at,consumed_at "
                "FROM mcp_pending_approvals_v969"
            ).fetchall()
        self.assertEqual(
            columns,
            [
                "preview_id",
                "scope",
                "binding_digest",
                "created_at",
                "expires_at",
                "consumed_at",
            ],
        )
        rendered = repr(values)
        self.assertNotIn("do-not-store-this-build", rendered)
        self.assertNotIn("pin-version", rendered)
        self.assertNotIn("v9.6.8", rendered)


if __name__ == "__main__":
    unittest.main()
