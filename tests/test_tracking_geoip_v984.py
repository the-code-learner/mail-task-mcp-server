from __future__ import annotations

import os
import sqlite3
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from postmaster.tracking_geoip_v984 import (
    enrich_ip, geoip_status, persist_generic_network, generic_details, _reader,
)


class Analytics:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn


class GeoIPV984Tests(unittest.TestCase):
    def setUp(self):
        _reader.cache_clear()

    def test_no_local_mmdb_does_not_fabricate_a_city_or_provider(self):
        with TemporaryDirectory() as td, patch.dict(os.environ, {
            "POSTMASTER_GEOIP_CITY_DB": td + "/missing-city.mmdb",
            "POSTMASTER_GEOIP_ASN_DB": td + "/missing-asn.mmdb",
            "POSTMASTER_GEOIP_ISP_DB": td + "/missing-isp.mmdb",
        }):
            result = enrich_ip("8.8.8.8", "US")
            self.assertEqual(result["geo_country_code"], "US")
            self.assertEqual(result["geo_city"], "")
            self.assertEqual(result["geo_organization"], "")
            self.assertEqual(result["geo_confidence"], "low")
            self.assertFalse(geoip_status()["external_lookup"])

    def test_raw_ip_and_organizational_metadata_are_persisted_indefinitely(self):
        with TemporaryDirectory() as td, patch.dict(os.environ, {
            "POSTMASTER_GEOIP_CITY_DB": td + "/missing-city.mmdb",
            "POSTMASTER_GEOIP_ASN_DB": td + "/missing-asn.mmdb",
            "POSTMASTER_GEOIP_ISP_DB": td + "/missing-isp.mmdb",
        }):
            db = Analytics(Path(td) / "analytics.db")
            persist_generic_network(
                db, event_id=9,
                link={"id": "glnk-test", "owner_id": "davide", "project_id": "postmaster-mcp"},
                client_ip="2001:4860:4860::8888", country_code="US",
                observed_at="2020-01-01T00:00:00+00:00",
            )
            row = generic_details(db, [9])[9]
            self.assertEqual(row["source_ip"], "2001:4860:4860::8888")
            self.assertEqual(row["owner_id"], "davide")
            self.assertEqual(row["project_id"], "postmaster-mcp")
            self.assertEqual(row["observed_at"], "2020-01-01T00:00:00+00:00")
            self.assertEqual(row["geo_country_code"], "US")

    def test_invalid_ip_is_never_persisted_as_an_address(self):
        with TemporaryDirectory() as td:
            db = Analytics(Path(td) / "analytics.db")
            persist_generic_network(
                db, event_id=3, link={"id": "glnk-test", "owner_id": "x"},
                client_ip="<bad>", country_code="ZZ",
                observed_at="2025-01-01",
            )
            self.assertEqual(generic_details(db, [3])[3]["source_ip"], "")


if __name__ == "__main__":
    unittest.main()
