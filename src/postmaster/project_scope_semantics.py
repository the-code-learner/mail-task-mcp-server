from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .knowledge_scopes import KnowledgeScopeStore


_INSTALLED = False


def _latest_unassigned_ids(store: KnowledgeScopeStore) -> list[str]:
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT a.item_id
            FROM knowledge_scope_audit a
            JOIN (
                SELECT item_id, MAX(id) AS max_id
                FROM knowledge_scope_audit
                GROUP BY item_id
            ) latest ON latest.item_id=a.item_id AND latest.max_id=a.id
            JOIN knowledge_items i ON i.id=a.item_id
            WHERE a.action='project_detach_unassigned' AND i.project_id IS NULL
            """
        ).fetchall()
    return [str(row["item_id"]) for row in rows]


def _batched_attach_many(self: KnowledgeScopeStore, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    values = [dict(item) for item in items]
    ids = [str(item.get("id") or item.get("item_id") or "") for item in values]
    wanted = [item_id for item_id in ids if item_id]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if wanted:
        with self._connect() as conn:
            for start in range(0, len(wanted), 400):
                batch = wanted[start:start + 400]
                marks = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"""
                    SELECT item_id,owner_id,project_id,is_primary,created_at
                    FROM knowledge_item_scopes
                    WHERE item_id IN ({marks})
                    ORDER BY item_id,is_primary DESC,owner_id,project_id
                    """,
                    batch,
                ).fetchall()
                for row in rows:
                    grouped[str(row["item_id"])].append({
                        "owner_id": str(row["owner_id"]),
                        "project_id": str(row["project_id"]) or None,
                        "is_primary": bool(row["is_primary"]),
                        "created_at": str(row["created_at"]),
                    })
    for item, item_id in zip(values, ids):
        item["scopes"] = grouped.get(item_id, []) if item_id else []
    return values


def install_project_scope_semantics() -> None:
    """Install v9.6.2 scope fixes before the singleton scope store is constructed."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = KnowledgeScopeStore._init_schema

    def _init_schema_v962(self: KnowledgeScopeStore) -> None:
        original_init(self)
        # Older bootstrap behavior synthesized project_id='' (Global) for every legacy
        # NULL project. If the latest scope audit explicitly says the item became
        # Unassigned through project deletion, remove only that synthesized Global row.
        # This keeps Unassigned durable across process restarts without a DB migration.
        unassigned = _latest_unassigned_ids(self)
        if not unassigned:
            return
        with self._lock, self._connect() as conn:
            for item_id in unassigned:
                conn.execute(
                    "DELETE FROM knowledge_item_scopes WHERE item_id=? AND project_id=''",
                    (item_id,),
                )
            conn.commit()

    KnowledgeScopeStore._init_schema = _init_schema_v962
    KnowledgeScopeStore.attach_many = _batched_attach_many
