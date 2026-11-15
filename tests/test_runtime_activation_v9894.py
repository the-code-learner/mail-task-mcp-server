"""Beta 9.8.9 activation is confirmed only by running build, never selector intent.\n\nFinal beta.4 exact-commit CI smoke contract.\n"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from postmaster import runtime_control, runtime_v967


class _Base:
    @staticmethod
    def _project_version():
        return "9.8.9-beta.4"


class ActivationTests(unittest.TestCase):
    def snapshot(self, selector, marker=""):
        env = {
            "BRIDGE_BUILD": "v9.8.9-beta.4",
            "POSTMASTER_VERSION": "latest",
            "POSTMASTER_BOOTSTRAP_CONTROL_V2": marker,
        }
        with patch.dict(os.environ, env), patch.object(
            runtime_control, "read_control", return_value={"selector": selector}
        ):
            return runtime_v967._local_runtime_status(_Base(), lambda **kwargs: {})

    def test_current_beta_requires_matching_running_version(self):
        status = self.snapshot("v9.8.9-beta.4", "true")
        self.assertEqual(status["update_activation_status"], "active")
        self.assertTrue(status["update_activation_verified"])
        self.assertTrue(status["bootstrap_control_supported"])
        self.assertNotIn("update_activation_warning", status)

    def test_legacy_bootstrap_reports_mismatch(self):
        status = self.snapshot("v9.8.9-beta.5")
        self.assertEqual(status["update_activation_status"], "bootstrap_unverified")
        self.assertFalse(status["update_activation_verified"])
        self.assertFalse(status["bootstrap_control_supported"])
        self.assertIn("Portainer", status["update_activation_warning"])

    def test_new_bootstrap_reports_pending_not_installed(self):
        status = self.snapshot("v9.8.9-beta.5", "true")
        self.assertEqual(status["update_activation_status"], "pending_activation")
        self.assertFalse(status["update_activation_verified"])

    def test_follow_stable_does_not_claim_specific_version(self):
        status = self.snapshot("latest", "true")
        self.assertEqual(status["update_activation_status"], "following_stable")
        self.assertFalse(status["update_activation_verified"])


if __name__ == "__main__":
    unittest.main()
