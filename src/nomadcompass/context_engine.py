from __future__ import annotations

import os
from typing import Any

from .knowledge_store import KnowledgeStore, KnowledgeError
from .semantic_engine import SemanticEngine, SemanticError


class ContextEngine:
    def __init__(self):
        self.store = KnowledgeStore()
        self.semantic = SemanticEngine()
        self.semantic_weight = float(os.getenv("CONTEXT_SEMANTIC_WEIGHT", "0.60"))
        self.lexical_weight = float(os.getenv("CONTEXT_LEXICAL_WEIGHT", "0.25"))
        self.priority_weight = float(os.getenv("CONTEXT_PRIORITY_WEIGHT", "0.10"))
        self.scope_weight = float(os.getenv("CONTEXT_SCOPE_WEIGHT", "0.05"))
        self.auto_index_on_search = os.getenv("CONTEXT_AUTO_INDEX_ON_SEARCH", "true").strip().lower() in {"1","true","yes","on"}
        self.auto_index_limit = max(1, int(os.getenv("CONTEXT_AUTO_INDEX_LIMIT", "10000")))

    def warmup(self) -> dict[str, Any]:
        self.semantic.ensure_model(download_if_missing=True)
        return self.status()

    def status(self) -> dict[str, Any]:
        st = self.store.status()
        st["semantic"] = self.semantic.status()
        st["ranking"] = {
            "semantic_weight": self.semantic_weight,
            "lexical_weight": self.lexical_weight,
            "priority_weight": self.priority_weight,
            "scope_weight": self.scope_weight,
            "strategy": "rank-fusion + priority + scope",
        }
        return st

    def create(self, **kwargs) -> dict[str, Any]:
        item = self.store.create_item(**kwargs)
        self._index_item_if_loaded(item["id"])
        return self.store.get_item(item["id"])

    def update(self, item_id: str, **kwargs) -> dict[str, Any]:
        item = self.store.update_item(item_id, **kwargs)
        self._index_item_if_loaded(item_id)
        return self.store.get_item(item_id)

    def delete(self, item_id: str, actor: str = "mcp") -> dict[str, Any]:
        return self.store.delete_item(item_id, actor=actor)

    def _index_item_if_loaded(self, item_id: str) -> None:
        if self.semantic._model is not None:
            try:
                self.reindex(item_id=item_id, limit=10000)
            except Exception:
                pass

    def reindex(self, *, item_id: str | None = None, owner_id: str | None = None,
                project_id: str | None = None, force: bool = False, limit: int = 100000) -> dict[str, Any]:
        if not self.semantic.ensure_model(download_if_missing=True):
            return {"ok": False, "indexed": 0, "semantic": self.semantic.status(), "error": self.semantic.last_error}
        model_id = self.semantic.model_id
        if force and item_id:
            rows = self.store.chunks_for_embedding(item_id=item_id, limit=limit)
        else:
            rows = self.store.chunks_for_embedding(
                only_missing_for_model=None if force else model_id,
                item_id=item_id, owner_id=owner_id, project_id=project_id, limit=limit,
            )
        if not rows:
            return {"ok": True, "indexed": 0, "model_id": model_id, "remaining": self.store.status().get("missing_embeddings", 0)}

        batch_size = 512
        indexed = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            vectors = self.semantic.encode([str(r["content"]) for r in batch])
            for row, vector in zip(batch, vectors):
                blob, dims = self.semantic.vector_bytes(vector)
                self.store.save_embedding(str(row["chunk_id"]), blob, dims, model_id)
                indexed += 1
        return {"ok": True, "indexed": indexed, "model_id": model_id, "remaining": self.store.status().get("missing_embeddings", 0)}

    @staticmethod
    def _semantic_scope_ok(row: dict[str, Any], owner_id: str | None, project_id: str | None, include_global: bool,
                           kinds: list[str] | None) -> bool:
        if owner_id and row.get("owner_id") != owner_id:
            return False
        if project_id is not None:
            if include_global:
                if row.get("project_id") not in {None, project_id}:
                    return False
            elif row.get("project_id") != project_id:
                return False
        if kinds and row.get("kind") not in set(kinds):
            return False
        return True

    def _semantic_results(self, query: str, *, owner_id: str | None, project_id: str | None,
                          include_global: bool, kinds: list[str] | None, limit: int) -> tuple[list[dict[str, Any]], bool]:
        if not query.strip() or not self.semantic.enabled:
            return [], False
        if not self.semantic.ensure_model(download_if_missing=True):
            return [], False
        if self.auto_index_on_search:
            try:
                self.reindex(owner_id=owner_id, project_id=project_id, force=False, limit=self.auto_index_limit)
            except Exception:
                pass
        qvec = self.semantic.encode([query])[0]
        rows = self.store.chunks_for_embedding(owner_id=owner_id, project_id=project_id, limit=max(self.auto_index_limit, 10000))
        scored: list[dict[str, Any]] = []
        for row in rows:
            if not self._semantic_scope_ok(row, owner_id, project_id, include_global, kinds):
                continue
            blob = row.get("embedding")
            dims = row.get("embedding_dims")
            if not blob or not dims or row.get("embedding_model") != self.semantic.model_id:
                continue
            score = self.semantic.cosine(qvec, blob, int(dims))
            d = dict(row)
            d["semantic_raw"] = score
            scored.append(d)
        scored.sort(key=lambda x: float(x.get("semantic_raw", -1.0)), reverse=True)
        out: list[dict[str, Any]] = []
        seen_items: set[str] = set()
        for row in scored:
            iid = str(row["item_id"])
            if iid in seen_items:
                continue
            seen_items.add(iid)
            row["semantic_rank"] = len(out) + 1
            out.append(row)
            if len(out) >= limit:
                break
        return out, True

    def search(self, query: str, *, owner_id: str | None = None, project_id: str | None = None,
               include_global: bool = True, kinds: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        query = (query or "").strip()
        limit = max(1, min(int(limit), 100))
        kinds_clean = [str(k).strip().lower() for k in (kinds or []) if str(k).strip()]
        lexical_rows = self.store.lexical_search(
            query, owner_id=owner_id, project_id=project_id, include_global=include_global,
            kinds=kinds_clean or None, limit=max(limit * 4, 40),
        ) if query else []

        lexical: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in lexical_rows:
            iid = str(row["item_id"])
            if iid in seen:
                continue
            seen.add(iid)
            row = dict(row)
            row["lexical_rank"] = len(lexical) + 1
            lexical.append(row)

        semantic, semantic_active = self._semantic_results(
            query, owner_id=owner_id, project_id=project_id, include_global=include_global,
            kinds=kinds_clean or None, limit=max(limit * 4, 40),
        )

        candidates: dict[str, dict[str, Any]] = {}
        for row in lexical:
            iid = str(row["item_id"])
            c = candidates.setdefault(iid, {"item_id": iid})
            c.update({k: row.get(k) for k in ["owner_id","project_id","kind","title","content","priority","always_include","updated_at","tags"]})
            c["lexical_rank"] = int(row["lexical_rank"])
            c["best_chunk"] = row.get("chunk_content")
        for row in semantic:
            iid = str(row["item_id"])
            c = candidates.setdefault(iid, {"item_id": iid})
            if "title" not in c:
                item = self.store.get_item(iid)
                c.update({k: item.get(k) for k in ["owner_id","project_id","kind","title","content","priority","always_include","updated_at","tags"]})
            c["semantic_rank"] = int(row["semantic_rank"])
            c["semantic_similarity"] = float(row.get("semantic_raw", 0.0))
            c.setdefault("best_chunk", row.get("content"))

        if not query:
            items = self.store.list_items(kind=kinds_clean[0] if len(kinds_clean) == 1 else None,
                                          owner_id=owner_id, project_id=project_id, include_global=include_global,
                                          enabled_only=True, limit=limit)
            return {"ok": True, "query": query, "semantic_active": False, "results": [dict(x, score=float(x.get("priority", 0.5))) for x in items]}

        sem_w = self.semantic_weight
        lex_w = self.lexical_weight
        if semantic_active and not lexical:
            sem_w += lex_w; lex_w = 0.0
        elif lexical and not semantic_active:
            lex_w += sem_w; sem_w = 0.0

        ranked: list[dict[str, Any]] = []
        for c in candidates.values():
            lexical_component = (1.0 / float(c["lexical_rank"])) if c.get("lexical_rank") else 0.0
            semantic_component = (1.0 / float(c["semantic_rank"])) if c.get("semantic_rank") else 0.0
            priority = max(0.0, min(1.0, float(c.get("priority") or 0.0)))
            if project_id is None:
                scope = 1.0 if c.get("project_id") is None else 0.0
            else:
                scope = 1.0 if c.get("project_id") == project_id else (0.5 if c.get("project_id") is None else 0.0)
            score = sem_w * semantic_component + lex_w * lexical_component + self.priority_weight * priority + self.scope_weight * scope
            c["score"] = round(score, 8)
            c["score_components"] = {
                "semantic_rank": c.get("semantic_rank"), "lexical_rank": c.get("lexical_rank"),
                "priority": priority, "scope": scope,
            }
            ranked.append(c)
        ranked.sort(key=lambda x: (float(x.get("score", 0.0)), float(x.get("priority", 0.0))), reverse=True)
        return {"ok": True, "query": query, "semantic_active": semantic_active, "results": ranked[:limit]}

    def project_context(self, *, owner_id: str, project_id: str | None, query: str = "",
                        budget_chars: int = 12000, kinds: list[str] | None = None, limit: int = 40) -> dict[str, Any]:
        budget = max(1000, min(int(budget_chars), 200000))
        kinds_clean = [str(k).strip().lower() for k in (kinds or ["memory", "skill"])]
        always = self.store.always_items(owner_id=owner_id, project_id=project_id, kinds=kinds_clean, limit=200)
        relevant = self.search(query, owner_id=owner_id, project_id=project_id, include_global=True,
                               kinds=kinds_clean, limit=max(1, min(int(limit), 100)))["results"] if query.strip() else self.store.list_items(
                                   owner_id=owner_id, project_id=project_id, include_global=True, enabled_only=True, limit=max(1, min(int(limit), 100)))
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in always + relevant:
            iid = str(item.get("id") or item.get("item_id"))
            if not iid or iid in seen:
                continue
            seen.add(iid)
            full = self.store.get_item(iid)
            full["search_score"] = item.get("score")
            ordered.append(full)

        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        used = 0
        for item in ordered:
            scope = item.get("project_id") or "global"
            tags = ", ".join(item.get("tags") or [])
            header = f"## {str(item.get('kind','')).upper()}: {item.get('title','')}\n[scope={scope}; priority={item.get('priority',0.5):.2f}; revision={item.get('revision',1)}"
            if tags:
                header += f"; tags={tags}"
            header += "]\n"
            text = header + str(item.get("content") or "").strip() + "\n"
            if used + len(text) > budget:
                remaining = budget - used
                if remaining < 240:
                    break
                text = text[:remaining - 24].rstrip() + "\n[…context truncated…]\n"
            parts.append(text)
            used += len(text)
            sources.append({"id": item["id"], "kind": item["kind"], "title": item["title"], "project_id": item.get("project_id"), "revision": item["revision"]})
            if used >= budget:
                break
        return {
            "ok": True, "owner_id": owner_id, "project_id": project_id, "query": query,
            "budget_chars": budget, "used_chars": used, "item_count": len(sources),
            "context_text": "\n".join(parts), "sources": sources,
            "semantic_active": bool(self.semantic._model is not None),
        }
