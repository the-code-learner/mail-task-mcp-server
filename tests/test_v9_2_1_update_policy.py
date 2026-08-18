from __future__ import annotations

import os
import unittest
from pathlib import Path


class V921UpdatePolicyTests(unittest.TestCase):
    KEYS = (
        "BRIDGE_BUILD",
        "POSTMASTER_REF",
        "POSTMASTER_VERSION",
        "POSTMASTER_REQUESTED_VERSION",
        "POSTMASTER_CHECK_UPDATES_ON_START",
        "POSTMASTER_FORCE_REFRESH",
    )

    def setUp(self) -> None:
        self.old = {key: os.environ.get(key) for key in self.KEYS}

    def tearDown(self) -> None:
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_build_status_reports_version_and_update_policy(self) -> None:
        import postmaster.server as server

        expected_version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
        expected_ref = f"v{expected_version}"
        os.environ.pop("BRIDGE_BUILD", None)
        os.environ["POSTMASTER_REF"] = expected_ref
        os.environ["POSTMASTER_VERSION"] = "latest"
        os.environ["POSTMASTER_REQUESTED_VERSION"] = "latest"
        os.environ["POSTMASTER_CHECK_UPDATES_ON_START"] = "false"
        os.environ["POSTMASTER_FORCE_REFRESH"] = "true"

        status = server.build_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["version"], expected_version)
        self.assertEqual(status["build"], expected_ref)
        self.assertEqual(status["requested_version"], "latest")
        self.assertFalse(status["check_updates_on_start"])
        self.assertTrue(status["force_refresh"])


if __name__ == "__main__":
    unittest.main()
