"""Regression tests for Tracking period transitions and GeoIP readiness."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request

from postmaster import webgui_tracking_v985, webgui_v962


def _request(query: str = "") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "query_string": query.encode("utf-8"), "headers": [],
    })


# Exact-commit release guard: real period links and optional ISP availability.
class TrackingPeriodGeoIPV98913Tests(unittest.TestCase):
    def test_period_chips_have_distinct_force_reload_urls(self):
        form = webgui_tracking_v985._period_form(
            _request("ui_view=tracking&period=7d&account_id=work"
                     "&owner_id=o&tracking_tab=email"), "7d"
        )
        import re
        links=re.findall(r'<a[^>]*data-v985-period="([^"]+)"[^>]*href="([^"]+)"',form)
        self.assertEqual(len(links),5)
        for label,raw in links:
            with self.subTest(label=label):
                url=urlsplit(raw.replace("&amp;","&"))
                args=parse_qs(url.query)
                self.assertEqual(args["period"],[label])
                self.assertEqual(args["account_id"],["work"])
                self.assertEqual(args["owner_id"],["o"])
                self.assertEqual(args["tracking_tab"],["email"])

    def test_tracking_renderer_distinguishes_optional_isp_from_required_city_asn(self):
        start=datetime(2026,11,7,tzinfo=UTC)
        end=start+timedelta(days=7)
        with patch.object(webgui_tracking_v985,"_period",
                          return_value=(start,end,"7d")), \
             patch.object(webgui_tracking_v985,"scoped_events",return_value=([],False)), \
             patch.object(webgui_tracking_v985,"_sent",return_value=([],False)), \
             patch.object(webgui_tracking_v985,"_received",return_value=([], "")), \
             patch.object(webgui_tracking_v985,"_heatmap",return_value=""), \
             patch.object(webgui_tracking_v985,"_activity_trend",return_value=""):
            variants = [
                (True, True, False, 'data-geoip-status="ready"',
                 "Optional ISP database not installed"),
                (True, True, True, 'data-geoip-status="ready"',
                 "Optional ISP database active"),
                (True, False, False, 'data-geoip-status="partial"',
                 "Missing required: ASN"),
                (False, False, False, 'data-geoip-status="missing"',
                 "free City and ASN databases not installed"),
            ]
            for city,asn,isp,marker,expected in variants:
                with self.subTest(city=city,asn=asn,isp=isp), \
                     patch.object(webgui_tracking_v985,"geoip_status",return_value={
                         "city_database_available":city,
                         "asn_database_available":asn,
                         "isp_database_available":isp,
                     }):
                    html=webgui_tracking_v985.render_tracking_v985(
                        object(),object(),_request("ui_view=tracking&period=7d")
                    )
                    self.assertIn(marker,html)
                    self.assertIn(expected,html)
                    if city and asn:
                        self.assertNotIn("local databases unavailable",html)

    def test_period_router_shows_pending_and_does_not_discard_old_report(self):
        source=webgui_v962.SCRIPT
        self.assertIn("const trackingPending = view==='tracking'",source)
        self.assertIn("Loading '+(descriptions[chosen]||chosen)+' statistics",source)
        self.assertIn("target.classList.add('v98913-tracking-loading')",source)
        self.assertIn("controller.abort(); controllers.delete",source)
        self.assertIn("generations.get(view) !== generation",source)
        self.assertIn("if(view==='tracking' && trackingPending)",source)
        self.assertIn("Could not load the selected period",source)
        self.assertIn("retry.addEventListener('click'",source)
        self.assertIn("load('tracking',a.href,true,true)",source)


if __name__ == "__main__":
    unittest.main()
