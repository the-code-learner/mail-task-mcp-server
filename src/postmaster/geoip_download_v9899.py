"""Manually triggered, license-aware MaxMind GeoLite2 database downloader.

No MaxMind license key is persisted, logged or placed in a URL. Databases
are written only to configured fixed MMDB paths in the existing data volume.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from .tracking_geoip_v984 import _reader, geoip_status

_LOCK = threading.Lock()
_EDITIONS = {
    "GeoLite2-City": ("POSTMASTER_GEOIP_CITY_DB", "GeoLite2-City.mmdb"),
    "GeoLite2-ASN": ("POSTMASTER_GEOIP_ASN_DB", "GeoLite2-ASN.mmdb"),
}
_DOWNLOAD_HOST = "download.maxmind.com"
_ALLOWED_REDIRECT_HOSTS = {
    "download.maxmind.com", "updates.maxmind.com",
    "mm-prod-geoip-databases.a2649acb697e2c09b632799562c076f2.r2.cloudflarestorage.com",
}
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_DATABASE_BYTES = 256 * 1024 * 1024


class GeoIPDownloadError(Exception):
    pass


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_REDIRECT_HOSTS:
            raise GeoIPDownloadError("Database download redirected to an unapproved host")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # Authentication is valid only for download.maxmind.com; the
            # object-store presigned URL must never receive MaxMind credentials.
            redirected.remove_header("Authorization")
        return redirected


def _target(edition: str) -> Path:
    env, filename = _EDITIONS[edition]
    path = Path(os.getenv(env, "/data/" + filename))
    # Use only server configured absolute paths; the form cannot select paths.
    if not path.is_absolute():
        raise GeoIPDownloadError("GeoIP destination must be an absolute filesystem path")
    return path


def _fetch_edition(account_id: str, license_key: str, edition: str) -> dict:
    destination = _target(edition)
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = ("https://" + _DOWNLOAD_HOST + "/geoip/databases/" +
           edition + "/download?suffix=tar.gz")
    credentials = base64.b64encode(
        (account_id + ":" + license_key).encode("utf-8")
    ).decode("ascii")
    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(
        url, headers={
            "Authorization": "Basic " + credentials,
            "User-Agent": "Postmaster-GeoIP-Manager/1.0",
            "Accept": "application/gzip,application/octet-stream",
        },
    )
    archive_path = None
    staged_path = None
    try:
        with opener.open(req, timeout=60) as response, tempfile.NamedTemporaryFile(
            mode="wb", prefix=".geoip-archive-", suffix=".tar.gz",
            dir=str(destination.parent), delete=False,
        ) as archive:
            archive_path = Path(archive.name)
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise GeoIPDownloadError("GeoIP archive exceeds maximum allowed size")
                archive.write(chunk)
        with tarfile.open(archive_path, mode="r:gz") as bundle:
            members = [
                m for m in bundle.getmembers()
                if m.isfile() and Path(m.name).name == edition + ".mmdb"
            ]
            if len(members) != 1 or members[0].size > _MAX_DATABASE_BYTES:
                raise GeoIPDownloadError("GeoIP archive contains no valid single MMDB database")
            source = bundle.extractfile(members[0])
            if source is None:
                raise GeoIPDownloadError("GeoIP archive cannot be read")
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".geoip-", suffix=".mmdb",
                dir=str(destination.parent), delete=False,
            ) as staged:
                staged_path = Path(staged.name)
                with source:
                    shutil.copyfileobj(source, staged, 65536)
        # Validate MMDB before atomic replacement so partial downloads cannot
        # corrupt an existing functional City / ASN database.
        try:
            import geoip2.database
            with geoip2.database.Reader(str(staged_path)):
                pass
        except Exception as exc:
            raise GeoIPDownloadError("Downloaded file is not a valid GeoIP MMDB") from exc
        os.chmod(staged_path, 0o644)
        _reader.cache_clear()  # close cached handles before replacing
        os.replace(staged_path, destination)
        staged_path = None
        return {"edition": edition, "installed": True, "path": str(destination)}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise GeoIPDownloadError(
                "MaxMind rejected the account/license or this database edition is unavailable"
            ) from None
        if exc.code == 429:
            raise GeoIPDownloadError("MaxMind download quota reached; try another day") from None
        raise GeoIPDownloadError("MaxMind database download failed (HTTP error)") from None
    except (urllib.error.URLError, OSError, tarfile.TarError) as exc:
        raise GeoIPDownloadError("GeoIP download or archive validation failed") from None
    finally:
        for path in (archive_path, staged_path):
            if path is not None:
                path.unlink(missing_ok=True)


def install_geolite2(account_id: str, license_key: str) -> dict:
    """One explicit user-requested install; credentials are never stored."""
    account_id = str(account_id or "").strip()
    license_key = str(license_key or "").strip()
    if not account_id.isascii() or not account_id.isdigit() or len(account_id) > 18:
        raise GeoIPDownloadError("Enter the numeric MaxMind Account ID")
    if (not 8 <= len(license_key) <= 256 or not license_key.isascii()
            or not license_key.isprintable() or ":" in license_key):
        raise GeoIPDownloadError("Enter a valid MaxMind license key")
    if not _LOCK.acquire(blocking=False):
        raise GeoIPDownloadError("A GeoIP download is already running")
    try:
        installed = [_fetch_edition(account_id, license_key, edition)
                     for edition in _EDITIONS]
        status = geoip_status()
        return {"ok": status["city_database_available"] and
                status["asn_database_available"], "installed": installed, "status": status}
    finally:
        _LOCK.release()
