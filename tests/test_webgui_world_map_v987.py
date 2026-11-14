from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from starlette.requests import Request

from postmaster.webgui_world_map_v987 import (
    LAND_PATH, WORLD_MAP_SCRIPT, _category, aggregate_map_points, render_world_map,
)
from postmaster.webgui_tracking_v985 import render_tracking_v985


def req():
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/",
        "query_string": b"period=28d&ui_view=tracking", "headers": [],
    })


class WorldMapTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            {"event_kind": "email_open", "geo_latitude": 45.46,
             "geo_longitude": 9.18, "geo_city": "Milan", "geo_country_code": "IT",
             "user_agent": "GoogleImageProxy", "source_ip": "192.0.2.1",
             "recipient": "private@example.org"},
            {"event_kind": "email_open", "geo_latitude": 45.46,
             "geo_longitude": 9.18, "geo_city": "Milan", "geo_country_code": "IT",
             "user_agent": "GoogleImageProxy"},
            {"event_kind": "tracked_url_click", "geo_latitude": 37.7,
             "geo_longitude": -122.4, "user_agent": "Security Scanner"},
            {"event_kind": "email_open", "geo_latitude": None,
             "geo_longitude": None, "geo_country_code": "FR"},
        ]

    def test_world_geometry_is_bundled(self):
        self.assertGreater(len(LAND_PATH), 30000)
        self.assertIn("M", LAND_PATH)
        self.assertIn("Z", LAND_PATH)

    def test_aggregates_locations_and_drops_sensitive_identifiers(self):
        points, missing = aggregate_map_points(self.events)
        self.assertEqual(missing, 1)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["count"], 2)
        self.assertEqual(points[0]["category"], "proxy")
        html = render_world_map(self.events, truncated=True)
        self.assertIn("Geographical activity", html)
        self.assertIn("sample limit reached", html)
        self.assertIn("without coordinates", html)
        self.assertIn('data-category="scanner"', html)
        self.assertNotIn("192.0.2.1", html)
        self.assertNotIn("private@example.org", html)
        self.assertNotIn("GoogleImageProxy", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_classification_is_conservative(self):
        self.assertEqual(_category({"event_kind": "email_open"}), "other")
        self.assertEqual(_category({"event_kind": "email_open",
                                    "client_source": "gmail_image_proxy"}), "proxy")
        self.assertEqual(_category({"event_kind": "email_click",
                                    "estimated_classification": "likely_human"}), "human")
        self.assertEqual(_category({"event_kind": "email_open",
                                    "user_agent": "HeadlessScanner"}), "scanner")

    def test_invalid_coordinates_are_not_placed_on_map(self):
        points, missing = aggregate_map_points([
            {"geo_latitude": "nan", "geo_longitude": 0},
            {"geo_latitude": 91, "geo_longitude": 0},
            {"geo_latitude": "1", "geo_longitude": "2"},
        ])
        self.assertEqual(missing, 2)
        self.assertEqual(len(points), 1)

    def test_zoom_uses_bounded_base_radius_with_gentle_scale(self):
        html = render_world_map(self.events)
        self.assertIn('data-base-radius=', html)
        self.assertIn('Math.pow(width/1000,.85)', WORLD_MAP_SCRIPT)
        self.assertIn("Math.max(1.2,base*scale)", WORLD_MAP_SCRIPT)
        self.assertIn("node.setAttribute('r'", WORLD_MAP_SCRIPT)
        self.assertIn("data-map-zoom=\"reset\"", html)

    def test_overlapping_points_have_accessible_safe_chooser(self):
        self.assertIn("Math.hypot", WORLD_MAP_SCRIPT)
        self.assertIn("pm-map-overlap", WORLD_MAP_SCRIPT)
        self.assertIn("document.createElement('button')", WORLD_MAP_SCRIPT)
        self.assertIn("button.textContent", WORLD_MAP_SCRIPT)
        self.assertIn("popup.replaceChildren", WORLD_MAP_SCRIPT)
        self.assertNotIn("popup.innerHTML", WORLD_MAP_SCRIPT)

    def test_category_colors_and_transparency_are_preserved(self):
        from postmaster.webgui_world_map_v987 import WORLD_MAP_STYLE
        for category in ("human", "proxy", "scanner", "other"):
            self.assertIn(f'data-category="{category}"', WORLD_MAP_STYLE)
        self.assertIn("opacity:.72", WORLD_MAP_STYLE)
        self.assertIn("stroke-width:.85", WORLD_MAP_STYLE)

    def test_map_controls_are_delegated_for_lazy_fragments(self):
        self.assertIn("document.addEventListener('click'", WORLD_MAP_SCRIPT)
        self.assertIn("aria-pressed", render_world_map(self.events))
        self.assertIn("data-map-zoom", render_world_map(self.events))

    def test_tracking_overview_renders_map_with_period_scoped_events(self):
        start = datetime(2026, 11, 1, tzinfo=UTC)
        end = datetime(2026, 11, 14, tzinfo=UTC)
        with patch("postmaster.webgui_tracking_v985._period",
                   return_value=(start, end, "28d")), \
             patch("postmaster.webgui_tracking_v985.scoped_events",
                   return_value=(self.events, False)), \
             patch("postmaster.webgui_tracking_v985._sent",
                   return_value=([], False)), \
             patch("postmaster.webgui_tracking_v985._received",
                   return_value=([], "No dates")), \
             patch("postmaster.webgui_tracking_v985._heatmap",
                   return_value="HEATMAP"), \
             patch("postmaster.webgui_tracking_v985._activity_trend",
                   return_value="TREND"):
            html = render_tracking_v985(object(), object(), req())
        self.assertIn("Geographical activity", html)
        self.assertIn("Approximate source networks", html)
        self.assertIn("HEATMAP", html)
        self.assertIn("28d", html)


if __name__ == "__main__":
    unittest.main()
