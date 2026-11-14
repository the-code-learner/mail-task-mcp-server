"""Local, request-time GeoIP/ASN enrichment for v9.8.4 tracking.

No third-party lookup API is called. Optional administrator-provisioned MaxMind
City/ASN/ISP MMDB files live inside the existing /data volume. Missing databases
produce explicit unknown fields rather than fictitious locations/providers.
"""
from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


GEO_COLUMNS = {
    "geo_region": "TEXT NOT NULL DEFAULT ''",
    "geo_city": "TEXT NOT NULL DEFAULT ''",
    "geo_postal_code": "TEXT NOT NULL DEFAULT ''",
    "geo_latitude": "REAL",
    "geo_longitude": "REAL",
    "geo_accuracy_km": "INTEGER",
    "geo_asn": "INTEGER",
    "geo_isp": "TEXT NOT NULL DEFAULT ''",
    "geo_organization": "TEXT NOT NULL DEFAULT ''",
}


def _ip(value: str) -> str:
    try:
        return ipaddress.ip_address(str(value or "").strip()).compressed
    except ValueError:
        return ""


@lru_cache(maxsize=12)
def _reader(path: str):
    if not path or not Path(path).is_file():
        return None
    try:
        import geoip2.database  # optional dependency, always local
        return geoip2.database.Reader(path)
    except (ImportError, OSError, ValueError):
        return None


def _database(env_name: str, filename: str):
    path = os.getenv(env_name, f"/data/{filename}").strip()
    return _reader(path)


def geoip_status() -> dict[str, Any]:
    return {
        "network_lookup": "local_mmdb_only",
        "city_database_available": _database("POSTMASTER_GEOIP_CITY_DB", "GeoLite2-City.mmdb") is not None,
        "asn_database_available": _database("POSTMASTER_GEOIP_ASN_DB", "GeoLite2-ASN.mmdb") is not None,
        "isp_database_available": _database("POSTMASTER_GEOIP_ISP_DB", "GeoIP2-ISP.mmdb") is not None,
        "external_lookup": False,
        "raw_ip_retention": "indefinite_until_explicit_deletion",
    }


def enrich_ip(client_ip: str, country_code: str = "") -> dict[str, Any]:
    ip = _ip(client_ip)
    fallback_country = str(country_code or "").strip().upper()
    if not (len(fallback_country) == 2 and fallback_country.isalpha()):
        fallback_country = ""
    out: dict[str, Any] = {
        "geo_country_code": fallback_country,
        "geo_region": "",
        "geo_city": "",
        "geo_postal_code": "",
        "geo_latitude": None,
        "geo_longitude": None,
        "geo_accuracy_km": None,
        "geo_asn": None,
        "geo_isp": "",
        "geo_organization": "",
        "geo_label": f"Country {fallback_country}" if fallback_country else "Unavailable",
        "geo_source": "edge country" if fallback_country else "unavailable",
        "geo_confidence": "low" if fallback_country else "unknown",
    }
    if not ip or not ipaddress.ip_address(ip).is_global:
        return out
    source: list[str] = []
    city_db = _database("POSTMASTER_GEOIP_CITY_DB", "GeoLite2-City.mmdb")
    if city_db:
        try:
            result = city_db.city(ip)
            out["geo_country_code"] = result.country.iso_code or fallback_country
            subdivision = result.subdivisions.most_specific
            out["geo_region"] = subdivision.name or subdivision.iso_code or ""
            out["geo_city"] = result.city.name or ""
            out["geo_postal_code"] = result.postal.code or ""
            out["geo_latitude"] = result.location.latitude
            out["geo_longitude"] = result.location.longitude
            out["geo_accuracy_km"] = result.location.accuracy_radius
            source.append("local City MMDB")
        except Exception:
            pass
    asn_db = _database("POSTMASTER_GEOIP_ASN_DB", "GeoLite2-ASN.mmdb")
    if asn_db:
        try:
            result = asn_db.asn(ip)
            out["geo_asn"] = result.autonomous_system_number
            out["geo_organization"] = result.autonomous_system_organization or ""
            source.append("local ASN MMDB")
        except Exception:
            pass
    isp_db = _database("POSTMASTER_GEOIP_ISP_DB", "GeoIP2-ISP.mmdb")
    if isp_db:
        try:
            result = isp_db.isp(ip)
            out["geo_isp"] = result.isp or ""
            if result.organization:
                out["geo_organization"] = result.organization
            if not out["geo_asn"]:
                out["geo_asn"] = result.autonomous_system_number
            source.append("local ISP MMDB")
        except Exception:
            pass
    if source:
        out["geo_source"] = " + ".join(source) + (" + edge country" if fallback_country else "")
        out["geo_confidence"] = "estimated"
    out["geo_label"] = ", ".join(
        str(x) for x in (out["geo_city"], out["geo_region"], out["geo_country_code"]) if x
    ) or "Unavailable"
    return out


def ensure_email_geo_columns(analytics: Any) -> None:
    with analytics._connect() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tracking_sensitive_telemetry)")}
        for column, definition in GEO_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE tracking_sensitive_telemetry ADD COLUMN {column} {definition}")


def persist_generic_network(
    analytics: Any,
    *,
    event_id: int,
    link: dict[str, Any],
    client_ip: str,
    country_code: str,
    observed_at: str,
) -> None:
    details = enrich_ip(client_ip, country_code)
    with analytics._connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS generic_tracking_network_v984 (
                event_id INTEGER PRIMARY KEY,
                tracked_link_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                project_id TEXT,
                observed_at TEXT NOT NULL,
                source_ip TEXT NOT NULL DEFAULT '',
                geo_country_code TEXT NOT NULL DEFAULT '',
                geo_region TEXT NOT NULL DEFAULT '',
                geo_city TEXT NOT NULL DEFAULT '',
                geo_postal_code TEXT NOT NULL DEFAULT '',
                geo_latitude REAL, geo_longitude REAL, geo_accuracy_km INTEGER,
                geo_asn INTEGER, geo_isp TEXT NOT NULL DEFAULT '',
                geo_organization TEXT NOT NULL DEFAULT '',
                geo_label TEXT NOT NULL DEFAULT '',
                geo_source TEXT NOT NULL DEFAULT '',
                geo_confidence TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_generic_tracking_network_time "
            "ON generic_tracking_network_v984(observed_at DESC)"
        )
        conn.execute(
            """INSERT OR REPLACE INTO generic_tracking_network_v984(
                event_id,tracked_link_id,owner_id,project_id,observed_at,source_ip,
                geo_country_code,geo_region,geo_city,geo_postal_code,geo_latitude,
                geo_longitude,geo_accuracy_km,geo_asn,geo_isp,geo_organization,
                geo_label,geo_source,geo_confidence
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(event_id), str(link.get("id") or ""),
                str(link.get("owner_id") or ""), link.get("project_id"),
                str(observed_at), _ip(client_ip),
                details["geo_country_code"], details["geo_region"], details["geo_city"],
                details["geo_postal_code"], details["geo_latitude"], details["geo_longitude"],
                details["geo_accuracy_km"], details["geo_asn"], details["geo_isp"],
                details["geo_organization"], details["geo_label"], details["geo_source"],
                details["geo_confidence"],
            ),
        )


def generic_details(analytics: Any, event_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not event_ids:
        return {}
    with analytics._connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='generic_tracking_network_v984'"
        ).fetchone()
        if not exists:
            return {}
        marks = ",".join("?" for _ in event_ids)
        records = conn.execute(
            f"SELECT * FROM generic_tracking_network_v984 WHERE event_id IN ({marks})",
            event_ids,
        ).fetchall()
    return {int(row["event_id"]): dict(row) for row in records}


def global_tracking_network_events(
    analytics: Any,
    *,
    account_id: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    event_kind: str | None = None,
    recipient: str | None = None,
    country: str | None = None,
    city: str | None = None,
    provider: str | None = None,
    domain: str | None = None,
    asn: int | None = None,
    sort: str = "desc",
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Read across all organizational scopes; filters never act as authentication."""
    from .tracking_telemetry_v971 import _ensure_schema
    _ensure_schema(analytics)
    ensure_email_geo_columns(analytics)
    # The generic table may not have a row yet. Creating it lazily is additive.
    with analytics._connect() as conn:
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS generic_tracking_network_v984 (
                event_id INTEGER PRIMARY KEY, tracked_link_id TEXT NOT NULL,
                owner_id TEXT NOT NULL, project_id TEXT, observed_at TEXT NOT NULL,
                source_ip TEXT NOT NULL DEFAULT '', geo_country_code TEXT NOT NULL DEFAULT '',
                geo_region TEXT NOT NULL DEFAULT '', geo_city TEXT NOT NULL DEFAULT '',
                geo_postal_code TEXT NOT NULL DEFAULT '', geo_latitude REAL,
                geo_longitude REAL, geo_accuracy_km INTEGER, geo_asn INTEGER,
                geo_isp TEXT NOT NULL DEFAULT '', geo_organization TEXT NOT NULL DEFAULT '',
                geo_label TEXT NOT NULL DEFAULT '', geo_source TEXT NOT NULL DEFAULT '',
                geo_confidence TEXT NOT NULL DEFAULT ''
            );"""
        )
        geo = "s.source_ip,s.geo_country_code,s.geo_region,s.geo_city,s.geo_postal_code,s.geo_latitude,s.geo_longitude,s.geo_accuracy_km,s.geo_asn,s.geo_isp,s.geo_organization,s.geo_label,s.geo_source,s.geo_confidence"
        generic_geo = geo.replace("s.", "n.")
        union = f"""
          SELECT 'email_open' AS event_kind,s.event_id,s.observed_at,
             s.account_id,'' AS owner_id,NULL AS project_id,o.recipient,s.delivery_id,
             '' AS tracked_link_id,'' AS label,'email' AS target_type,
             o.user_agent,o.browser,o.os,o.client_source,o.metadata_confidence,{geo},'' AS destination_host
          FROM tracking_sensitive_telemetry s
          JOIN tracking_opens o ON s.event_kind='open' AND o.id=s.event_id
          WHERE s.event_kind='open'
          UNION ALL
          SELECT 'email_click',s.event_id,s.observed_at,
             s.account_id,'' AS owner_id,NULL AS project_id,c.recipient,s.delivery_id,
             c.link_id,'' AS label,'email' AS target_type,
             c.user_agent,c.browser,c.os,c.client_source,c.metadata_confidence,{geo},'' AS destination_host
          FROM tracking_sensitive_telemetry s
          JOIN tracking_clicks c ON s.event_kind='click' AND c.id=s.event_id
          WHERE s.event_kind='click'
          UNION ALL
          SELECT CASE WHEN l.target_type='stored_file' THEN 'tracked_file_download' ELSE 'tracked_url_click' END,
             n.event_id,n.observed_at,
             '' AS account_id,n.owner_id,n.project_id,'' AS recipient,'' AS delivery_id,
             n.tracked_link_id,l.label,l.target_type,
             c.user_agent,c.browser,c.os,c.client_source,c.metadata_confidence,{generic_geo},l.destination_host
          FROM generic_tracking_network_v984 n
          JOIN generic_tracking_clicks c ON c.id=n.event_id
          JOIN generic_tracking_links l ON l.id=n.tracked_link_id
        """
        filters, params = [], []
        for col, value in (
            ("account_id", account_id), ("owner_id", owner_id),
            ("project_id", project_id), ("event_kind", event_kind),
        ):
            if value is not None and str(value).strip():
                filters.append(f"{col}=?")
                params.append(str(value).strip())
        if recipient:
            filters.append("recipient LIKE ?")
            params.append("%" + str(recipient).strip() + "%")
        if country:
            filters.append("geo_country_code=?")
            params.append(str(country).strip().upper())
        if city:
            filters.append("geo_city LIKE ?")
            params.append("%" + str(city).strip() + "%")
        if provider:
            filters.append("(geo_isp LIKE ? OR geo_organization LIKE ?)")
            params.extend(["%" + str(provider).strip() + "%"] * 2)
        if asn is not None:
            filters.append("geo_asn=?")
            params.append(int(asn))
        if domain:
            filters.append("(destination_host LIKE ? OR recipient LIKE ?)")
            val = "%" + str(domain).strip().lower() + "%"
            params.extend([val, val])
        if since:
            filters.append("observed_at>=?")
            params.append(str(since))
        if until:
            filters.append("observed_at<=?")
            params.append(str(until))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        bounded = max(1, min(int(limit), 500))
        skip = max(0, int(offset))
        direction = "ASC" if str(sort).lower() == "asc" else "DESC"
        sql = f"SELECT * FROM ({union}){where} ORDER BY observed_at {direction},event_id {direction} LIMIT ? OFFSET ?"
        rows = [dict(x) for x in conn.execute(sql, (*params, bounded, skip)).fetchall()]
    return {
        "ok": True, "events": rows, "returned": len(rows), "offset": skip,
        "retention": "no_automatic_expiry",
        "access_scope": "MCP_instance_global",
        "location_warning": "Network location may identify a proxy or scanner, not the recipient",
    }
