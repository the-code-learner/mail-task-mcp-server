"""Beta.9 GeoIP Settings: CSRF, credentials, download integrity and UI."""
from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request

from postmaster.geoip_download_v9899 import (
    _SafeRedirect, _target, GeoIPDownloadError, install_geolite2,
)
from postmaster.webgui_geoip_v9899 import (
    geoip_settings_panel, install_geoip_settings,
)


class _Base:
    def _csrf_value(self):
        return "test-geoip-csrf"


def request(query: bytes = b""):
    return Request({"type":"http","method":"GET","path":"/",
                    "query_string":query,"headers":[]})


class GeoIPSettingsTests(unittest.TestCase):
    # Exact-commit release gate: untrusted inputs never fetch files until explicit consent.
    def test_geoip_tab_shows_download_consent_status_without_credential_storage(self):
        with patch("postmaster.webgui_geoip_v9899.geoip_status",
                   return_value={"city_database_available":False,
                                 "asn_database_available":True,
                                 "isp_database_available":False}):
            html = geoip_settings_panel(_Base(),request())
        self.assertIn("GeoIP databases",html)
        self.assertIn("GeoLite2 City and ASN",html)
        self.assertIn('name="csrf" value="test-geoip-csrf"',html)
        self.assertIn('type="password" name="license_key"',html)
        self.assertIn('name="license_confirm"',html)
        self.assertIn('action="/dashboard/geoip/install"',html)
        self.assertIn("Not installed",html)
        self.assertIn("Installed",html)
        self.assertNotIn("password123",html)

    def test_input_validation_blocks_invalid_credentials_without_network_calls(self):
        with patch("postmaster.geoip_download_v9899._fetch_edition") as fetch:
            for account,key in (("","valid-key-test"),("a","valid-key-test"),
                                ("123","short"),("123","abc:defghi")):
                with self.subTest(account=account,key=key):
                    with self.assertRaises(GeoIPDownloadError):
                        install_geolite2(account,key)
            fetch.assert_not_called()

    def test_download_requires_both_editions_and_reports_validated_status(self):
        with patch("postmaster.geoip_download_v9899._fetch_edition",
                   side_effect=lambda account,key,edition:{"edition":edition,"installed":True}) as download, \
             patch("postmaster.geoip_download_v9899.geoip_status",
                   return_value={"city_database_available":True,"asn_database_available":True}):
            result=install_geolite2("123456","valid-license-value")
        self.assertTrue(result["ok"])
        self.assertEqual([call.args[2] for call in download.call_args_list],
                         ["GeoLite2-City","GeoLite2-ASN"])

    def test_selected_geoip_path_comes_only_from_server_config(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{
            "POSTMASTER_GEOIP_CITY_DB":td+"/my-city.mmdb"
        }):
            self.assertEqual(str(_target("GeoLite2-City")),td+"/my-city.mmdb")

    # Exact-SHA release check: MaxMind auth stays official and R2 never gets secrets.
    def test_maxmind_same_host_redirect_preserves_auth_but_r2_strips_it(self):
        from urllib.request import Request
        req=Request(
            "https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz",
            headers={"Authorization":"Basic ONLY_IN_MAXMIND"}
        )
        handler=_SafeRedirect()
        same=handler.redirect_request(
            req,None,302,"Found",{},
            "https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz&redirect=1"
        )
        self.assertEqual(same.get_header("Authorization"),"Basic ONLY_IN_MAXMIND")
        other=handler.redirect_request(
            req,None,302,"Found",{},
            "https://mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com/archive?token=hidden"
        )
        self.assertIsNone(other.get_header("Authorization"))

    def test_maxmind_failure_diagnostics_never_expose_license_or_signed_url(self):
        import urllib.error
        from postmaster.geoip_download_v9899 import _http_download_error
        url="https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz"
        error=urllib.error.HTTPError(
            url,403,"Forbidden",{},io.BytesIO(
                b'{"code":"PERMISSION_REQUIRED","error":"secret=do-not-print"}'
            )
        )
        message=str(_http_download_error(error,"GeoLite2-City"))
        self.assertIn("HTTP 403",message)
        self.assertIn("MaxMind authentication",message)
        self.assertIn("PERMISSION_REQUIRED",message)
        self.assertNotIn("do-not-print",message)
        r2="https://mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com/a?X-Amz-Signature=PRIVATE_SIGNATURE"
        error2=urllib.error.HTTPError(r2,403,"Forbidden",{},io.BytesIO(b"PRIVATE_SIGNATURE"))
        message2=str(_http_download_error(error2,"GeoLite2-ASN"))
        self.assertIn("Cloudflare R2",message2)
        self.assertIn("HTTP 403",message2)
        self.assertNotIn("PRIVATE_SIGNATURE",message2)

    def test_http_errors_are_classified_by_stage_and_status(self):
        import urllib.error
        from postmaster.geoip_download_v9899 import _http_download_error
        u="https://download.maxmind.com/geoip/databases/GeoLite2-ASN/download?suffix=tar.gz"
        for status, expected in ((401,"active database-download"),
                                 (404,"edition"),(429,"download limit"),(503,"Temporary")):
            with self.subTest(status=status):
                e=urllib.error.HTTPError(u,status,"Failed",{},io.BytesIO(b""))
                self.assertIn(expected,str(_http_download_error(e,"GeoLite2-ASN")))

    def test_redirects_cannot_send_credentials_to_unapproved_hosts(self):
        from urllib.request import Request
        original=Request("https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz",
                         headers={"Authorization":"Basic secret"})
        handler=_SafeRedirect()
        with self.assertRaises(GeoIPDownloadError):
            handler.redirect_request(original,None,302,"Found",{},
                                     "https://malicious.example/steal")
        redirected=handler.redirect_request(
            original,None,302,"Found",{},
            "https://mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com/fixture")
        self.assertIsNotNone(redirected)
        self.assertNotIn("Authorization",dict(redirected.header_items()))

    def test_settings_tab_is_wired_to_active_runtime(self):
        from postmaster import webgui_settings_v985
        import inspect
        from pathlib import Path
        source=inspect.getsource(webgui_settings_v985)
        runtime_source=(Path(__file__).resolve().parents[1] /
                        "src/postmaster/runtime.py").read_text(encoding="utf-8")
        self.assertIn('tab("geoip","GeoIP databases")',source)
        self.assertIn('geoip_settings_panel(base,request)',source)
        self.assertIn("install_geoip_settings(app, _base, _webgui_v962)",runtime_source)
        self.assertIn('Route("/dashboard/geoip/install", install, methods=["POST"])',
                      inspect.getsource(install_geoip_settings))

if __name__=="__main__":
    unittest.main()
