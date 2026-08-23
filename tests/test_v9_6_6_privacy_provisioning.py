from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from postmaster.email_privacy_v963 import PrivacyProxyClient, PrivacyProxyStore
from postmaster.privacy_provisioning_v966 import (
    MAX_PREVIOUS_SECRET_GRACE_SECONDS,
    PREVIOUS_SECRET_GRACE_SECONDS,
    PrivacyProxyProvisioning,
    provisioning_canonical,
)


ROOT = Path(__file__).resolve().parents[1]


class PrivacyProxyProvisioningV966Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = PrivacyProxyStore(str(root / "proxy.db"), str(root / "proxy.key"))
        self.service = PrivacyProxyProvisioning(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def _confirm(self, action: str, *, worker_url: str | None = None, post_ok=True, health_ok=True):
        preview = self.service.preview(action, worker_url=worker_url)
        token = preview["confirmation_token"]
        with patch.object(
            self.service,
            "_post_provision",
            return_value=(post_ok, 204 if post_ok else 401, "" if post_ok else "worker_provisioning_rejected"),
        ), patch.object(self.service, "_verify_health", return_value=(health_ok, 200 if health_ok else 401)):
            return self.service.execute(action, confirmation_token=token, worker_url=worker_url)

    def _prepare(self):
        result = self._confirm("prepare_provisioning", worker_url="https://worker.example")
        self.assertTrue(result["ok"])
        return result

    def _provision(self):
        self._prepare()
        result = self._confirm("provision")
        self.assertTrue(result["ok"])
        return result

    def test_prepare_returns_public_material_only_and_private_key_stays_encrypted(self):
        result = self._prepare()
        public = result["privacy_proxy_provisioning"]
        self.assertTrue(public["prepared"])
        self.assertTrue(public["public_key"])
        self.assertTrue(public["key_id"].startswith("pm-ed25519-"))
        self.assertTrue(public["fingerprint"].startswith("sha256:"))
        self.assertTrue(result["public_material_only"])
        self.assertNotIn("private_key", repr(result))
        self.assertNotIn("private_key_enc", repr(result))

        with self.store._connect() as conn:
            encoded = str(
                conn.execute(
                    "SELECT private_key_enc FROM privacy_proxy_provisioning WHERE singleton=1"
                ).fetchone()[0]
            )
        raw = self.store._fernet.decrypt(encoded.encode("ascii"))
        self.assertEqual(len(raw), 32)
        self.assertNotIn(base64.urlsafe_b64encode(raw).decode("ascii"), repr(result))

    def test_confirmation_is_preview_first_short_lived_and_one_time(self):
        preview = self.service.preview(
            "prepare_provisioning", worker_url="https://worker.example"
        )
        self.assertTrue(preview["approval_required"])
        self.assertFalse(preview["action_applied"])
        token = preview["confirmation_token"]
        first = self.service.execute(
            "prepare_provisioning",
            confirmation_token=token,
            worker_url="https://worker.example",
        )
        self.assertTrue(first["ok"])
        second = self.service.execute(
            "prepare_provisioning",
            confirmation_token=token,
            worker_url="https://worker.example",
        )
        self.assertFalse(second["ok"])
        self.assertTrue(second["approval_required"])

    def test_ed25519_signature_binds_origin_path_timestamp_nonce_body_generation_and_key_id(self):
        prepared = self._prepare()["privacy_proxy_provisioning"]
        body = json.dumps(
            {
                "generation": 1,
                "operation": "provision",
                "previous_secret_grace_seconds": PREVIOUS_SECRET_GRACE_SECONDS,
                "secret": "server-to-server-only",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        endpoint, headers = self.service._sign_headers(
            worker_url="https://worker.example",
            operation="provision",
            generation=1,
            body=body,
        )
        self.assertEqual(endpoint, "https://worker.example/provision")
        public_raw = base64.urlsafe_b64decode(
            prepared["public_key"] + "=" * (-len(prepared["public_key"]) % 4)
        )
        public_key = Ed25519PublicKey.from_public_bytes(public_raw)
        digest = hashlib.sha256(body).hexdigest()
        canonical = provisioning_canonical(
            method="POST",
            path="/provision",
            origin="https://worker.example",
            timestamp=headers["X-Postmaster-Provisioning-Timestamp"],
            nonce=headers["X-Postmaster-Provisioning-Nonce"],
            body_digest=digest,
            generation=1,
            operation="provision",
            key_id=prepared["key_id"],
        )
        signature = base64.urlsafe_b64decode(
            headers["X-Postmaster-Provisioning-Signature"]
            + "=" * (-len(headers["X-Postmaster-Provisioning-Signature"]) % 4)
        )
        public_key.verify(signature, canonical)
        for changed in (
            {"origin": "https://other.example"},
            {"path": "/other"},
            {"generation": 2},
            {"key_id": "wrong-key"},
            {"body_digest": "0" * 64},
        ):
            values = dict(
                method="POST",
                path="/provision",
                origin="https://worker.example",
                timestamp=headers["X-Postmaster-Provisioning-Timestamp"],
                nonce=headers["X-Postmaster-Provisioning-Nonce"],
                body_digest=digest,
                generation=1,
                operation="provision",
                key_id=prepared["key_id"],
            )
            values.update(changed)
            with self.assertRaises(InvalidSignature):
                public_key.verify(signature, provisioning_canonical(**values))

    def test_provision_pending_verify_active_and_secret_never_appears_in_result(self):
        self._prepare()
        preview = self.service.preview("provision")
        token = preview["confirmation_token"]
        with patch.object(self.service, "_post_provision", return_value=(False, 401, "worker_provisioning_rejected")):
            failed = self.service.execute("provision", confirmation_token=token)
        pending = self.service._pending()
        self.assertIsNotNone(pending)
        generated = pending.secret
        self.assertNotIn(generated, repr(failed))
        self.assertEqual(failed["phase"], "pending")
        self.assertTrue(failed["reconcile_required"])
        self.assertFalse(self.store.status()["secret_configured"])

        preview = self.service.preview("reconcile")
        with patch.object(self.service, "_post_provision", return_value=(True, 204, "")), patch.object(
            self.service, "_verify_health", return_value=(True, 200)
        ):
            reconciled = self.service.execute(
                "reconcile", confirmation_token=preview["confirmation_token"]
            )
        self.assertTrue(reconciled["ok"])
        self.assertTrue(reconciled["health_verified"])
        self.assertTrue(self.store.status()["secret_configured"])
        self.assertNotIn(generated, repr(reconciled))

    def test_interrupted_rotation_is_recoverable_and_generation_is_monotonic(self):
        self._provision()
        old_secret = self.store._secret()
        self.assertEqual(self.service.public_status()["generation"], 1)

        preview = self.service.preview("rotate")
        with patch.object(self.service, "_post_provision", return_value=(True, 204, "")), patch.object(
            self.service, "_verify_health", return_value=(False, 401)
        ):
            interrupted = self.service.execute(
                "rotate", confirmation_token=preview["confirmation_token"]
            )
        self.assertFalse(interrupted["ok"])
        self.assertTrue(interrupted["reconcile_required"])
        self.assertEqual(self.store._secret(), old_secret)
        self.assertEqual(self.service.public_status()["pending_generation"], 2)

        preview = self.service.preview("reconcile")
        with patch.object(self.service, "_post_provision", return_value=(True, 204, "")), patch.object(
            self.service, "_verify_health", return_value=(True, 200)
        ):
            recovered = self.service.execute(
                "reconcile", confirmation_token=preview["confirmation_token"]
            )
        self.assertTrue(recovered["ok"])
        self.assertEqual(self.service.public_status()["generation"], 2)
        self.assertNotEqual(self.store._secret(), old_secret)

    def test_deprovision_invalidates_active_state_without_exposing_secret(self):
        self._provision()
        active_secret = self.store._secret()
        preview = self.service.preview("deprovision")
        with patch.object(self.service, "_post_provision", return_value=(True, 204, "")):
            result = self.service.execute(
                "deprovision", confirmation_token=preview["confirmation_token"]
            )
        self.assertTrue(result["ok"])
        self.assertFalse(self.store.status()["secret_configured"])
        self.assertFalse(self.store.status()["enabled"])
        self.assertFalse(self.service.public_status()["provisioned"])
        self.assertEqual(self.service.public_status()["generation"], 2)
        self.assertNotIn(active_secret, repr(result))

    def test_hmac_health_fetch_contract_and_legacy_fallback_remain(self):
        self._provision()
        secret = self.store._secret()
        headers = PrivacyProxyClient._headers(secret, b"{}")
        self.assertRegex(headers["X-Postmaster-Signature"], r"^[0-9a-f]{64}$")
        source = (ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js").read_text()
        self.assertIn('env.POSTMASTER_PROXY_SECRET', source)
        self.assertIn('path === "/health"', source)
        self.assertIn('path !== "/fetch"', source)
        self.assertIn("active_secret", source)
        self.assertIn("previous_secret", source)

    def test_worker_fails_closed_and_enforces_replay_timestamp_key_generation_and_bounded_grace(self):
        source = (ROOT / "extras/cloudflare-email-privacy-proxy/src/index.js").read_text()
        for marker in (
            "provisioning_key_not_configured",
            "x-postmaster-provisioning-timestamp",
            "x-postmaster-provisioning-nonce",
            "x-postmaster-provisioning-key-id",
            "x-postmaster-provisioning-generation",
            "x-postmaster-provisioning-operation",
            "x-postmaster-provisioning-body-sha256",
            "x-postmaster-provisioning-signature",
            "return checkNonce(env, nonce, seconds, \"hmac\")",
            "generation !== currentGeneration + 1",
            "generation_conflict",
            "generation_out_of_order",
            "MAX_PREVIOUS_SECRET_GRACE_SECONDS = 300",
            'crypto.subtle.importKey("raw", keyBytes, { name: "Ed25519" }',
        ):
            self.assertIn(marker, source)
        self.assertLessEqual(PREVIOUS_SECRET_GRACE_SECONDS, MAX_PREVIOUS_SECRET_GRACE_SECONDS)
        self.assertEqual(MAX_PREVIOUS_SECRET_GRACE_SECONDS, 300)
        self.assertNotIn("first_claim", source.casefold())
        self.assertNotIn("tofu", source.casefold())


class ReleaseBoundaryV966CompatibilityTests(unittest.TestCase):
    def test_final_registry_preserves_v966_legacy_privacy_schema_inside_v967_surface(self):
        import postmaster.runtime as runtime

        tools = asyncio.run(runtime.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(len(by_name), 97)
        props = by_name["set_amp_account_state"].input_schema["properties"]
        self.assertIn("privacy_proxy_action", props)
        self.assertIn("privacy_proxy_confirm", props)
        self.assertTrue(
            {
                "privacy_proxy_worker_url",
                "privacy_proxy_secret",
                "privacy_proxy_enabled",
            }
            <= set(props)
        )
        self.assertIn(
            "privacy_proxy_action",
            inspect.signature(runtime._base.set_amp_account_state).parameters,
        )

    def test_yaml_is_unchanged_and_v966_historical_contract_remains_documented(self):
        def blob_sha(path: Path) -> str:
            data = path.read_bytes()
            return hashlib.sha1(
                f"blob {len(data)}\0".encode() + data, usedforsecurity=False
            ).hexdigest()

        self.assertEqual(
            blob_sha(ROOT / "postmaster-mcp.yml"),
            "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9",
        )
        changelog = (ROOT / "CHANGELOG.md").read_text()
        self.assertIn("## 9.6.6 - ", changelog)
        runtime_source = (ROOT / "src/postmaster/runtime.py").read_text()
        self.assertIn("install_runtime_v966", runtime_source)
        self.assertNotIn("@mcp.tool", (ROOT / "src/postmaster/runtime_v966.py").read_text())


if __name__ == "__main__":
    unittest.main()
