from __future__ import annotations

import os
import unittest


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

        os.environ.pop("BRIDGE_BUILD", None)
        os.environ["POSTMASTER_REF"] = "v9.2.1"
        os.environ["POSTMASTER_VERSION"] = "latest"
        os.environ["POSTMASTER_REQUESTED_VERSION"] = "latest"
        os.environ["POSTMASTER_CHECK_UPDATES_ON_START"] = "false"
        os.environ["POSTMASTER_FORCE_REFRESH"] = "true"

        status = server.build_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["version"], "9.2.1")
        self.assertEqual(status["build"], "v9.2.1")
        self.assertEqual(status["requested_version"], "latest")
        self.assertFalse(status["check_updates_on_start"])
        self.assertTrue(status["force_refresh"])


if __name__ == "__main__":
    unittest.main()
