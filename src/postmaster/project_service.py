from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from .runtime_v960_knowledge import knowledge_scope_store


class ProjectServiceError(RuntimeError):
    pass


class ProjectDeletionBlocked(ProjectServiceError):
    pass


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_project_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    return text[:64].rstrip("-")


def validate_project_slug(value: str) -> str:
    slug = str(value or "").strip()
    if not slug:
        raise ProjectServiceError("Project ID / slug is required")
    if not _SLUG_RE.fullmatch(slug) or slug.endswith("-"):
        raise ProjectServiceError(
            "Project ID / slug must be 1-64 lowercase letters, numbers or hyphens, "
            "start and end with a letter or number"
        )
    return slug


class ProjectService:
    """Shared project-domain operations used by the WebGUI without adding MCP tools."""

    def __init__(self, scheduler: Any, file_store: Any) -> None:
        self.scheduler = scheduler
        self.file_store = file_store
        self.scope_store = knowledge_scope_store()

    def list_active(self) -> list[dict[str, Any]]:
        rows = self.scheduler.list_projects()
        return [dict(row) for row in rows if isinstance(row, dict) and bool(row.get("active", True))]

    def get(self, project_id: str, *, active_only: bool = True) -> dict[str, Any]:
        pid = str(project_id or "").strip()
        with self.scheduler._connect() as conn:
            row = conn.execute(
                "SELECT id,owner_id,name,description,active,created_at FROM projects WHERE id=?",
                (pid,),
            ).fetchone()
        if not row or (active_only and not bool(row["active"])):
            raise ProjectServiceError(f"Unknown/disabled project: {pid}")
        return dict(row)

    def create(
        self,
        *,
        owner_id: str,
        project_id: str,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        slug = validate_project_slug(project_id)
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ProjectServiceError("Project name is required")
        result = self.scheduler.create_project(
            owner_id=str(owner_id or "").strip(),
            project_id=slug,
            name=clean_name,
            description=str(description or "").strip(),
        )
        return dict(result)

    def update(self, *, project_id: str, name: str, description: str = "") -> dict[str, Any]:
        pid = str(project_id or "").strip()
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ProjectServiceError("Project name is required")
        with self.scheduler._lock, self.scheduler._connect() as conn:
            row = conn.execute(
                "SELECT id,owner_id,active FROM projects WHERE id=?", (pid,)
            ).fetchone()
            if not row or not bool(row["active"]):
                raise ProjectServiceError(f"Unknown/disabled project: {pid}")
            conn.execute(
                "UPDATE projects SET name=?, description=? WHERE id=?",
                (clean_name, str(description or "").strip(), pid),
            )
        return {
            "ok": True,
            "project_id": pid,
            "owner_id": str(row["owner_id"]),
            "name": clean_name,
            "description": str(description or "").strip(),
        }

    def _scheduler_reference_counts(self, project_id: str, conn: Any | None = None) -> dict[str, int]:
        owns_connection = conn is None
        connection = conn or self.scheduler._connect()
        try:
            jobs = int(connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)
            ).fetchone()[0])
            profiles = int(connection.execute(
                "SELECT COUNT(*) FROM execution_profiles WHERE project_id=?", (project_id,)
            ).fetchone()[0])
            return {"jobs": jobs, "execution_profiles": profiles}
        finally:
            if owns_connection:
                connection.close()

    def _knowledge_impact(self, project_id: str) -> dict[str, int]:
        with self.scope_store._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.kind, COUNT(DISTINCT i.id) AS n
                FROM knowledge_items i
                LEFT JOIN knowledge_item_scopes s ON s.item_id=i.id
                WHERE s.project_id=? OR COALESCE(i.project_id,'')=?
                GROUP BY i.kind
                """,
                (project_id, project_id),
            ).fetchall()
        counts = {"memory": 0, "skill": 0}
        for row in rows:
            kind = str(row["kind"])
            if kind in counts:
                counts[kind] = int(row["n"])
        return {"memories": counts["memory"], "skills": counts["skill"]}

    def _file_impact(self, project_id: str) -> int:
        with self.file_store._conn() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM stored_files WHERE project_id=?", (project_id,)
            ).fetchone()[0])

    def impact(self, project_id: str) -> dict[str, Any]:
        project = self.get(project_id)
        scheduler_refs = self._scheduler_reference_counts(str(project["id"]))
        knowledge = self._knowledge_impact(str(project["id"]))
        files = self._file_impact(str(project["id"]))
        blocked = scheduler_refs["jobs"] > 0 or scheduler_refs["execution_profiles"] > 0
        return {
            "project": project,
            **knowledge,
            "files": files,
            **scheduler_refs,
            "blocked": blocked,
            "blocked_reason": (
                "Delete is blocked while scheduler jobs or execution profiles still reference "
                "this stable project ID. Remove or re-home those references first."
                if blocked else ""
            ),
        }

    def _detach_knowledge(self, project_id: str) -> dict[str, int]:
        counts = {"memories": 0, "skills": 0, "unassigned": 0, "kept_scoped": 0}
        with self.scope_store._lock, self.scope_store._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT i.id, i.kind
                FROM knowledge_items i
                LEFT JOIN knowledge_item_scopes s ON s.item_id=i.id
                WHERE s.project_id=? OR COALESCE(i.project_id,'')=?
                ORDER BY i.id
                """,
                (project_id, project_id),
            ).fetchall()
            for item in rows:
                item_id = str(item["id"])
                kind = str(item["kind"])
                if kind == "memory":
                    counts["memories"] += 1
                elif kind == "skill":
                    counts["skills"] += 1

                conn.execute(
                    "DELETE FROM knowledge_item_scopes WHERE item_id=? AND project_id=?",
                    (item_id, project_id),
                )
                remaining = conn.execute(
                    """
                    SELECT owner_id,project_id,is_primary
                    FROM knowledge_item_scopes WHERE item_id=?
                    ORDER BY is_primary DESC, owner_id, project_id
                    """,
                    (item_id,),
                ).fetchall()
                if remaining:
                    primary = next((row for row in remaining if bool(row["is_primary"])), remaining[0])
                    conn.execute(
                        "UPDATE knowledge_item_scopes SET is_primary=0 WHERE item_id=?", (item_id,)
                    )
                    conn.execute(
                        """
                        UPDATE knowledge_item_scopes SET is_primary=1
                        WHERE item_id=? AND owner_id=? AND project_id=?
                        """,
                        (item_id, str(primary["owner_id"]), str(primary["project_id"])),
                    )
                    conn.execute(
                        "UPDATE knowledge_items SET owner_id=?, project_id=? WHERE id=?",
                        (
                            str(primary["owner_id"]),
                            str(primary["project_id"]) or None,
                            item_id,
                        ),
                    )
                    counts["kept_scoped"] += 1
                    action = "project_detach_kept_scopes"
                    details = {
                        "deleted_project_id": project_id,
                        "remaining_scopes": [
                            {
                                "owner_id": str(row["owner_id"]),
                                "project_id": str(row["project_id"]) or None,
                            }
                            for row in remaining
                        ],
                    }
                else:
                    # No scope row means Unassigned. In particular, do not create project_id=''
                    # here because that row is the explicit Global scope representation.
                    conn.execute(
                        "UPDATE knowledge_items SET project_id=NULL WHERE id=?", (item_id,)
                    )
                    counts["unassigned"] += 1
                    action = "project_detach_unassigned"
                    details = {
                        "deleted_project_id": project_id,
                        "semantic_state": "unassigned",
                        "global": False,
                    }
                conn.execute(
                    """
                    INSERT INTO knowledge_scope_audit(item_id,action,details_json,created_at)
                    VALUES(?,?,?,?)
                    """,
                    (item_id, action, json.dumps(details, ensure_ascii=False, sort_keys=True), _now()),
                )
            conn.commit()
        return counts

    def _detach_files(self, project_id: str) -> int:
        with self.file_store._lock, self.file_store._conn() as conn:
            cursor = conn.execute(
                "UPDATE stored_files SET project_id=NULL, updated_at=? WHERE project_id=?",
                (_now(), project_id),
            )
            count = int(cursor.rowcount or 0)
        return count

    def delete(self, *, project_id: str, confirmation: str) -> dict[str, Any]:
        pid = str(project_id or "").strip()
        if str(confirmation or "").strip() != pid:
            raise ProjectServiceError("Type the exact project_id to confirm deletion")

        # BEGIN IMMEDIATE holds the scheduler DB write reservation through the cross-store
        # detach. A concurrent create_job/create_profile can therefore not commit a new
        # reference after this safety check but before project deactivation.
        with self.scheduler._lock:
            with self.scheduler._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                project = conn.execute(
                    "SELECT id,owner_id,name,active FROM projects WHERE id=?", (pid,)
                ).fetchone()
                if not project or not bool(project["active"]):
                    raise ProjectServiceError(f"Unknown/disabled project: {pid}")
                refs = self._scheduler_reference_counts(pid, conn)
                if refs["jobs"] or refs["execution_profiles"]:
                    raise ProjectDeletionBlocked(
                        "Delete blocked: scheduler jobs or execution profiles still reference "
                        f"project {pid!r}. No data was deleted."
                    )

                knowledge = self._detach_knowledge(pid)
                files = self._detach_files(pid)
                conn.execute("UPDATE projects SET active=0 WHERE id=?", (pid,))

        return {
            "ok": True,
            "project_id": pid,
            "active": False,
            "files_unassigned": files,
            **knowledge,
            "content_deleted": 0,
            "global_promotions": 0,
        }
