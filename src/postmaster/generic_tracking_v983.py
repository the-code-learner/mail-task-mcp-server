from __future__ import annotations

from functools import lru_cache
import sqlite3
from typing import Any, Callable

from .email_analytics import _now, _token
from .file_store import FileStore, FileStoreError
from .generic_tracking_v982 import GenericTrackedLinkError, GenericTrackedLinkStore


class GenericTrackedLinkStoreV983(GenericTrackedLinkStore):
    """v9.8.3 extension: generic tracked links may target an exact Stored File incarnation."""

    def __init__(
        self,
        analytics: Any,
        *,
        file_store_provider: Callable[[], FileStore],
    ) -> None:
        self._file_store_provider = file_store_provider
        super().__init__(analytics)

    def _file_store(self) -> FileStore:
        return self._file_store_provider()

    def _init_schema(self) -> None:
        super()._init_schema()
        additions = {
            "target_type": "TEXT NOT NULL DEFAULT 'url'",
            "stored_file_id": "TEXT",
            "stored_file_sha256": "TEXT NOT NULL DEFAULT ''",
            "stored_file_created_at": "TEXT NOT NULL DEFAULT ''",
            "download_filename": "TEXT NOT NULL DEFAULT ''",
            "download_media_type": "TEXT NOT NULL DEFAULT ''",
            "stored_file_size_bytes": "INTEGER",
        }
        with self._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(generic_tracking_links)"
                ).fetchall()
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE generic_tracking_links ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_generic_tracking_links_target_type "
                "ON generic_tracking_links(target_type, created_at)"
            )

    @staticmethod
    def _clean_file_id(file_id: str) -> str:
        value = str(file_id or "").strip()
        if (
            not value
            or len(value) > 255
            or value in {".", ".."}
            or any(ch in value for ch in "/\\?#\x00\r\n")
        ):
            raise GenericTrackedLinkError("file_id is invalid")
        return value

    def _resolve_file_for_scope(
        self,
        *,
        owner_id: str,
        project_id: str | None,
        file_id: str,
    ) -> dict[str, Any]:
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        fid = self._clean_file_id(file_id)
        try:
            info = self._file_store().get_info(fid)
        except FileStoreError as exc:
            raise GenericTrackedLinkError("Stored File not found") from exc

        if str(info.get("owner_id") or "") != owner:
            raise GenericTrackedLinkError(
                "Stored File not found in the requested owner scope"
            )

        file_project = (
            str(info.get("project_id")).strip()
            if info.get("project_id")
            else None
        )
        # Project-scoped tools may use a project-local file or an owner-global file, matching
        # the established FileStore include_global project semantics. They must never cross into
        # another project.
        if project is not None and file_project not in {None, project}:
            raise GenericTrackedLinkError(
                "Stored File not found in the requested project scope"
            )
        return info

    def _safe_link(
        self,
        row: dict[str, Any],
        *,
        aggregate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = super()._safe_link(row, aggregate=aggregate)
        target_type = str(row.get("target_type") or "url")
        result["target_type"] = target_type
        if target_type == "stored_file":
            result["file_id"] = str(row.get("stored_file_id") or "")
            result["filename"] = str(row.get("download_filename") or "")
            result["media_type"] = str(row.get("download_media_type") or "")
            result["size_bytes"] = int(row.get("stored_file_size_bytes") or 0)
            # The exact-incarnation SHA/created_at are server-side enforcement metadata and are
            # intentionally not needed by the MCP client.
            result.pop("destination_url", None)
            result.pop("normalized_url", None)
            result.pop("destination_host", None)
        return result

    def create_file(
        self,
        *,
        owner_id: str,
        file_id: str,
        label: str,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        owner = self._clean_owner(owner_id)
        project = self._clean_project(project_id)
        clean_label = self._clean_label(label)
        idem = self._clean_idempotency_key(idempotency_key)
        info = self._resolve_file_for_scope(
            owner_id=owner,
            project_id=project,
            file_id=file_id,
        )
        fid = str(info["id"])
        sha256 = str(info.get("sha256") or "")
        created_at = str(info.get("created_at") or "")
        filename = str(info.get("filename") or "")
        media_type = str(info.get("media_type") or "application/octet-stream")
        size_bytes = int(info.get("size_bytes") or 0)
        if not sha256 or not created_at:
            raise GenericTrackedLinkError(
                "Stored File is missing exact-incarnation metadata"
            )

        if idem:
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
                    str(row.get("target_type") or "url") != "stored_file"
                    or str(row.get("stored_file_id") or "") != fid
                    or str(row.get("stored_file_sha256") or "") != sha256
                    or str(row.get("stored_file_created_at") or "") != created_at
                    or str(row.get("label") or "") != clean_label
                ):
                    raise GenericTrackedLinkError(
                        "idempotency_key is already bound to a different tracked-link intent"
                    )
                out = self._safe_link(
                    row,
                    aggregate=self._aggregate(str(row["id"])),
                )
                out["ok"] = True
                out["idempotent_replay"] = True
                return out

        now = _now()
        last_error: Exception | None = None
        for _ in range(4):
            tracked_link_id = f"gtl_{_token(12)}"
            token = "glnk_" + _token(24)
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO generic_tracking_links(
                            id,tracking_token,owner_id,project_id,label,destination_url,
                            normalized_url,destination_host,status,created_at,updated_at,
                            revoked_at,idempotency_key,target_type,stored_file_id,
                            stored_file_sha256,stored_file_created_at,download_filename,
                            download_media_type,stored_file_size_bytes
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            tracked_link_id,
                            token,
                            owner,
                            project,
                            clean_label,
                            "",
                            "",
                            "",
                            "active",
                            now,
                            now,
                            None,
                            idem,
                            "stored_file",
                            fid,
                            sha256,
                            created_at,
                            filename,
                            media_type,
                            size_bytes,
                        ),
                    )
                row = self._row_by_id(
                    tracked_link_id,
                    owner_id=owner,
                    project_id=project,
                )
                out = self._safe_link(
                    row,
                    aggregate=self._aggregate(tracked_link_id),
                )
                out["ok"] = True
                out["idempotent_replay"] = False
                return out
            except sqlite3.IntegrityError as exc:
                last_error = exc
                if idem:
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
                            str(row.get("target_type") or "url") != "stored_file"
                            or str(row.get("stored_file_id") or "") != fid
                            or str(row.get("stored_file_sha256") or "") != sha256
                            or str(row.get("stored_file_created_at") or "") != created_at
                            or str(row.get("label") or "") != clean_label
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
            "Unable to allocate a unique tracked file link: "
            + (type(last_error).__name__ if last_error else "unknown")
        )

    def resolve_file_target(self, link: dict[str, Any]) -> dict[str, Any]:
        if str(link.get("target_type") or "url") != "stored_file":
            raise GenericTrackedLinkError("Tracked link is not a Stored File target")
        file_id = str(link.get("stored_file_id") or "").strip()
        expected_sha = str(link.get("stored_file_sha256") or "")
        expected_created = str(link.get("stored_file_created_at") or "")
        if not file_id or not expected_sha or not expected_created:
            raise GenericTrackedLinkError(
                "Tracked Stored File target is missing exact-incarnation metadata"
            )
        try:
            info = self._file_store().get_info(file_id)
        except FileStoreError as exc:
            raise GenericTrackedLinkError(
                "Tracked Stored File target is unavailable"
            ) from exc
        if (
            str(info.get("sha256") or "") != expected_sha
            or str(info.get("created_at") or "") != expected_created
        ):
            raise GenericTrackedLinkError(
                "Tracked Stored File target no longer matches its original incarnation"
            )
        return info

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
                       l.label,l.target_type,l.destination_url,l.destination_host,
                       l.stored_file_id,l.download_filename,l.download_media_type
                FROM generic_tracking_clicks c
                JOIN generic_tracking_links l ON l.id=c.tracked_link_id
                WHERE {where}
                ORDER BY c.observed_at DESC,c.id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            if str(item.get("target_type") or "url") == "stored_file":
                item["file_id"] = item.pop("stored_file_id", None)
                item["filename"] = item.pop("download_filename", "")
                item["media_type"] = item.pop("download_media_type", "")
                item.pop("destination_url", None)
                item.pop("destination_host", None)
            else:
                item.pop("stored_file_id", None)
                item.pop("download_filename", None)
                item.pop("download_media_type", None)
            events.append(item)
        return {
            "ok": True,
            "owner_id": owner,
            "project_id": project,
            "events": events,
            "count": len(events),
            "raw_ip_stored": False,
        }

    def status(self) -> dict[str, Any]:
        status = dict(super().status())
        with self._connect() as conn:
            file_links = int(
                conn.execute(
                    "SELECT COUNT(*) FROM generic_tracking_links "
                    "WHERE target_type='stored_file'"
                ).fetchone()[0]
            )
        status.update(
            {
                "tracked_file_links": file_links,
                "stored_file_targets_supported": True,
                "stored_file_exact_incarnation_enforced": True,
            }
        )
        return status


def bind_generic_tracked_link_store_v983(base: Any) -> Callable[[], GenericTrackedLinkStoreV983]:
    @lru_cache(maxsize=1)
    def _store() -> GenericTrackedLinkStoreV983:
        return GenericTrackedLinkStoreV983(
            base.analytics_store(),
            file_store_provider=base.file_store,
        )
    return _store


__all__ = [
    "GenericTrackedLinkStoreV983",
    "bind_generic_tracked_link_store_v983",
]
