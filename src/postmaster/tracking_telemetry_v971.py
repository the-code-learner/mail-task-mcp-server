from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from .provider_classification import classify_click_events


SENSITIVE_RETENTION_DAYS = 30
_SCHEMA_VERSION = "post-v9.7.0-tracking-sensitive-v1"
_COUNTRY_RE = re.compile(r"^[A-Z0-9]{2}$")
_INSTALLED_FLAG = "_postmaster_v971_sensitive_telemetry_installed"
_WEBGUI_INSTALLED_FLAG = "_postmaster_v971_tracking_webgui_installed"

STYLE = r"""
/* post-v9.7.0 tracking telemetry + Inbox IA */
.v971-tracking-action{display:inline-flex;align-items:center;text-decoration:none}
.v971-tracking-overlay{display:none;position:fixed;inset:0;z-index:1200;background:rgba(5,12,24,.72);padding:24px;overflow:auto}
.v971-tracking-overlay:target{display:flex;align-items:flex-start;justify-content:center}
.v971-tracking-sheet{width:min(1180px,100%);max-height:calc(100vh - 48px);overflow:auto;background:var(--surface);border:1px solid var(--border);border-radius:16px;box-shadow:0 24px 80px rgba(0,0,0,.35);padding:18px}
.v971-tracking-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;position:sticky;top:-18px;background:var(--surface);padding:18px 0 12px;z-index:2}
.v971-tracking-close{font-size:22px;text-decoration:none;line-height:1}
.v971-tracking-table code{white-space:pre-wrap;word-break:break-all}
.v971-tracking-table td{vertical-align:top}
.v971-tracking-event details{min-width:260px}
.v971-sent-tracking{margin:14px 0;border-left:4px solid var(--v963-accent-strong)}
.v971-sent-tracking h4{margin:0 0 6px}
.v971-sensitive-note{border:1px solid color-mix(in srgb,var(--v963-amber) 48%,var(--border));background:color-mix(in srgb,var(--v963-amber) 8%,var(--surface));border-radius:10px;padding:10px;margin:10px 0}
@media(max-width:760px){.v971-tracking-overlay{padding:8px}.v971-tracking-sheet{max-height:calc(100vh - 16px);border-radius:12px;padding:12px}.v971-tracking-head{top:-12px}}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_ip(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # The public handlers already select the first X-Forwarded-For hop. Validate again
    # before persistence so malformed/control-bearing values never enter the sensitive store.
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return ""


def _clean_country(value: str) -> str:
    code = str(value or "").strip().upper()
    return code if _COUNTRY_RE.fullmatch(code) else ""


def _geo_estimate(country_code: str) -> dict[str, str]:
    country = _clean_country(country_code)
    if not country:
        return {
            "geo_country_code": "",
            "geo_label": "Unavailable",
            "geo_source": "network signal unavailable",
            "geo_confidence": "unknown",
        }
    return {
        "geo_country_code": country,
        "geo_label": f"Country {country}",
        "geo_source": "edge/network country signal",
        "geo_confidence": "low",
    }


def _ensure_schema(analytics: Any) -> None:
    with analytics._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tracking_sensitive_telemetry (
                event_kind TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                delivery_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_ip TEXT NOT NULL DEFAULT '',
                geo_country_code TEXT NOT NULL DEFAULT '',
                geo_label TEXT NOT NULL DEFAULT '',
                geo_source TEXT NOT NULL DEFAULT '',
                geo_confidence TEXT NOT NULL DEFAULT '',
                schema_version TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(event_kind,event_id)
            );
            CREATE INDEX IF NOT EXISTS ix_tracking_sensitive_account_time
                ON tracking_sensitive_telemetry(account_id,observed_at DESC);
            CREATE INDEX IF NOT EXISTS ix_tracking_sensitive_delivery
                ON tracking_sensitive_telemetry(delivery_id,observed_at DESC);
            """
        )


def enforce_sensitive_retention(analytics: Any, *, days: int = SENSITIVE_RETENTION_DAYS) -> int:
    """Delete raw IP/geolocation sidecar rows older than the explicit retention window.

    Base tracking events, keyed fingerprints and aggregate counters are intentionally retained.
    """
    _ensure_schema(analytics)
    bounded = max(1, min(int(days), 365))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=bounded)).isoformat()
    with analytics._connect() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM tracking_sensitive_telemetry").fetchone()[0])
        conn.execute(
            "DELETE FROM tracking_sensitive_telemetry WHERE observed_at < ?",
            (cutoff,),
        )
        # Keep the sidecar from outliving deleted historical event rows.
        conn.execute(
            """
            DELETE FROM tracking_sensitive_telemetry
            WHERE event_kind='open'
              AND NOT EXISTS (
                  SELECT 1 FROM tracking_opens o
                  WHERE o.id=tracking_sensitive_telemetry.event_id
              )
            """
        )
        conn.execute(
            """
            DELETE FROM tracking_sensitive_telemetry
            WHERE event_kind='click'
              AND NOT EXISTS (
                  SELECT 1 FROM tracking_clicks c
                  WHERE c.id=tracking_sensitive_telemetry.event_id
              )
            """
        )
        after = int(conn.execute("SELECT COUNT(*) FROM tracking_sensitive_telemetry").fetchone()[0])
    return max(0, before - after)


def _persist_sensitive(
    analytics: Any,
    *,
    event_kind: str,
    event_id: int,
    account_id: str,
    delivery_id: str,
    observed_at: str,
    client_ip: str,
    country_code: str,
) -> None:
    _ensure_schema(analytics)
    geo = _geo_estimate(country_code)
    with analytics._connect() as conn:
        conn.execute(
            """
            INSERT INTO tracking_sensitive_telemetry(
                event_kind,event_id,account_id,delivery_id,observed_at,captured_at,
                source_ip,geo_country_code,geo_label,geo_source,geo_confidence,schema_version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_kind,event_id) DO UPDATE SET
                account_id=excluded.account_id,
                delivery_id=excluded.delivery_id,
                observed_at=excluded.observed_at,
                captured_at=excluded.captured_at,
                source_ip=excluded.source_ip,
                geo_country_code=excluded.geo_country_code,
                geo_label=excluded.geo_label,
                geo_source=excluded.geo_source,
                geo_confidence=excluded.geo_confidence,
                schema_version=excluded.schema_version
            """,
            (
                event_kind,
                int(event_id),
                str(account_id or ""),
                str(delivery_id or ""),
                str(observed_at or ""),
                _now(),
                _clean_ip(client_ip),
                geo["geo_country_code"],
                geo["geo_label"],
                geo["geo_source"],
                geo["geo_confidence"],
                _SCHEMA_VERSION,
            ),
        )
    enforce_sensitive_retention(analytics)


def _latest_open_event_id(analytics: Any, *, delivery_id: str, opened_at: str) -> tuple[int, str] | None:
    with analytics._connect() as conn:
        row = conn.execute(
            """
            SELECT id,account_id
            FROM tracking_opens
            WHERE delivery_id=? AND opened_at=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (delivery_id, opened_at),
        ).fetchone()
    if not row:
        return None
    return int(row["id"]), str(row["account_id"] or "")


def install_tracking_telemetry_v971(base: Any, core: Any) -> None:
    """Persist sensitive network telemetry in a private, short-lived sidecar.

    Visibility contract:
    - existing MCP/public tracking rows stay unchanged and never gain raw source IP fields;
    - sensitive rows are account-scoped and consumed only by authenticated WebGUI rendering;
    - retention removes the sidecar after 30 days while historical aggregate/fingerprint data stays.
    """
    analytics = base.analytics_store()
    links = core.link_store()
    _ensure_schema(analytics)
    enforce_sensitive_retention(analytics)

    if getattr(analytics, _INSTALLED_FLAG, False):
        return

    original_open = analytics.record_open
    original_amp = analytics.record_amp_view
    original_click = links.record_click

    def record_open(
        token: str,
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        result = original_open(
            token,
            user_agent=user_agent,
            client_ip=client_ip,
            country_code=country_code,
        )
        located = _latest_open_event_id(
            analytics,
            delivery_id=str(result.get("delivery_id") or ""),
            opened_at=str(result.get("opened_at") or ""),
        )
        if located:
            event_id, account_id = located
            _persist_sensitive(
                analytics,
                event_kind="open",
                event_id=event_id,
                account_id=account_id,
                delivery_id=str(result.get("delivery_id") or ""),
                observed_at=str(result.get("opened_at") or ""),
                client_ip=client_ip,
                country_code=country_code,
            )
        return result

    def record_amp_view(
        amp_token: str,
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        result = original_amp(
            amp_token,
            user_agent=user_agent,
            client_ip=client_ip,
            country_code=country_code,
        )
        located = _latest_open_event_id(
            analytics,
            delivery_id=str(result.get("delivery_id") or ""),
            opened_at=str(result.get("opened_at") or ""),
        )
        if located:
            event_id, account_id = located
            _persist_sensitive(
                analytics,
                event_kind="open",
                event_id=event_id,
                account_id=account_id,
                delivery_id=str(result.get("delivery_id") or ""),
                observed_at=str(result.get("opened_at") or ""),
                client_ip=client_ip,
                country_code=country_code,
            )
        return result

    def record_click(
        link: dict[str, Any],
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        result = original_click(
            link,
            user_agent=user_agent,
            client_ip=client_ip,
            country_code=country_code,
        )
        _persist_sensitive(
            analytics,
            event_kind="click",
            event_id=int(result.get("id") or 0),
            account_id=str(link.get("account_id") or ""),
            delivery_id=str(result.get("delivery_id") or ""),
            observed_at=str(result.get("observed_at") or ""),
            client_ip=client_ip,
            country_code=country_code,
        )
        return result

    analytics.record_open = record_open
    analytics.record_amp_view = record_amp_view
    links.record_click = record_click
    setattr(analytics, _INSTALLED_FLAG, True)


def _open_classification(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    ua = str(row.get("user_agent") or "").lower()
    source = str(row.get("client_source") or "").lower()
    event_type = str(row.get("event_type") or "")
    reasons: list[str] = []
    if "googleimageproxy" in ua or "image_proxy" in source:
        reasons.append("known/likely mailbox image-proxy signature")
        return "likely_machine_or_proxy", "medium", reasons
    if "proxy" in source or event_type == "amp_xhr":
        reasons.append("provider/client proxy path may mediate the request")
        return "machine_or_human_uncertain", "low", reasons
    reasons.append("no strong provider-proxy signature; remote fetch is still not proof of a human read")
    return "human_or_unclassified", "low", reasons


def sensitive_events_for_account(
    analytics: Any,
    *,
    account_id: str,
    message_id: str = "",
    limit: int = 120,
) -> list[dict[str, Any]]:
    """Private WebGUI-only account query. account_id is mandatory by design."""
    selected = str(account_id or "").strip()
    if not selected:
        return []
    _ensure_schema(analytics)
    enforce_sensitive_retention(analytics)
    bounded = max(1, min(int(limit), 500))
    message_filter = str(message_id or "").strip()
    with analytics._connect() as conn:
        open_params: list[Any] = [selected]
        open_where = "o.account_id=?"
        if message_filter:
            open_where += " AND d.message_id=?"
            open_params.append(message_filter)
        open_params.append(bounded)
        opens = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    'open' AS event_kind,o.id,o.delivery_id,o.campaign_id,o.account_id,o.recipient,
                    o.opened_at AS observed_at,o.event_type,o.user_agent,o.client_fingerprint,
                    o.country_code,o.browser,o.os,o.client_source,o.metadata_confidence,
                    d.message_id,d.open_count AS event_count,c.subject,
                    '' AS link_id,'' AS original_url,'' AS anchor_text,
                    s.source_ip,s.captured_at,s.geo_country_code,s.geo_label,s.geo_source,s.geo_confidence
                FROM tracking_opens o
                JOIN tracking_deliveries d ON d.id=o.delivery_id
                JOIN tracking_campaigns c ON c.id=o.campaign_id
                LEFT JOIN tracking_sensitive_telemetry s
                  ON s.event_kind='open' AND s.event_id=o.id
                WHERE {open_where}
                ORDER BY o.opened_at DESC,o.id DESC
                LIMIT ?
                """,
                open_params,
            ).fetchall()
        ]

        click_params: list[Any] = [selected]
        click_where = "cx.account_id=?"
        if message_filter:
            click_where += " AND COALESCE(NULLIF(l.message_id,''),d.message_id)=?"
            click_params.append(message_filter)
        click_params.append(bounded)
        clicks = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT
                    'click' AS event_kind,cx.id,cx.delivery_id,cx.campaign_id,cx.account_id,cx.recipient,
                    cx.observed_at,cx.event_type,cx.user_agent,cx.client_fingerprint,
                    cx.country_code,cx.browser,cx.os,cx.client_source,cx.metadata_confidence,
                    COALESCE(NULLIF(l.message_id,''),d.message_id) AS message_id,
                    0 AS event_count,c.subject,cx.link_id,l.original_url,l.anchor_text,
                    s.source_ip,s.captured_at,s.geo_country_code,s.geo_label,s.geo_source,s.geo_confidence
                FROM tracking_clicks cx
                JOIN tracking_links l ON l.id=cx.link_occurrence_id
                JOIN tracking_deliveries d ON d.id=cx.delivery_id
                JOIN tracking_campaigns c ON c.id=cx.campaign_id
                LEFT JOIN tracking_sensitive_telemetry s
                  ON s.event_kind='click' AND s.event_id=cx.id
                WHERE {click_where}
                ORDER BY cx.observed_at DESC,cx.id DESC
                LIMIT ?
                """,
                click_params,
            ).fetchall()
        ]

    if clicks:
        classified = classify_click_events(clicks)
        click_counts: dict[tuple[str, str], int] = {}
        for row in clicks:
            key = (str(row.get("delivery_id") or ""), str(row.get("link_id") or ""))
            click_counts[key] = click_counts.get(key, 0) + 1
        for row in classified:
            row["estimated_classification"] = str(row.get("provider_classification") or "uncertain")
            row["classification_confidence"] = (
                "high" if row["estimated_classification"] == "known_email_proxy"
                else "medium" if int(row.get("provider_likelihood") or 0) >= 70
                else "low"
            )
            row["classification_reasons"] = list(row.get("classification_reasons") or [])
            row["event_count"] = click_counts.get(
                (str(row.get("delivery_id") or ""), str(row.get("link_id") or "")),
                1,
            )
        clicks = classified

    for row in opens:
        label, confidence, reasons = _open_classification(row)
        row["estimated_classification"] = label
        row["classification_confidence"] = confidence
        row["classification_reasons"] = reasons

    rows = opens + clicks
    rows.sort(
        key=lambda row: (str(row.get("observed_at") or ""), int(row.get("id") or 0)),
        reverse=True,
    )
    return rows[:bounded]


def _fmt_geo(row: dict[str, Any]) -> str:
    label = str(row.get("geo_label") or "Unavailable")
    source = str(row.get("geo_source") or "")
    confidence = str(row.get("geo_confidence") or "unknown")
    return f"{label} · {source} · confidence {confidence}"


def _event_details(row: dict[str, Any]) -> str:
    url = str(row.get("original_url") or "")
    reasons = "; ".join(str(value) for value in row.get("classification_reasons") or []) or "—"
    return f"""
<details><summary>Expanded details</summary>
<div class="small">
<p><strong>Fingerprint</strong><br><code>{escape(str(row.get('client_fingerprint') or '—'))}</code></p>
<p><strong>Persisted source IP</strong><br><code>{escape(str(row.get('source_ip') or 'unavailable'))}</code></p>
<p><strong>Geolocation estimate</strong><br>{escape(_fmt_geo(row))}</p>
<p><strong>Client</strong><br>{escape(str(row.get('browser') or '—'))} · {escape(str(row.get('os') or '—'))} · {escape(str(row.get('client_source') or '—'))}</p>
<p><strong>User-Agent</strong><br><code>{escape(str(row.get('user_agent') or '—'))}</code></p>
<p><strong>Exact URL</strong><br><code>{escape(url or 'not applicable')}</code></p>
<p><strong>Classification evidence</strong><br>{escape(reasons)}</p>
<p><strong>Telemetry captured</strong><br>{escape(str(row.get('captured_at') or 'not retained'))}</p>
</div></details>
"""


def _events_table(rows: list[dict[str, Any]], *, compact: bool = False) -> str:
    if not rows:
        return '<p class="muted">No retained tracking activity for this scope.</p>'
    body: list[str] = []
    for row in rows:
        kind = "Click" if row.get("event_kind") == "click" else "Open"
        url = str(row.get("original_url") or "")
        title = str(row.get("subject") or "Tracked message")
        count = int(row.get("event_count") or 1)
        classification = str(row.get("estimated_classification") or "uncertain")
        if compact:
            body.append(
                "<tr>"
                f"<td>{escape(kind)}</td>"
                f"<td>{escape(str(row.get('recipient') or ''))}</td>"
                f"<td>{escape(str(row.get('observed_at') or ''))}</td>"
                f"<td>{count}</td>"
                f"<td>{escape(classification)}</td>"
                f"<td>{_event_details(row)}</td>"
                "</tr>"
            )
        else:
            body.append(
                '<tr class="v971-tracking-event">'
                f"<td>{escape(kind)}</td>"
                f"<td><strong>{escape(title)}</strong><br><span class=\"small muted\">{escape(str(row.get('recipient') or ''))}</span></td>"
                f"<td>{escape(str(row.get('observed_at') or ''))}<br><span class=\"small muted\">count {count}</span></td>"
                f"<td>{'<code>' + escape(url) + '</code>' if url else '—'}</td>"
                f"<td>{escape(classification)}<br><span class=\"small muted\">confidence {escape(str(row.get('classification_confidence') or 'low'))}</span></td>"
                f"<td>{_event_details(row)}</td>"
                "</tr>"
            )
    if compact:
        head = "<tr><th>Event</th><th>Recipient</th><th>Time</th><th>Count</th><th>Estimate</th><th>Details</th></tr>"
    else:
        head = "<tr><th>Event</th><th>Message / recipient</th><th>Event time / count</th><th>Exact URL</th><th>Estimated classification</th><th>Details</th></tr>"
    return '<div class="scroll"><table class="v971-tracking-table"><thead>' + head + "</thead><tbody>" + "".join(body) + "</tbody></table></div>"


def account_tracking_overlay(analytics: Any, *, account_id: str) -> str:
    rows = sensitive_events_for_account(analytics, account_id=account_id, limit=120)
    return f"""
<div id="v971-tracking-overlay" class="v971-tracking-overlay" role="dialog" aria-modal="true" aria-labelledby="v971-tracking-title">
<section class="v971-tracking-sheet">
<div class="v971-tracking-head"><div><h3 id="v971-tracking-title">Recent tracking activity</h3><p class="small muted">Selected account only. Classification and geolocation are estimates, never proof.</p></div><a class="v971-tracking-close" href="#inbox" aria-label="Close tracking">×</a></div>
<div class="v971-sensitive-note"><strong>Sensitive telemetry policy.</strong> Raw source IP and the network-derived geography estimate are retained for {SENSITIVE_RETENTION_DAYS} days, visible only on the authenticated account-scoped WebGUI surface, and never added to legacy MCP tracking responses. Logs must not include these values.</div>
{_events_table(rows)}
</section></div>
"""


def sent_tracking_block(analytics: Any, *, account_id: str, message_id: str) -> str:
    mid = str(message_id or "").strip()
    if not mid:
        return ""
    rows = sensitive_events_for_account(
        analytics,
        account_id=account_id,
        message_id=mid,
        limit=80,
    )
    if not rows:
        return '<section class="card v971-sent-tracking"><h4>Tracking</h4><p class="small muted">No retained tracking events for this sent message.</p></section>'
    recipients = len({str(row.get("recipient") or "") for row in rows if row.get("recipient")})
    latest = str(rows[0].get("observed_at") or "")
    return f"""
<section class="card v971-sent-tracking">
<h4>Tracking</h4>
<p class="small muted">{len(rows)} retained event(s) · {recipients} recipient(s) · latest {escape(latest)}. Human-vs-machine and location values are estimates.</p>
<details><summary>Show tracking event details</summary>{_events_table(rows, compact=True)}</details>
</section>
"""


def _inject_before_last_detail_close(html: str, fragment: str) -> str:
    if not fragment:
        return html
    marker = "</div></td></tr>"
    index = html.rfind(marker)
    if index < 0:
        return html
    return html[:index] + fragment + html[index:]


def install_tracking_webgui_v971(base: Any, core: Any, webgui_v963: Any, webgui_v962: Any) -> None:
    """Make Tracking contextual to Inbox/Sent without changing MCP tool names or send semantics."""
    if getattr(webgui_v963, _WEBGUI_INSTALLED_FLAG, False):
        return
    original = webgui_v963.render_inbox_v963

    def render_inbox_v971(proxied_base: Any, request: Any) -> str:
        html = original(proxied_base, request)
        try:
            accounts, account_id = webgui_v963._selected(proxied_base, request)
            _ = accounts
            if not account_id:
                return html
            action = '<a class="btn v971-tracking-action" href="#v971-tracking-overlay">Tracking</a>'
            html = html.replace('<div class="v963-refresh">', '<div class="v963-refresh">' + action, 1)
            overlay = account_tracking_overlay(base.analytics_store(), account_id=str(account_id))
            close = html.rfind("</section>")
            if close >= 0:
                html = html[:close] + overlay + html[close:]

            uid = str(request.query_params.get("message_uid") or "").strip()
            if uid:
                mailbox = str(request.query_params.get("mailbox") or "INBOX").strip() or "INBOX"
                catalog = proxied_base.mailbox_cache_store().list_mailboxes(str(account_id))
                role = webgui_v963._role(catalog, mailbox)
                if role == "sent":
                    detail = proxied_base.mailbox_cache_store().get_message(
                        str(account_id), mailbox, uid, include_body=False
                    ) or {}
                    block = sent_tracking_block(
                        base.analytics_store(),
                        account_id=str(account_id),
                        message_id=str(detail.get("message_id") or ""),
                    )
                    html = _inject_before_last_detail_close(html, block)
        except Exception:
            # Tracking enrichment is read-only UI. It must never make Inbox/Sent unusable.
            return html
        return html

    webgui_v963.render_inbox_v963 = render_inbox_v971
    webgui_v963.v960.render_inbox = render_inbox_v971
    webgui_v963.v951.render_inbox = render_inbox_v971
    if "post-v9.7.0 tracking telemetry + Inbox IA" not in webgui_v962.BASE_STYLE:
        webgui_v962.BASE_STYLE += STYLE
    setattr(webgui_v963, _WEBGUI_INSTALLED_FLAG, True)
