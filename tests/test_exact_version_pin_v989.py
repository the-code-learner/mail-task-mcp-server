"""Regression tests for the exact release selector in Settings."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from postmaster.webgui_versions_v984 import versions_settings, _release_snapshot, VERSIONS_SCRIPT
from postmaster import release_channels_v986 as channels


def snapshot(channel="stable_beta"):
    return {
        "ok": True, "release_channel": channel,
        "current_version": "9.8.9-beta.15", "selected_target": "v9.8.9-beta.15",
        "activation_mismatch": False, "bootstrap_control_supported": True,
        "stable": "v9.8.8", "beta": "v9.8.9-beta.15",
        "stable_update": True, "beta_update": False,
        "beta_allowed": channel == "stable_beta",
        "release_tags": ["v9.8.9-beta.15", "v9.8.9-beta.14", "v9.8.8", "v9.8.7"],
        "checked_at": "2026-11-14T22:00:00+00:00", "check_status": "ok",
    }


class ExactPinTests(unittest.TestCase):
    def setUp(self):
        self.base = MagicMock()
        self.base._csrf_value.return_value = "csrf-test"

    def test_selector_offers_stable_and_beta_with_pin_operation(self):
        with patch("postmaster.webgui_versions_v984._release_snapshot",
                   return_value=snapshot()), \
             patch("postmaster.webgui_versions_v984.runtime_control.read_control",
                   return_value={"selector": "v9.8.9-beta.15"}):
            html = versions_settings(self.base)
        self.assertIn('data-v988-pin', html)
        self.assertIn('name="operation" value="pin-version"', html)
        self.assertIn('name="target_version" required', html)
        self.assertIn('<option value="v9.8.9-beta.14">', html)
        self.assertIn('<option value="v9.8.7">', html)
        self.assertIn('Preview &amp; pin version', html)
        self.assertIn('name="csrf" value="csrf-test"', html)
        self.assertIn('data-v988-update="stable"', html)
        self.assertIn('data-v988-update="beta"', html)

    def test_stable_only_never_lists_beta_options(self):
        with patch("postmaster.webgui_versions_v984._release_snapshot",
                   return_value=snapshot("stable")), \
             patch("postmaster.webgui_versions_v984.runtime_control.read_control",
                   return_value={"selector": "latest"}):
            html = versions_settings(self.base)
        self.assertNotIn('<option value="v9.8.9-beta.14">', html)
        self.assertNotIn('label="Beta releases"', html)
        self.assertIn('<option value="v9.8.8">', html)

    def test_empty_catalog_disables_selector(self):
        data=snapshot()
        data["release_tags"]=[]
        data["check_status"]="error"
        with patch("postmaster.webgui_versions_v984._release_snapshot",
                   return_value=data), \
             patch("postmaster.webgui_versions_v984.runtime_control.read_control",
                   return_value={}):
            html=versions_settings(self.base)
        self.assertIn('name="target_version" required disabled', html)

    def test_live_poll_preserves_existing_choice_and_checks_eligibility(self):
        self.assertIn('valid.includes(previous)?previous', VERSIONS_SCRIPT)
        self.assertIn('data.release_tags||[]', VERSIONS_SCRIPT)
        self.assertIn("pinning=form.hasAttribute('data-v988-pin')", VERSIONS_SCRIPT)

    def test_snapshot_reuses_verified_catalog(self):
        with patch("postmaster.webgui_versions_v984.runtime_control.available_release_tags",
                   return_value=(snapshot()["release_tags"], "ok")), \
             patch("postmaster.webgui_versions_v984.runtime_control.preferred_release_channel",
                   return_value=channels.STABLE_AND_BETA), \
             patch("postmaster.webgui_versions_v984.runtime_control.read_control",
                   return_value={"selector": "latest"}), \
             patch("postmaster.webgui_versions_v984.time.monotonic", return_value=999999999.0):
            self.base.build_status.return_value={"version": "9.8.9-beta.15"}
            with patch("postmaster.webgui_versions_v984._CATALOG_AT",0):
                data=_release_snapshot(self.base)
        self.assertIn("v9.8.9-beta.14",data["release_tags"])


if __name__ == "__main__":
    unittest.main()
