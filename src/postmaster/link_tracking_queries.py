from __future__ import annotations

from typing import Any

from .provider_classification import classify_click_events, summarize_click_classification


class LinkTrackingQueriesMixin:
    def _classification_events(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("c.campaign_id", campaign_id),
            ("c.delivery_id", delivery_id),
            ("c.link_id", link_id),
            ("c.account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*
                FROM tracking_clicks c
                {where}
                ORDER BY c.observed_at ASC,c.id ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_links(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        account_id: str | None = None,
        clicked_only: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("l.campaign_id", campaign_id),
            ("l.delivery_id", delivery_id),
            ("l.link_id", link_id),
            ("l.account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if clicked_only:
            clauses.append("EXISTS (SELECT 1 FROM tracking_clicks cx WHERE cx.link_occurrence_id=l.id)")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.id AS occurrence_id,l.link_id,l.campaign_id,l.delivery_id,l.account_id,
                       l.recipient,l.original_url,l.normalized_url,l.destination_host,l.position,
                       l.anchor_text,l.message_id,l.created_at,
                       COUNT(c.id) AS total_clicks,
                       COUNT(DISTINCT c.client_fingerprint) AS unique_clicks,
                       MIN(c.observed_at) AS first_click,
                       MAX(c.observed_at) AS last_click
                FROM tracking_links l
                LEFT JOIN tracking_clicks c ON c.link_occurrence_id=l.id
                {where}
                GROUP BY l.id
                ORDER BY l.created_at DESC,l.position ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_click_events(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        recipient: str | None = None,
        account_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("c.campaign_id", campaign_id),
            ("c.delivery_id", delivery_id),
            ("c.link_id", link_id),
            ("c.account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if recipient:
            clauses.append("c.recipient=? COLLATE NOCASE")
            params.append(recipient.strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 2000))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*,l.original_url,l.normalized_url,l.destination_host,l.position,l.anchor_text
                FROM tracking_clicks c
                JOIN tracking_links l ON l.id=c.link_occurrence_id
                {where}
                ORDER BY c.observed_at DESC,c.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return classify_click_events([dict(row) for row in rows])

    def summary(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("l.campaign_id", campaign_id),
            ("l.delivery_id", delivery_id),
            ("l.link_id", link_id),
            ("l.account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT l.id) AS link_occurrences,
                       COUNT(DISTINCT l.link_id) AS logical_links,
                       COUNT(c.id) AS total_clicks,
                       COUNT(DISTINCT c.delivery_id || '|' || c.link_id || '|' || c.client_fingerprint) AS unique_clicks,
                       COUNT(DISTINCT CASE WHEN c.id IS NOT NULL THEN c.recipient END) AS unique_recipients,
                       MIN(c.observed_at) AS first_click,
                       MAX(c.observed_at) AS last_click
                FROM tracking_links l
                LEFT JOIN tracking_clicks c ON c.link_occurrence_id=l.id
                {where}
                """,
                params,
            ).fetchone()
        out = dict(row)
        out["unique_click_definition"] = "delivery_id + link_id + client_fingerprint"
        out["fingerprint_fallback"] = (
            "If IP and User-Agent are unavailable, the existing keyed fingerprint pipeline "
            "uses the stable HMAC of the empty pair, collapsing unknown repeat fetches for that delivery/link."
        )
        qualitative = summarize_click_classification(
            self._classification_events(
                campaign_id=campaign_id,
                delivery_id=delivery_id,
                link_id=link_id,
                account_id=account_id,
            )
        )
        out["qualitative_estimate"] = qualitative
        out["likely_provider_unique_clicks"] = qualitative["likely_provider_unique_clicks"]
        out["likely_human_or_unclassified_unique_clicks"] = qualitative["likely_human_or_unclassified_unique_clicks"]
        out["uncertain_unique_clicks"] = qualitative["uncertain_unique_clicks"]
        out["potential_provider_share"] = qualitative["potential_provider_share"]
        out["provider_suspects"] = qualitative["provider_suspects"]
        out["provider_classification_model"] = qualitative["classification_model"]
        return out

    def top_links(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        account_id: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("l.campaign_id", campaign_id),
            ("l.delivery_id", delivery_id),
            ("l.account_id", account_id),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 200))
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.link_id,
                       MIN(l.anchor_text) AS anchor_text,
                       MIN(l.original_url) AS original_url,
                       MIN(l.normalized_url) AS normalized_url,
                       MIN(l.destination_host) AS destination_host,
                       MIN(l.position) AS position,
                       COUNT(c.id) AS total_clicks,
                       COUNT(DISTINCT c.delivery_id || '|' || c.link_id || '|' || c.client_fingerprint) AS unique_clicks,
                       COUNT(DISTINCT CASE WHEN c.id IS NOT NULL THEN c.recipient END) AS unique_recipients,
                       MIN(c.observed_at) AS first_click,
                       MAX(c.observed_at) AS last_click
                FROM tracking_links l
                LEFT JOIN tracking_clicks c ON c.link_occurrence_id=l.id
                {where}
                GROUP BY l.link_id
                ORDER BY total_clicks DESC,unique_clicks DESC,l.link_id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def unified_events(
        self,
        *,
        campaign_id: str | None = None,
        delivery_id: str | None = None,
        link_id: str | None = None,
        recipient: str | None = None,
        account_id: str | None = None,
        event_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        want = (event_type or "all").strip().lower()
        rows: list[dict[str, Any]] = []
        if want in {"all", "pixel", "amp_xhr"} and not link_id:
            opens = self.analytics.list_open_events(
                delivery_id=delivery_id,
                campaign_id=campaign_id,
                recipient=recipient,
                account_id=account_id,
                limit=limit,
            )
            for event in opens:
                if want != "all" and str(event.get("event_type")) != want:
                    continue
                rows.append({
                    **event,
                    "observed_at": event.get("opened_at", ""),
                    "link_id": "",
                    "anchor_text": "",
                    "original_url": "",
                    "destination_host": "",
                    "position": None,
                })
        if want in {"all", "link"}:
            rows.extend(
                self.list_click_events(
                    campaign_id=campaign_id,
                    delivery_id=delivery_id,
                    link_id=link_id,
                    recipient=recipient,
                    account_id=account_id,
                    limit=limit,
                )
            )
        rows.sort(key=lambda item: str(item.get("observed_at") or ""), reverse=True)
        return rows[: max(1, min(int(limit), 2000))]
