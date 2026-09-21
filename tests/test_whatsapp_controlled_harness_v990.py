from __future__ import annotations

from dataclasses import asdict
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


def _load_harness():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "whatsapp_v990_controlled_interop.py"
    spec = importlib.util.spec_from_file_location("whatsapp_v990_controlled_interop_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load controlled WhatsApp interop harness")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControlledInteropHarnessV990Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = _load_harness()

    def test_evidence_schema_contains_only_non_secret_release_signals(self):
        evidence = asdict(self.harness.Evidence())
        self.assertIn("controlled_account_interop_observed", evidence)
        self.assertIn("group_sender_key_interop_observed", evidence)
        self.assertIn("media_interop_observed", evidence)
        self.assertIn("receipt_policy_observed", evidence)
        forbidden_fragments = (
            "jid",
            "qr",
            "auth",
            "private",
            "secret",
            "token",
            "payload",
            "filename",
            "phone",
        )
        for key in evidence:
            lowered = key.lower()
            self.assertFalse(
                any(fragment in lowered for fragment in forbidden_fragments),
                f"Evidence field must not expose controlled-account identifiers or secrets: {key}",
            )

    def test_harness_file_store_keeps_payloads_in_memory_and_counts_inbound(self):
        store = self.harness.HarnessFileStore()
        info, outbound = store.raw_bytes("controlled-interop-document")
        self.assertEqual(info["media_type"], "text/plain")
        self.assertIn(b"controlled WhatsApp", outbound)

        saved = store.save_bytes(
            owner_id="controlled-interop",
            filename="peer.txt",
            data=b"inbound",
            media_type="text/plain",
            tags=["whatsapp", "inbound"],
        )
        self.assertEqual(store.inbound_saved, 1)
        saved_info, saved_bytes = store.raw_bytes(saved["id"])
        self.assertEqual(saved_info["size_bytes"], 7)
        self.assertEqual(saved_bytes, b"inbound")

    def test_secret_file_writer_creates_owner_only_permissions(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "auth.key"
            self.harness._write_secret(path, b"k" * 32)
            self.assertEqual(path.read_bytes(), b"k" * 32)
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_full_acceptance_defaults_require_every_remote_observation(self):
        self.assertTrue(self.harness._bool("__POSTMASTER_TEST_UNSET_TRUE__", True))
        self.assertFalse(self.harness._bool("__POSTMASTER_TEST_UNSET_FALSE__", False))


if __name__ == "__main__":
    unittest.main()
