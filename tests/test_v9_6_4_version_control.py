from __future__ import annotations

import hashlib
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from postmaster import runtime_control
from postmaster.runtime_v964 import install_runtime_v964


EXPECTED_SINGLE_YAML_BLOB = "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9"
STABLE_RELEASES = ["v9.6.5", "v9.6.4", "v9.6.3", "v9.6.2"]


class FakeMcpRegistry:
    def __init__(self):
        self.tools = {f"placeholder_{index}": object() for index in range(86)}
        self.tools.update({
            "build_status": object(),
            "send_email": object(),
            "reply_email": object(),
            "follow_up_email": object(),
        })

    def remove_tool(self, name):
        self.tools.pop(name, None)

    def add_tool(self, fn, *, name):
        self.tools[name] = fn


class FakeRuntimeBase:
    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def mail_client(self, account_id=None):
        raise AssertionError("version-control tests must not execute mail sends")


class FakeCore:
    def __init__(self):
        self.mcp = FakeMcpRegistry()


class RuntimeVersionApprovalTests(unittest.TestCase):
    def setUp(self):
        runtime_control.clear_version_change_approvals()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {"POSTMASTER_RUNTIME_CONTROL_PATH": str(Path(self.temp.name) / "control.json")},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.base = FakeRuntimeBase()
        self.core = FakeCore()
        self.status = {
            "ok": True,
            "version": "9.6.2",
            "build": "v9.6.2",
            "requested_version": "latest",
            "latest_version": "9.6.3",
            "update_available": True,
            "update_check_status": "ok",
        }
        self.build_status = install_runtime_v964(self.base, self.core, lambda: dict(self.status))

    @property
    def control_path(self) -> Path:
        return Path(os.environ["POSTMASTER_RUNTIME_CONTROL_PATH"])

    def test_read_only_update_check_requires_no_approval_and_does_not_write_control(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")):
            result = self.build_status(operation="check-update")
        self.assertTrue(result["ok"])
        self.assertFalse(result["approval_required"])
        self.assertFalse(result["version_change_applied"])
        self.assertEqual(result["latest_version_ref"], "v9.6.5")
        self.assertFalse(self.control_path.exists())

    def test_update_latest_without_approval_is_preview_only(self):
        result = self.build_status(operation="update-latest")
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["version_change_applied"])
        self.assertEqual(result["current_version"], "9.6.2")
        self.assertEqual(result["current_build"], "v9.6.2")
        self.assertEqual(result["current_selector"], "latest")
        self.assertEqual(result["target_version_ref"], "latest")
        self.assertTrue(result["confirmation_token"])
        self.assertFalse(self.control_path.exists())

    def test_version_change_without_approval_is_preview_only(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")):
            result = self.build_status(operation="pin-version", target_version="v9.6.4")
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["version_change_applied"])
        self.assertEqual(result["target_version_ref"], "v9.6.4")
        self.assertFalse(self.control_path.exists())

    def test_exact_target_approval_allows_one_operation(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")), patch.object(
            runtime_control, "schedule_current_process_termination"
        ) as restart:
            preview = self.build_status(operation="pin-version", target_version="v9.6.4")
            applied = self.build_status(
                operation="pin-version",
                target_version="v9.6.4",
                confirm_version_change=preview["confirmation_token"],
            )
        self.assertTrue(applied["version_change_applied"])
        self.assertEqual(runtime_control.read_control(self.control_path), {"selector": "v9.6.4"})
        restart.assert_called_once_with()

    def test_approval_for_specific_version_cannot_be_reused_for_latest_or_other_target(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")):
            preview = self.build_status(operation="pin-version", target_version="v9.6.4")
            token = preview["confirmation_token"]
            wrong_operation = self.build_status(
                operation="update-latest",
                confirm_version_change=token,
            )
            reused = self.build_status(
                operation="pin-version",
                target_version="v9.6.4",
                confirm_version_change=token,
            )
        self.assertFalse(wrong_operation["version_change_applied"])
        self.assertFalse(reused["version_change_applied"])
        self.assertFalse(self.control_path.exists())

    def test_approval_is_nonpersistent_between_calls(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")), patch.object(
            runtime_control, "schedule_current_process_termination"
        ):
            preview = self.build_status(operation="pin-version", target_version="v9.6.4")
            token = preview["confirmation_token"]
            first = self.build_status(
                operation="pin-version",
                target_version="v9.6.4",
                confirm_version_change=token,
            )
            second = self.build_status(
                operation="pin-version",
                target_version="v9.6.4",
                confirm_version_change=token,
            )
        self.assertTrue(first["version_change_applied"])
        self.assertFalse(second["version_change_applied"])

    def test_target_modified_after_preview_requires_new_approval(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")):
            preview = self.build_status(operation="switch-version", target_version="v9.6.4")
            changed = self.build_status(
                operation="switch-version",
                target_version="v9.6.5",
                confirm_version_change=preview["confirmation_token"],
            )
        self.assertFalse(changed["version_change_applied"])
        self.assertTrue(changed["approval_required"])
        self.assertFalse(self.control_path.exists())

    def test_force_refresh_is_read_only_and_distinct_from_version_approval(self):
        with patch.object(
            runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")
        ) as releases:
            refreshed = self.build_status(operation="list-versions", force_refresh=True)
            rejected = self.build_status(operation="update-latest", force_refresh=True)
        releases.assert_called_once_with(force=True)
        self.assertTrue(refreshed["force_refresh"])
        self.assertFalse(refreshed["approval_required"])
        self.assertFalse(refreshed["version_change_applied"])
        self.assertFalse(rejected["version_change_applied"])
        self.assertIn("read-only", rejected["error"])
        self.assertFalse(self.control_path.exists())

    def test_rollback_and_update_latest_use_same_exact_approval_rule(self):
        with patch.object(runtime_control, "stable_release_tags", return_value=(STABLE_RELEASES, "ok")), patch.object(
            runtime_control, "schedule_current_process_termination"
        ) as restart:
            rollback_preview = self.build_status(operation="rollback-version", target_version="v9.6.3")
            self.assertFalse(self.control_path.exists())
            rollback = self.build_status(
                operation="rollback-version",
                target_version="v9.6.3",
                confirm_version_change=rollback_preview["confirmation_token"],
            )
            self.control_path.unlink()
            latest_preview = self.build_status(operation="update-latest")
            latest = self.build_status(
                operation="update-latest",
                confirm_version_change=latest_preview["confirmation_token"],
            )
        self.assertTrue(rollback["version_change_applied"])
        self.assertTrue(latest["version_change_applied"])
        self.assertEqual(runtime_control.read_control(self.control_path), {"selector": "latest", "check_updates_once": True})
        self.assertEqual(restart.call_count, 2)

    def test_mcp_command_names_remain_exactly_90_and_description_requires_chat_approval(self):
        self.assertEqual(len(self.core.mcp.tools), 90)
        self.assertIn("confirm_version_change", inspect.signature(self.build_status).parameters)
        doc = inspect.getdoc(self.build_status) or ""
        self.assertIn("explicit user", doc)
        self.assertIn("active chat", doc)
        self.assertIn("Never infer approval", doc)
        for fn in (self.core.send_email, self.core.reply_email, self.core.follow_up_email):
            self.assertIn("idempotency_key", inspect.signature(fn).parameters)
            self.assertIn("force_send", inspect.signature(fn).parameters)
            self.assertIn("confirm_suppressed_recipients", inspect.signature(fn).parameters)

    def test_single_yaml_blob_is_unchanged(self):
        path = Path(__file__).resolve().parents[1] / "postmaster-mcp.yml"
        payload = path.read_bytes()
        blob = hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()
        self.assertEqual(blob, EXPECTED_SINGLE_YAML_BLOB)


if __name__ == "__main__":
    unittest.main()
