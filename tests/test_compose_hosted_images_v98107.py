"""Regression tests for Postmaster-hosted email images as open beacons."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.testclient import TestClient

from postmaster.email_analytics import EmailAnalyticsStore, TRANSPARENT_GIF
from postmaster.file_store import FileStore
from postmaster.generic_tracking_v983 import GenericTrackedLinkStoreV983
from postmaster.inline_images_v9810 import (
    image_media_type, image_url, personalize_images, install_compose_images,
)


class _Base:
    def __init__(self, file_store, analytics):
        self.fs = file_store
        self.analytics = analytics

    def file_store(self):
        return self.fs

    def analytics_store(self):
        return self.analytics

    async def _verified_form(self, request):
        form = await request.form()
        if str(form.get("csrf") or "") != "valid-csrf":
            return None, object()
        return form, None


class HostedComposeImagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"PUBLIC_EMAIL_BASE_URL": "https://postmaster.example.test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        path = Path(self.tmp.name)
        self.analytics = EmailAnalyticsStore(
            db_path=str(path / "analytics.db"), key_path=str(path / "analytics.key"),
        )
        self.files = FileStore(
            db_path=str(path / "files.db"), root=str(path / "stored"), max_bytes=256_000,
        )
        self.generic = GenericTrackedLinkStoreV983(
            self.analytics, file_store_provider=lambda: self.files,
        )
        self.app = Starlette()
        install_compose_images(self.app, _Base(self.files, self.analytics), self.generic)
        self.http = TestClient(self.app)
        self.addCleanup(self.http.close)

    def upload(self, *, file_bytes=TRANSPARENT_GIF, name="logo.gif",
               media_type="image/gif", owner="email-owner"):
        return self.http.post(
            "/dashboard/compose/image/upload",
            data={"owner_id": owner, "csrf": "valid-csrf"},
            files={"file": (name, file_bytes, media_type)},
        )

    def delivery(self, *, tracked=True):
        campaign = self.analytics.create_campaign(
            account_id="test-account", sender="sender@example.test",
            subject="Hello", track_opens=tracked, amp_used=False,
        )
        return self.analytics.create_delivery(
            campaign_id=campaign["id"], account_id="test-account",
            recipient="recipient@example.test", recipient_role="to",
        )

    def test_upload_stores_raster_and_returns_no_external_or_pixel_url(self):
        result = self.upload()
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["image_url"].startswith("https://postmaster.example.test/t/i/glnk_"))
        self.assertEqual(body["media_type"], "image/gif")
        saved = self.files.get_info(body["file_id"])
        self.assertEqual(saved["owner_id"], "email-owner")
        self.assertEqual(saved["sha256"], self.files.get_info(body["file_id"])["sha256"])
        self.assertEqual(saved["media_type"], "image/gif")
        self.assertIn("no-store", result.headers["cache-control"])

    def test_html_image_replaces_pixel_when_tracked(self):
        img = self.upload().json()
        delivery = self.delivery()
        source = f'<p>Hi</p><img src="{img["image_url"]}" alt="Logo">'
        out, amp = self.analytics.render_for_recipient(
            body_html=source, body_amp=None, delivery=delivery, track_opens=True,
        )
        self.assertIsNone(amp)
        self.assertIn("/" + delivery["tracking_token"], out)
        self.assertNotIn("/track/open/", out)
        self.assertEqual(out.count('<img '), 1)
        public_path = urlsplit(img["image_url"]).path
        tracked_path = urlsplit(
            img["image_url"] + "/" + delivery["tracking_token"]
        ).path
        normal = self.http.get(public_path)
        self.assertEqual(normal.status_code, 200)
        self.assertEqual(normal.content, TRANSPARENT_GIF)
        self.assertEqual(self.analytics.get_delivery(delivery["id"])["open_count"], 0)
        head = self.http.head(tracked_path)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(self.analytics.get_delivery(delivery["id"])["open_count"], 0)
        opened = self.http.get(tracked_path, headers={"user-agent": "Mozilla/5.0"})
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.headers["content-type"], "image/gif")
        self.assertIn("no-store", opened.headers["cache-control"])
        self.assertEqual(opened.content, TRANSPARENT_GIF)
        self.assertEqual(self.analytics.get_delivery(delivery["id"])["open_count"], 1)
        events = self.analytics.list_open_events(account_id="test-account", limit=10)
        self.assertEqual(events[0]["event_type"], "hosted_image")

    def test_first_of_multiple_images_only_is_instrumented(self):
        img = self.upload().json()
        token = self.delivery()["tracking_token"]
        html = f'<img src="{img["image_url"]}"><img src="{img["image_url"]}">'
        rewritten, used = personalize_images(html, token, tracking=True)
        self.assertTrue(used)
        self.assertEqual(rewritten.count("/" + token), 1)

    def test_tracking_off_never_records_image_and_keeps_original_src(self):
        img = self.upload().json()
        delivery = self.delivery(tracked=False)
        html = '<img src="' + img["image_url"] + '">'
        rendered, _ = self.analytics.render_for_recipient(
            body_html=html, body_amp=None, delivery=delivery, track_opens=False,
        )
        self.assertEqual(rendered, html)
        self.assertNotIn("/track/open/", rendered)
        path = urlsplit(img["image_url"]).path
        self.assertEqual(self.http.get(path).status_code, 200)
        self.assertEqual(self.analytics.get_delivery(delivery["id"])["open_count"], 0)

    def test_pixel_fallback_when_only_remote_image_or_plain_html(self):
        delivery = self.delivery()
        original = '<img src="https://others.example/picture.jpg">'
        out, _ = self.analytics.render_for_recipient(
            body_html=original, body_amp=None, delivery=delivery, track_opens=True,
        )
        self.assertIn("/track/open/", out)
        self.assertIn(original, out)

    def test_amp_image_is_individualized_with_no_redundant_amp_pixel(self):
        image = self.upload().json()
        delivery = self.delivery()
        amp = '<html amp4email><body><amp-img src="' + image["image_url"] + '" width="400" height="100"></amp-img></body></html>'
        html, amp_result = self.analytics.render_for_recipient(
            body_html="<p>Text fallback</p>", body_amp=amp,
            delivery=delivery, track_opens=True,
        )
        self.assertIn("/track/open/", html)  # HTML has no hosted image.
        self.assertIn("/" + delivery["tracking_token"], amp_result)
        self.assertNotIn("/track/open/", amp_result)

    def test_rejects_invalid_file_types_and_csrf(self):
        for raw, typ in [(b"<svg><script>alert(1)</script></svg>", "image/svg+xml"),
                         (b"<html>Not an image</html>", "image/png")]:
            result = self.upload(file_bytes=raw, name="not-image.svg", media_type=typ)
            self.assertEqual(result.status_code, 400)
            self.assertFalse(result.json()["ok"])
        bad = self.http.post(
            "/dashboard/compose/image/upload", data={"owner_id": "a", "csrf": "wrong"},
            files={"file": ("logo.gif", TRANSPARENT_GIF, "image/gif")},
        )
        self.assertEqual(bad.status_code, 403)

    def test_file_image_token_cannot_be_used_after_file_delete(self):
        image = self.upload().json()
        path = urlsplit(image["image_url"]).path
        self.assertEqual(self.http.get(path).status_code, 200)
        self.files.delete_file(image["file_id"])
        self.assertEqual(self.http.get(path).status_code, 404)

    def test_wrong_or_invalid_image_token_not_found(self):
        self.assertEqual(self.http.get("/t/i/glnk_doesnotexist123456789").status_code, 404)
        self.assertEqual(self.http.get("/t/i/glnk_../../files").status_code, 404)
        self.assertIsNone(image_media_type(b"<html>unsafe</html>"))


if __name__ == "__main__":
    unittest.main()
