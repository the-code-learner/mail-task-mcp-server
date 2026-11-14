"""Stable/Beta release channel contracts and persistent update opt-in."""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from postmaster import runtime_control, update_status
from postmaster.release_channels_v986 import (
    STABLE,STABLE_AND_BETA,canonical_tag,newer_than,parse_release_tag,
    release_is_allowed,release_tags,
)
from postmaster.runtime_v967 import _release_target
from postmaster.webgui_versions_v984 import (
    install_versions_webgui_v984,versions_settings,
)


RELEASES=[
    {"tag_name":"v9.8.5","prerelease":False,"draft":False},
    {"tag_name":"v9.8.6-beta.1","prerelease":True,"draft":False},
    {"tag_name":"v9.8.6-beta.2","prerelease":True,"draft":False},
    {"tag_name":"v9.8.6","prerelease":False,"draft":False},
    {"tag_name":"v9.8.7-beta.1","prerelease":True,"draft":False},
    {"tag_name":"v9.8.8","prerelease":False,"draft":True},
    {"tag_name":"v9.8.9-beta.1","prerelease":False,"draft":False}, # invalid flag
    {"tag_name":"v9.9.0","prerelease":True,"draft":False},      # invalid flag
    {"tag_name":"v9.8.7-rc.1","prerelease":True,"draft":False},
]


class ReleasePolicyTests(unittest.TestCase):
    def test_stable_excludes_beta_and_drafts(self):
        self.assertEqual(release_tags(RELEASES),["v9.8.6","v9.8.5"])

    def test_opt_in_beta_uses_semver_precedence(self):
        self.assertEqual(release_tags(RELEASES,channel="stable_beta"),[
            "v9.8.7-beta.1","v9.8.6","v9.8.6-beta.2",
            "v9.8.6-beta.1","v9.8.5",
        ])
        self.assertTrue(newer_than("v9.8.7-beta.1","v9.8.6"))
        self.assertTrue(newer_than("v9.8.6","v9.8.6-beta.2"))
        self.assertFalse(newer_than("v9.8.6-beta.1","v9.8.6"))
        self.assertFalse(newer_than("v9.8.6-beta.1","v9.8.6-beta.2"))

    def test_unpublished_or_inconsistent_tags_cannot_enter_channel(self):
        self.assertFalse(release_is_allowed("v9.8.6-beta.1","stable"))
        self.assertTrue(release_is_allowed("v9.8.6-beta.1","stable_beta"))
        with self.assertRaises(ValueError):
            canonical_tag("9.8.6-beta.02")
        for invalid in ("", "main", "v9.8.6-rc.1","v9.8.6-beta.0","v9.8.6-beta.latest"):
            self.assertIsNone(parse_release_tag(invalid))

    def test_default_stable_and_persistence_across_runtime_updates(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/"control.json"
            self.assertEqual(runtime_control.preferred_release_channel(dest),STABLE)
            result=runtime_control.save_release_channel("stable_beta",path=dest)
            self.assertEqual(result["selector"],"latest")
            self.assertEqual(result["release_channel"],STABLE_AND_BETA)
            self.assertEqual(runtime_control.preferred_release_channel(dest),STABLE_AND_BETA)
            pinned=runtime_control.write_control(
                selector="v9.8.6-beta.1",path=dest,
            )
            self.assertEqual(pinned["release_channel"],STABLE_AND_BETA)
            with self.assertRaisesRegex(ValueError,"stable version"):
                runtime_control.save_release_channel("stable",path=dest)
            runtime_control.write_control(selector="latest",path=dest)
            result=runtime_control.save_release_channel("stable",path=dest)
            self.assertEqual(result["release_channel"],STABLE)

    def test_missing_and_corrupt_pref_fail_closed_to_stable(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/"control.json"
            target.write_text('{"release_channel":"anything","selector":"latest"}')
            self.assertEqual(runtime_control.preferred_release_channel(target),STABLE)
            with self.assertRaises(ValueError):
                runtime_control.save_release_channel("unknown",path=target)

    def test_eligible_runtime_previews_and_exact_execute_are_channel_aware(self):
        with patch.object(runtime_control,"preferred_release_channel",return_value="stable_beta"),\
             patch.object(runtime_control,"available_release_tags",
                          return_value=(["v9.8.7-beta.1","v9.8.6","v9.8.5"],"ok")) as discover:
            target,selector,versions=_release_target("update-latest",None,execute=False)
            self.assertEqual((target,selector),("v9.8.7-beta.1","latest"))
            self.assertEqual(
                _release_target("update-latest","v9.8.7-beta.1",execute=True)[:2],
                ("v9.8.7-beta.1","latest"),
            )
            self.assertEqual(discover.call_args.kwargs["channel"],"stable_beta")
        with patch.object(runtime_control,"preferred_release_channel",return_value="stable"),\
             patch.object(runtime_control,"available_release_tags",
                          return_value=(["v9.8.6","v9.8.5"],"ok")):
            with self.assertRaisesRegex(ValueError,"not a published GitHub release"):
                _release_target("switch-version","v9.8.7-beta.1",execute=False)

    def test_latest_update_badge_respects_preference(self):
        update_status.reset_update_cache()
        with patch.object(runtime_control,"preferred_release_channel",return_value="stable_beta"),\
             patch.object(update_status,"_fetch_latest_beta_version",return_value="9.8.7-beta.1"),\
             patch.object(update_status.time,"monotonic",return_value=150.0):
            result=update_status.latest_version_status("9.8.6")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["release_channel"],"stable_beta")
        self.assertEqual(result["latest_version"],"9.8.7-beta.1")
        update_status.reset_update_cache()


class _Base:
    def _csrf_value(self):return "csrf-v986"
    def build_status(self):return {"version":"9.8.5","requested_version":"latest"}
    async def _verified_form(self,req):
        form=await req.form()
        if form.get("csrf")!="csrf-v986":
            return form,PlainTextResponse("Invalid CSRF",status_code=403)
        return form,None


def _client():
    app=Starlette()
    base=_Base()
    core=SimpleNamespace()
    views=SimpleNamespace(
        render_overview=lambda *_:"<section>Overview</section>",
        v951=SimpleNamespace(render_system=lambda *_:"<section>Settings</section>"),
    )
    v962=SimpleNamespace(NAV=(("system","Settings"),))
    v970=SimpleNamespace(NAV_GROUPS=(("Control",(("system","Settings","SY"),)),),
                          VIEW_LABELS={})
    install_versions_webgui_v984(app,base,core,v962,views,v970)
    return TestClient(app),base


class SettingsPreferenceTests(unittest.TestCase):
    def test_settings_channel_control_selected_and_releases_labeled(self):
        base=_Base()
        with patch.object(runtime_control,"preferred_release_channel",return_value="stable_beta"),\
             patch.object(runtime_control,"available_release_tags",
                          return_value=(["v9.8.6-beta.2","v9.8.5"],"ok")),\
             patch.object(runtime_control,"read_control",return_value={"selector":"latest"}):
            html=versions_settings(base)
        self.assertIn("Update channel",html)
        self.assertIn('name="release_channel"',html)
        self.assertIn('value="stable_beta" selected',html)
        self.assertIn("Stable only",html)
        self.assertIn("Stable + Beta",html)
        self.assertIn("v9.8.6-beta.2 · BETA",html)
        self.assertIn('action="/dashboard/versions/channel"',html)
        self.assertIn('name="csrf"',html)

    def test_preference_route_is_csrf_guarded_persistent_and_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/"control.json")
            with patch.dict(os.environ,{"POSTMASTER_RUNTIME_CONTROL_PATH":path}):
                client,base=_client()
                with client:
                    bad=client.post("/dashboard/versions/channel",data={
                        "csrf":"bad","release_channel":"stable_beta",
                    },follow_redirects=False)
                    self.assertEqual(bad.status_code,403)
                    self.assertFalse(Path(path).exists())
                    good=client.post("/dashboard/versions/channel",data={
                        "csrf":base._csrf_value(),"release_channel":"stable_beta",
                    },follow_redirects=False)
                self.assertEqual(good.status_code,303)
                self.assertIn("settings_tab=versions",good.headers["location"])
                self.assertEqual(runtime_control.preferred_release_channel(),STABLE_AND_BETA)
                self.assertEqual(runtime_control.read_control()["selector"],"latest")
                self.assertFalse(runtime_control.read_control().get("restart_ref_once"))

    def test_default_preference_stable_without_explicit_save(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"nothing.json"
            with patch.object(runtime_control,"control_path",return_value=path),\
                 patch.object(runtime_control,"stable_release_tags",
                              return_value=(["v9.8.5"],"ok")):
                html=versions_settings(_Base())
        self.assertIn('value="stable" selected',html)
        self.assertNotIn('value="stable_beta" selected',html)


class BootstrapContracts(unittest.TestCase):
    def test_single_yaml_bootstrap_reads_channel_and_never_selects_beta_by_default(self):
        yaml=(Path(__file__).resolve().parents[1]/"postmaster-mcp.yml").read_text()
        self.assertIn('CONTROL_CHANNEL="stable"',yaml)
        self.assertIn('payload.get("release_channel") or "stable"',yaml)
        self.assertIn('channel == "stable_beta" and beta.fullmatch(ref)',yaml)
        self.assertIn('if is_beta and channel != "stable_beta":',yaml)
        self.assertIn('order += (0 if is_beta else 1',yaml)
        self.assertIn('release.get("prerelease") is True',yaml)
        self.assertIn('POSTMASTER_VERSION: latest',yaml)
        self.assertIn('mcp_data:/data',yaml)

    def test_embedded_bootstrap_python_resolves_real_stable_and_beta_order(self):
        yaml=(Path(__file__).resolve().parents[1]/"postmaster-mcp.yml").read_text()
        found=re.search(
            r'if REF="\$\$\(python - <<\x27PY\x27\n(?P<body>.*?)\n        PY',
            yaml,re.S,
        )
        self.assertIsNotNone(found,"embedded release resolver missing")
        source=textwrap.dedent(found.group("body"))
        compile(source,"<portainer release resolver>","exec")
        def resolve(channel):
            result=io.StringIO()
            payload=json.dumps(RELEASES).encode()
            with patch.dict(os.environ,{"REPO":"the-code-learner/mail-task-mcp-server",
                                        "CONTROL_CHANNEL":channel}),\
                 patch("urllib.request.urlopen",return_value=io.BytesIO(payload)),\
                 redirect_stdout(result):
                exec(compile(source,"<portainer release resolver>","exec"),{})
            return result.getvalue().strip()
        self.assertEqual(resolve("stable"),"v9.8.6")
        self.assertEqual(resolve("stable_beta"),"v9.8.7-beta.1")

    def test_embedded_bootstrap_control_reader_validates_opt_in(self):
        yaml=(Path(__file__).resolve().parents[1]/"postmaster-mcp.yml").read_text()
        found=re.search(
            r'if CONTROL_VALUES="\$\$\(python - <<\x27PY\x27\n(?P<body>.*?)\n        PY',
            yaml,re.S,
        )
        self.assertIsNotNone(found,"embedded runtime preference reader missing")
        source=textwrap.dedent(found.group("body"))
        compile(source,"<portainer release preference>","exec")
        def read(payload):
            with tempfile.TemporaryDirectory() as td:
                path=Path(td)/"state.json"
                path.write_text(json.dumps(payload),encoding="utf-8")
                result=io.StringIO()
                with patch.dict(os.environ,{"CONTROL_FILE":str(path)}),redirect_stdout(result):
                    exec(compile(source,"<portainer release preference>","exec"),{})
                return result.getvalue().splitlines()
        self.assertEqual(
            read({"selector":"latest"}),
            ["latest","","false","stable"],
        )
        self.assertEqual(
            read({"selector":"latest","release_channel":"stable_beta",
                  "restart_ref_once":"v9.8.6-beta.1","check_updates_once":True}),
            ["latest","v9.8.6-beta.1","true","stable_beta"],
        )
        with self.assertRaisesRegex(RuntimeError,"invalid one-shot restart ref"):
            read({"selector":"latest","release_channel":"stable",
                  "restart_ref_once":"v9.8.6-beta.1"})

    def test_workflows_do_not_publish_beta_as_stable(self):
        root=Path(__file__).resolve().parents[1]/".github"/"workflows"
        stable=(root/"stable-release.yml").read_text()
        beta=(root/"beta-release.yml").read_text()
        self.assertIn("branches:",stable)
        self.assertIn("- main",stable)
        self.assertIn("Invalid VERSION",stable)
        self.assertIn("workflow_dispatch:",beta)
        self.assertIn("gh release create",beta)
        self.assertIn("--prerelease",beta)
        self.assertIn('data["isPrerelease"] is True',beta)
        self.assertIn("VERSION mismatch",beta)


if __name__=="__main__":
    unittest.main()
