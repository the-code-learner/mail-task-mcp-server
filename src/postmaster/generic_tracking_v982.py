from __future__ import annotations

import hashlib
import hmac
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from .email_analytics import (
    AnalyticsError,
    EmailAnalyticsStore,
    _client_metadata,
    _now,
    _safe_base_url,
    _token,
)
from .link_tracking_html import already_tracked_url, eligible_web_url, normalized_url

GENERIC_TRACKED_LINK_TOKEN_PREFIX = "glnk_"


class GenericTrackedLinkError(ValueError):
    pass


class GenericTrackedLinkStore:
    """Project/owner-scoped tracked URLs independent from email deliveries.

    Generic tracked links intentionally live beside, not inside, the email tracking tables.
    The public callback path is shared (/t/c/<token>) but generic tokens carry a fixed prefix,
    so legacy email/file tokens remain unambiguous and preserve their existing semantics.
    """

    def __init__(self, analytics: EmailAnalyticsStore):
        self.analytics = analytics
        self._init_schema()

    def _connect(self):
        return self.analytics._connect()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS generic_tracking_links (
                    id TEXT PRIMARY KEY,
                    tracking_token TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    project_id TEXT,
                    label TEXT NOT NULL,
                    destination_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    destination_host TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    idempotency_key TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS ix_generic_tracking_links_scope
                    ON generic_tracking_links(owner_id, project_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_generic_tracking_links_label
                    ON generic_tracking_links(owner_id, label COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS ix_generic_tracking_links_status
                    ON generic_tracking_links(status, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_generic_tracking_links_idempotency
                    ON generic_tracking_links(owner_id, IFNULL(project_id, ''), idempotency_key)
                    WHERE idempotency_key <> '';

                CREATE TABLE IF NOT EXISTS generic_tracking_clicks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracked_link_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    user_agent TEXT NOT NULL DEFAULT '',
                    client_fingerprint TEXT NOT NULL DEFAULT '',
                    country_code TEXT NOT NULL DEFAULT '',
                    browser TEXT NOT NULL DEFAULT '',
                    os TEXT NOT NULL DEFAULT '',
                    client_source TEXT NOT NULL DEFAULT '',
                    metadata_confidence TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(tracked_link_id) REFERENCES generic_tracking_links(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_generic_tracking_clicks_link
                    ON generic_tracking_clicks(tracked_link_id, observed_at);
                """
            )

    @staticmethod
    def is_generic_token(token: str) -> bool:
        return str(token or "").startswith(GENERIC_TRACKED_LINK_TOKEN_PREFIX)

    @staticmethod
    def _clean_owner(owner_id: str) -> str:
        owner = str(owner_id or "").strip()
        if not owner:
            raise GenericTrackedLinkError("owner_id is required")
        if len(owner) > 255:
            raise GenericTrackedLinkError("owner_id is too long")
        return owner

    @staticmethod
    def _clean_project(project_id: str | None) -> str | None:
        if project_id is None:
            return None
        project = str(project_id).strip()
        if not project:
            return None
        if len(project) > 255:
            raise GenericTrackedLinkError("project_id is too long")
        return project

    @staticmethod
    def _clean_label(label: str) -> str:
        value = " ".join(str(label or "").split())
        if not value:
            raise GenericTrackedLinkError("label is required")
        if len(value) > 240:
            raise GenericTrackedLinkError("label must be 240 characters or fewer")
        return value

    @staticmethod
    def _clean_idempotency_key(value: str | None) -> str:
        key = str(value or "").strip()
        if len(key) > 255:
            raise GenericTrackedLinkError("idempotency_key is too long")
        return key

    @staticmethod
    def _clean_destination(destination_url: str) -> tuple[str, str, str]:
        destination = str(destination_url or "").strip()
        if not eligible_web_url(destination):
            raise GenericTrackedLinkError("destination_url must be an absolute HTTP/HTTPS URL")
        public_base = _safe_base_url()
        if already_tracked_url(destination, public_base):
            raise GenericTrackedLinkError(
                "destination_url is already a Postmaster tracked URL; use its existing tracked URL instead"
            )
        normalized = normalized_url(destination)
        parts = urlsplit(destination)
        return destination, normalized, (parts.hostname or "").lower()

    @staticmethod
    def _project_match_sql(project_id: str | None, *, column: str = "project_id") -> tuple[str, list[Any]]:
        if project_id is None:
            return "", []
        return f" AND {column}=?", [project_id]

    @staticmethod
    def _tracked_url(token: str) -> str:
        return f"{_safe_base_url()}/t/c/{token}"

    def _safe_link(self, row: dict[str, Any], *, aggregate: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "id": str(row["id"]),
            "owner_id": str(row["owner_id"]),
            "project_id": row.get("project_id"),
            "label": str(row["label"]),
            "destination_url": str(row["destination_url"]),
            "normalized_url": str(row["normalized_url"]),
            "destination_host": str(row.get("destination_host") or ""),
            "status": str(row.get("status") or "active"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "revoked_at": row.get("revoked_at"),
            # The complete capability URL is intentionally returned: this MCP surface exists so
            # clients/LLMs can place the tracked URL in arbitrary user-authorized contexts.
            "tracked_url": self._tracked_url(str(row["tracking_token"])),
        }
        if aggregate:
            result.update(
                {
                    "total_clicks": int(aggregate.get("total_clicks") or 0),
                    "unique_clicks": int(aggregate.get("unique_clicks") or 0),
                    "first_click": aggregate.get("first_click"),
                    "last_click": aggregate.get("last_click"),
                }
            )
        return result

    def _row_by_id(
        self,
        tracked_link_id: str,
        *,
        owner_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        link_id = str(tracked_link_id or "").strip()
        if not link_id:
            raise GenericTrackedLinkError("tracked_link_id is required")
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        project_sql, project_params = self._project_match_sql(project)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM generic_tracking_links
                WHERE id=? AND owner_id=?{project_sql}
                """,
                [link_id, owner, *project_params],
            ).fetchone()
        if not row:
            raise GenericTrackedLinkError("Tracked link not found in the requested owner/project scope")
        return dict(row)

    def _aggregate(self, tracked_link_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_clicks,
                       COUNT(DISTINCT client_fingerprint) AS unique_clicks,
                       MIN(observed_at) AS first_click,
                       MAX(observed_at) AS last_click
                FROM generic_tracking_clicks
                WHERE tracked_link_id=?
                """,
                (tracked_link_id,),
            ).fetchone()
        return dict(row)

    def create(
        self,
        *,
        owner_id: str,
        destination_url: str,
        label: str,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        clean_label = self._clean_label(label)
        destination, normalized, host = self._clean_destination(destination_url)
        idem = self._clean_idempotency_key(idempotency_key)

        if idem:
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    SELECT * FROM generic_tracking_links
                    WHERE owner_id=? AND IFNULL(project_id,'')=IFNULL(?, '') AND idempotency_key=?
                    """,
                    (owner, project, idem),
                ).fetchone()
            if existing:
                row = dict(existing)
                if (
                    str(row["destination_url"]) != destination
                    or str(row["label"]) != clean_label
                ):
                    raise GenericTrackedLinkError(
                        "idempotency_key is already bound to a different tracked-link intent"
                    )
                out = self._safe_link(row, aggregate=self._aggregate(str(row["id"])))
                out["ok"] = True
                out["idempotent_replay"] = True
                return out

        now = _now()
        last_error: Exception | None = None
        for _ in range(4):
            tracked_link_id = f"gtl_{_token(12)}"
            token = GENERIC_TRACKED_LINK_TOKEN_PREFIX + _token(24)
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO generic_tracking_links(
                            id,tracking_token,owner_id,project_id,label,destination_url,
                            normalized_url,destination_host,status,created_at,updated_at,
                            revoked_at,idempotency_key
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            tracked_link_id,
                            token,
                            owner,
                            project,
                            clean_label,
                            destination,
                            normalized,
                            host,
                            "active",
                            now,
                            now,
                            None,
                            idem,
                        ),
                    )
                row = self._row_by_id(
                    tracked_link_id,
                    owner_id=owner,
                    project_id=project,
                )
                out = self._safe_link(row, aggregate=self._aggregate(tracked_link_id))
                out["ok"] = True
                out["idempotent_replay"] = False
                return out
            except sqlite3.IntegrityError as exc:
                last_error = exc
                if idem:
                    # A concurrent identical request may have won the partial unique index.
                    with self._connect() as conn:
                        existing = conn.execute(
                            """
                            SELECT * FROM generic_tracking_links
                            WHERE owner_id=? AND IFNULL(project_id,'')=IFNULL(?, '')
                              AND idempotency_key=?
                            """,
                            (owner, project, idem),
                        ).fetchone()
                    if existing:
                        row = dict(existing)
                        if (
                            str(row["destination_url"]) != destination
                            or str(row["label"]) != clean_label
                        ):
                            raise GenericTrackedLinkError(
                                "idempotency_key is already bound to a different tracked-link intent"
                            ) from exc
                        out = self._safe_link(
                            row,
                            aggregate=self._aggregate(str(row["id"])),
                        )
                        out["ok"] = True
                        out["idempotent_replay"] = True
                        return out
        raise GenericTrackedLinkError(
            f"Unable to allocate a unique tracked link: {type(last_error).__name__ if last_error else 'unknown'}"
        )

    def list_links(
        self,
        *,
        owner_id: str,
        project_id: str | None = None,
        label: str | None = None,
        active_only: bool = True,
        clicked_only: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        clauses = ["l.owner_id=?"]
        params: list[Any] = [owner]
        if project is not None:
            clauses.append("l.project_id=?")
            params.append(project)
        if label:
            clauses.append("l.label=? COLLATE NOCASE")
            params.append(self._clean_label(label))
        if active_only:
            clauses.append("l.status='active'")
        if clicked_only:
            clauses.append(
                "EXISTS (SELECT 1 FROM generic_tracking_clicks c2 WHERE c2.tracked_link_id=l.id)"
            )
        limit = max(1, min(int(limit), 1000))
        params.append(limit)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT l.*,
                       COUNT(c.id) AS total_clicks,
                       COUNT(DISTINCT c.client_fingerprint) AS unique_clicks,
                       MIN(c.observed_at) AS first_click,
                       MAX(c.observed_at) AS last_click
                FROM generic_tracking_links l
                LEFT JOIN generic_tracking_clicks c ON c.tracked_link_id=l.id
                WHERE {where}
                GROUP BY l.id
                ORDER BY l.created_at DESC,l.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        links = [
            self._safe_link(
                dict(row),
                aggregate={
                    "total_clicks": row["total_clicks"],
                    "unique_clicks": row["unique_clicks"],
                    "first_click": row["first_click"],
                    "last_click": row["last_click"],
                },
            )
            for row in rows
        ]
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "links": links,
            "count": len(links),
            "tracked_urls_intentionally_exposed": True,
        }

    def get(
        self,
        *,
        owner_id: str,
        tracked_link_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._row_by_id(
            tracked_link_id,
            owner_id=owner_id,
            project_id=project_id,
        )
        out = self._safe_link(
            row,
            aggregate=self._aggregate(str(row["id"])),
        )
        out["ok"] = True
        out["unique_click_definition"] = "tracked_link_id + client_fingerprint"
        out["tracked_urls_intentionally_exposed"] = True
        return out

    def list_events(
        self,
        *,
        owner_id: str,
        project_id: str | None = None,
        tracked_link_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        clauses = ["l.owner_id=?"]
        params: list[Any] = [owner]
        if project is not None:
            clauses.append("l.project_id=?")
            params.append(project)
        if tracked_link_id:
            clauses.append("l.id=?")
            params.append(str(tracked_link_id).strip())
        limit = max(1, min(int(limit), 2000))
        params.append(limit)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.id,c.tracked_link_id,c.observed_at,c.user_agent,c.client_fingerprint,
                       c.country_code,c.browser,c.os,c.client_source,c.metadata_confidence,
                       l.label,l.destination_url,l.destination_host
                FROM generic_tracking_clicks c
                JOIN generic_tracking_links l ON l.id=c.tracked_link_id
                WHERE {where}
                ORDER BY c.observed_at DESC,c.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        events = [dict(row) for row in rows]
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "events": events,
            "count": len(events),
            "raw_ip_stored": False,
        }

    def revoke(
        self,
        *,
        owner_id: str,
        tracked_link_id: str,
        project_id: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        if confirm is not True:
            raise GenericTrackedLinkError(
                "Revoking a tracked link disables its public redirect; retry with confirm=true"
            )
        row = self._row_by_id(
            tracked_link_id,
            owner_id=owner_id,
            project_id=project_id,
        )
        if str(row.get("status") or "") == "revoked":
            out = self._safe_link(
                row,
                aggregate=self._aggregate(str(row["id"])),
            )
            out.update({"ok": True, "revoked": True, "idempotent": True})
            return out
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE generic_tracking_links
                SET status='revoked',updated_at=?,revoked_at=?
                WHERE id=?
                """,
                (now, now, row["id"]),
            )
        updated = self._row_by_id(
            str(row["id"]),
            owner_id=owner_id,
            project_id=project_id,
        )
        out = self._safe_link(
            updated,
            aggregate=self._aggregate(str(row["id"])),
        )
        out.update({"ok": True, "revoked": True, "idempotent": False})
        return out

    def get_by_token(self, token: str) -> dict[str, Any]:
        value = str(token or "").strip()
        if not self.is_generic_token(value):
            raise GenericTrackedLinkError("Unknown generic tracked-link token")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM generic_tracking_links WHERE tracking_token=? AND status='active'",
                (value,),
            ).fetchone()
        if not row:
            raise GenericTrackedLinkError("Unknown generic tracked-link token")
        return dict(row)

    def record_click(
        self,
        link: dict[str, Any],
        *,
        user_agent: str = "",
        client_ip: str = "",
        country_code: str = "",
    ) -> dict[str, Any]:
        link_id = str(link.get("id") or "").strip()
        if not link_id:
            raise GenericTrackedLinkError("Tracked link id is missing")
        ua = str(user_agent or "")[:500]
        metadata = _client_metadata("link", ua, country_code)
        fingerprint = hmac.new(
            self.analytics._fingerprint_key,
            f"{client_ip or ''}|{ua}".encode("utf-8", "ignore"),
            hashlib.sha256,
        ).hexdigest()[:24]
        observed_at = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO generic_tracking_clicks(
                    tracked_link_id,observed_at,user_agent,client_fingerprint,country_code,
                    browser,os,client_source,metadata_confidence
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    link_id,
                    observed_at,
                    ua,
                    fingerprint,
                    metadata["country_code"],
                    metadata["browser"],
                    metadata["os"],
                    metadata["client_source"],
                    metadata["metadata_confidence"],
                ),
            )
            event_id = int(cur.lastrowid)
        return {
            "ok": True,
            "id": event_id,
            "tracked_link_id": link_id,
            "observed_at": observed_at,
            "client_fingerprint": fingerprint,
            "country_code": metadata["country_code"],
            "browser": metadata["browser"],
            "os": metadata["os"],
            "client_source": metadata["client_source"],
            "metadata_confidence": metadata["metadata_confidence"],
            "raw_ip_stored": False,
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            links = int(
                conn.execute("SELECT COUNT(*) FROM generic_tracking_links").fetchone()[0]
            )
            active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM generic_tracking_links WHERE status='active'"
                ).fetchone()[0]
            )
            clicks = int(
                conn.execute("SELECT COUNT(*) FROM generic_tracking_clicks").fetchone()[0]
            )
        return {
            "enabled": True,
            "links": links,
            "active_links": active,
            "click_events": clicks,
            "public_path": "/t/c/*",
            "token_prefix": GENERIC_TRACKED_LINK_TOKEN_PREFIX,
            "tracked_urls_returned_via_mcp": True,
            "email_tracking_tables_unchanged": True,
            "raw_ip_stored": False,
        }


__all__ = [
    "GENERIC_TRACKED_LINK_TOKEN_PREFIX",
    "GenericTrackedLinkError",
    "GenericTrackedLinkStore",
]
