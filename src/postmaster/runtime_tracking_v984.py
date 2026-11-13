"""v9.8.4 additive global tracking detail API and generic-link network persistence."""
from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

from .tracking_geoip_v984 import (
    generic_details, geoip_status, global_tracking_network_events,
    persist_generic_network,
)


def install_runtime_tracking_v984(base: Any, core: Any, *, store: Any) -> dict[str, Any]:
    analytics = base.analytics_store()
    original_record_click = store.record_click

    def record_click(link: dict[str, Any], *, user_agent: str = "",
                     client_ip: str = "", country_code: str = "") -> dict[str, Any]:
        result = original_record_click(
            link, user_agent=user_agent, client_ip=client_ip, country_code=country_code,
        )
        if isinstance(result, dict) and result.get("ok") and result.get("id"):
            persist_generic_network(
                analytics, event_id=int(result["id"]), link=link,
                client_ip=client_ip, country_code=country_code,
                observed_at=str(result.get("observed_at") or ""),
            )
            result = {**result, "raw_ip_stored": True}
        return result

    store.record_click = record_click

    def list_tracking_network_events(
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
    ):
        """Read-only. Global event history, including raw IP, City/ASN/ISP and full client metadata.

        MCP authorization is the existing instance-level boundary; owner/project/account are
        OPTIONAL organizational filters, never ACLs. This endpoint returns personal network
        data and must remain behind the authenticated MCP control plane. No public callbacks
        expose the details or new tokens.
        """
        try:
            return global_tracking_network_events(
                analytics, account_id=account_id, owner_id=owner_id,
                project_id=project_id, event_kind=event_kind,
                recipient=recipient, country=country, city=city,
                provider=provider, domain=domain, asn=asn, sort=sort,
                since=since, until=until, limit=limit, offset=offset,
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def get_tracking_network_event(event_kind: str, event_id: int):
        """Read-only. Get one event and its raw IP/network details without an owner/project ACL."""
        if event_kind not in {
            "email_open", "email_click", "tracked_url_click", "tracked_file_download",
        }:
            return {"ok": False, "error": "Unknown event kind"}
        try:
            with analytics._connect() as conn:
                if event_kind.startswith("email_"):
                    event_kind_db = "open" if event_kind == "email_open" else "click"
                    q = conn.execute(
                        "SELECT observed_at FROM tracking_sensitive_telemetry "
                        "WHERE event_kind=? AND event_id=?",
                        (event_kind_db, int(event_id)),
                    ).fetchone()
                else:
                    q = conn.execute(
                        "SELECT observed_at FROM generic_tracking_network_v984 WHERE event_id=?",
                        (int(event_id),),
                    ).fetchone()
            if not q:
                return {"ok": False, "error": "Event not found"}
            # A same-timestamp tie is possible. Select exactly by kind and numeric id.
            data = global_tracking_network_events(
                analytics, event_kind=event_kind, since=str(q[0]), until=str(q[0]),
                limit=500,
            )
            for row in data["events"]:
                if int(row["event_id"]) == int(event_id):
                    return {"ok": True, "event": row}
            return {"ok": False, "error": "Event not found"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    old_generic_events = core.list_tracked_link_events

    def list_tracked_link_events(owner_id: str, project_id: str | None = None,
                                 tracked_link_id: str | None = None, limit: int = 100):
        """Read-only. All generic-link click details including persisted raw IP and GeoIP."""
        result = old_generic_events(
            owner_id=owner_id, project_id=project_id,
            tracked_link_id=tracked_link_id, limit=limit,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return result
        rows = result.get("events") or []
        try:
            detail = generic_details(analytics, [int(x["id"]) for x in rows])
            enriched = []
            for event in rows:
                row = dict(event)
                row.update(detail.get(int(row["id"]), {}))
                enriched.append(row)
            return {**result, "events": enriched, "raw_ip_stored": True}
        except Exception as exc:
            return {**result, "network_enrichment_error": type(exc).__name__}

    previous_status = core.runtime_status

    def runtime_status():
        original = previous_status()
        return {
            **(original if isinstance(original, dict) else {}),
            "version_capability": "9.8.4",
            "mcp_command_count_expected": 130,
            "tracking_network_v984": {
                "raw_ip_mcp_available": True,
                "instance_global_access": True,
                "owner_project_account_are_filters": True,
                "automatic_retention_days": None,
                **geoip_status(),
            },
        }

    for name, fn in (
        ("list_tracked_link_events", list_tracked_link_events),
        ("runtime_status", runtime_status),
    ):
        core.mcp.remove_tool(name)
        core.mcp.add_tool(
            fn, name=name,
            annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
        )
        setattr(core, name, fn)
        setattr(base, name, fn)

    for name, fn in (
        ("list_tracking_network_events", list_tracking_network_events),
        ("get_tracking_network_event", get_tracking_network_event),
    ):
        core.mcp.add_tool(
            fn, name=name,
            annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
        )
        setattr(core, name, fn)
        setattr(base, name, fn)

    return {
        "runtime_status": runtime_status, "list_events": list_tracking_network_events,
        "get_event": get_tracking_network_event,
        "mcp_command_count_expected": 130,
    }
