from __future__ import annotations

import copy
import os
import tempfile
import unittest
import uuid
from pathlib import Path


class V9KnowledgeMCPIntegrationTests(unittest.TestCase):
    """Exercise the public v9 knowledge MCP wrappers against isolated SQLite stores."""

    ENV_KEYS = (
        "SCHEDULER_DB_PATH",
        "DEFAULT_OWNER_ID",
        "DEFAULT_OWNER_NAME",
        "SEED_TINKERER_PROJECT",
        "SEED_TINKERER_PROFILE",
        "CONTEXT_DB_PATH",
        "CONTEXT_SEMANTIC_ENABLED",
        "CONTEXT_MODEL_AUTO_DOWNLOAD",
        "CONTEXT_MODEL_AUTO_PREPARE",
        "MAIL_ACCOUNTS_DB_PATH",
        "MAIL_ACCOUNTS_KEY_PATH",
        "EMAIL_ANALYTICS_DB_PATH",
        "EMAIL_ANALYTICS_KEY_PATH",
        "RECIPIENT_POLICY_DB_PATH",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._old_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ.update(
            {
                "SCHEDULER_DB_PATH": str(root / "scheduler.db"),
                "DEFAULT_OWNER_ID": "ci-default",
                "DEFAULT_OWNER_NAME": "CI Default",
                "SEED_TINKERER_PROJECT": "false",
                "SEED_TINKERER_PROFILE": "false",
                "CONTEXT_DB_PATH": str(root / "knowledge.db"),
                "CONTEXT_SEMANTIC_ENABLED": "false",
                "CONTEXT_MODEL_AUTO_DOWNLOAD": "false",
                "CONTEXT_MODEL_AUTO_PREPARE": "false",
                "MAIL_ACCOUNTS_DB_PATH": str(root / "mail-accounts.db"),
                "MAIL_ACCOUNTS_KEY_PATH": str(root / "mail-accounts.key"),
                "EMAIL_ANALYTICS_DB_PATH": str(root / "analytics.db"),
                "EMAIL_ANALYTICS_KEY_PATH": str(root / "analytics.key"),
                "RECIPIENT_POLICY_DB_PATH": str(root / "recipient-policy.db"),
            }
        )

        import postmaster.server as server

        self.server = server
        server.scheduler.cache_clear()
        server.context_engine.cache_clear()
        server.account_store.cache_clear()
        server.policy_client.cache_clear()

    def tearDown(self) -> None:
        self.server.scheduler.cache_clear()
        self.server.context_engine.cache_clear()
        self.server.account_store.cache_clear()
        self.server.policy_client.cache_clear()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_all_v9_knowledge_mcp_operations(self) -> None:
        s = self.server
        owner_id = "ci-owner"
        project_id = "repo-dev"
        import_project_id = "repo-import"

        owner = s.create_owner(owner_id, "CI Owner")
        self.assertTrue(owner["ok"], owner)
        self.assertTrue(s.create_project(owner_id, project_id, "Repository development")["ok"])
        self.assertTrue(s.create_project(owner_id, import_project_id, "Imported repository memory")["ok"])

        invalid = s.create_memory(
            owner_id=owner_id,
            project_id="missing-project",
            title="Must fail",
            content="Unknown scheduler scopes must never be accepted.",
        )
        self.assertFalse(invalid["ok"])

        original_memory_content = (
            "The repository uses a multi-file Python runtime fetched by a single Portainer YAML bootstrap. "
            "Persistent data, code cache and the virtual environment live in Docker volumes."
        )
        memory = s.create_memory(
            owner_id=owner_id,
            project_id=project_id,
            title="Repository deployment architecture",
            content=original_memory_content,
            priority=0.92,
            always_include=False,
            tags=["repository", "deployment", "portainer"],
            metadata={"source": "integration-test", "version": 1},
        )
        self.assertEqual(memory["kind"], "memory")
        self.assertEqual(memory["revision"], 1)
        memory_id = memory["id"]

        global_memory = s.create_memory(
            owner_id=owner_id,
            title="Global repository safety rule",
            content="Use feature branches, required CI checks and pull requests before updating the protected main branch.",
            priority=0.98,
            always_include=True,
            tags=["repository", "safety"],
        )
        self.assertIsNone(global_memory["project_id"])
        global_memory_id = global_memory["id"]

        skill = s.create_skill(
            owner_id=owner_id,
            project_id=project_id,
            title="Safe repository update workflow",
            content="Make changes on a feature branch, run v9 runtime tests, then merge through the protected pull-request path.",
            priority=0.85,
            always_include=True,
            tags=["git", "ci", "workflow"],
            metadata={"source": "integration-test"},
        )
        self.assertEqual(skill["kind"], "skill")
        skill_id = skill["id"]

        status = s.knowledge_status()
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["memories"], 2)
        self.assertEqual(status["skills"], 1)
        self.assertFalse(status["semantic"]["enabled"])

        fetched_memory = s.get_memory(memory_id)
        fetched_skill = s.get_skill(skill_id)
        self.assertEqual(fetched_memory["id"], memory_id)
        self.assertEqual(fetched_skill["id"], skill_id)

        memories = s.list_memories(owner_id=owner_id, project_id=project_id, include_global=True)
        self.assertEqual({item["id"] for item in memories}, {memory_id, global_memory_id})
        skills = s.list_skills(owner_id=owner_id, project_id=project_id, include_global=True)
        self.assertEqual([item["id"] for item in skills], [skill_id])

        lexical = s.search_knowledge(
            "Portainer bootstrap persistent volumes",
            owner_id=owner_id,
            project_id=project_id,
            kinds=["memory"],
            limit=10,
        )
        self.assertTrue(lexical["ok"], lexical)
        self.assertFalse(lexical["semantic_active"])
        self.assertEqual(lexical["results"][0]["item_id"], memory_id)

        missing_owner = s.search_knowledge("repository", project_id=project_id)
        self.assertFalse(missing_owner["ok"])

        context = s.get_project_context(
            owner_id=owner_id,
            project_id=project_id,
            query="repository deployment and pull request workflow",
            budget_chars=5000,
        )
        self.assertTrue(context["ok"], context)
        source_ids = {source["id"] for source in context["sources"]}
        self.assertIn(memory_id, source_ids)
        self.assertIn(global_memory_id, source_ids)
        self.assertIn(skill_id, source_ids)
        self.assertIn("Repository deployment architecture", context["context_text"])
        self.assertIn("Safe repository update workflow", context["context_text"])

        updated_memory = s.update_memory(
            memory_id,
            content=original_memory_content + " Source downloads are pinned to an immutable commit for private deployments.",
            tags=["repository", "deployment", "portainer", "pinning"],
            metadata={"source": "integration-test", "version": 2},
        )
        self.assertEqual(updated_memory["revision"], 2)
        self.assertIn("pinning", updated_memory["tags"])

        updated_skill = s.update_skill(
            skill_id,
            content="Create a feature branch, require green CI, review the diff, then merge through the protected PR path.",
            priority=0.9,
        )
        self.assertEqual(updated_skill["revision"], 2)

        history = s.get_knowledge_history(memory_id)
        self.assertEqual([row["revision"] for row in history[:2]], [2, 1])
        restored = s.restore_knowledge_revision(memory_id, 1)
        self.assertEqual(restored["revision"], 3)
        self.assertEqual(restored["content"], original_memory_content)

        audit = s.get_knowledge_audit(memory_id)
        self.assertGreaterEqual(len(audit), 3)
        self.assertIn("create", {row["action"] for row in audit})
        self.assertIn("update", {row["action"] for row in audit})

        exported = s.export_knowledge(owner_id=owner_id, project_id=project_id)
        self.assertEqual(exported["format"], "postmaster-knowledge-v1")
        self.assertEqual({item["kind"] for item in exported["items"]}, {"memory", "skill"})
        self.assertNotIn(global_memory_id, {item["id"] for item in exported["items"]})

        imported_bundle = copy.deepcopy(exported)
        for item in imported_bundle["items"]:
            item["id"] = str(uuid.uuid4())
        imported = s.import_knowledge(
            imported_bundle,
            owner_id_override=owner_id,
            project_id_override=import_project_id,
        )
        self.assertTrue(imported["ok"], imported)
        self.assertEqual(imported["created"], len(imported_bundle["items"]))
        imported_items = s.context_engine().store.list_items(
            owner_id=owner_id,
            project_id=import_project_id,
            include_global=False,
            limit=20,
        )
        self.assertEqual(len(imported_items), len(imported_bundle["items"]))

        # With semantics deliberately disabled this tool must fail safely while lexical search remains usable.
        reindex = s.reindex_knowledge(owner_id=owner_id, project_id=project_id, force=True)
        self.assertFalse(reindex["ok"])
        fallback_search = s.search_knowledge(
            "deployment architecture",
            owner_id=owner_id,
            project_id=project_id,
            limit=10,
        )
        self.assertTrue(fallback_search["ok"])
        self.assertTrue(fallback_search["results"])

        self.assertTrue(s.delete_memory(memory_id)["ok"])
        self.assertTrue(s.delete_memory(global_memory_id)["ok"])
        self.assertTrue(s.delete_skill(skill_id)["ok"])
        self.assertFalse(s.get_memory(memory_id)["ok"])
        self.assertFalse(s.get_skill(skill_id)["ok"])

        audit_after_delete = s.get_knowledge_audit(memory_id)
        self.assertIn("delete", {row["action"] for row in audit_after_delete})


if __name__ == "__main__":
    unittest.main()
