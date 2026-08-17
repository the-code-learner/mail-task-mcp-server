from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path


class V91ServerToolTests(unittest.TestCase):
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
        "FILE_STORE_DB_PATH",
        "FILE_STORE_ROOT",
        "FILE_STORE_MAX_BYTES",
        "FILE_STORE_MAX_TOTAL_BYTES",
        "FILE_STORE_MAX_FILES",
        "FILE_STORE_TEXT_MAX_CHARS",
        "BRIDGE_BUILD",
        "POSTMASTER_REF",
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = {key: os.environ.get(key) for key in self.ENV_KEYS}
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
                "FILE_STORE_DB_PATH": str(root / "files.db"),
                "FILE_STORE_ROOT": str(root / "files"),
                "FILE_STORE_MAX_BYTES": "4096",
                "FILE_STORE_MAX_TOTAL_BYTES": "16384",
                "FILE_STORE_MAX_FILES": "20",
                "FILE_STORE_TEXT_MAX_CHARS": "1000",
                "POSTMASTER_REF": "ci-v9.1-ref",
            }
        )
        os.environ.pop("BRIDGE_BUILD", None)

        import postmaster.server as server

        self.s = server
        server.scheduler.cache_clear()
        server.context_engine.cache_clear()
        server.file_store.cache_clear()

    def tearDown(self) -> None:
        self.s.scheduler.cache_clear()
        self.s.context_engine.cache_clear()
        self.s.file_store.cache_clear()
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_file_tools_and_build_identity(self) -> None:
        s = self.s
        self.assertEqual(s.build_status()["build"], "ci-v9.1-ref")
        self.assertTrue(s.build_status()["small_file_store"])

        self.assertTrue(s.create_owner("owner", "Owner")["ok"])
        self.assertTrue(s.create_project("owner", "project", "Project")["ok"])

        invalid = s.save_text_file(
            owner_id="owner", project_id="missing", filename="bad.txt", content="no"
        )
        self.assertFalse(invalid["ok"])

        text = s.save_text_file(
            owner_id="owner",
            project_id="project",
            filename="notes.md",
            content="# hello\nsmall persistent file",
            media_type="text/markdown",
            description="integration",
            tags=["v9.1", "repo"],
        )
        self.assertEqual(text["filename"], "notes.md")
        fid = text["id"]

        binary = s.save_file(
            owner_id="owner",
            project_id="project",
            filename="tiny.bin",
            content_base64=base64.b64encode(b"\x00\x01\x02").decode("ascii"),
            tags=["v9.1"],
        )
        self.assertEqual(binary["size_bytes"], 3)

        listed = s.list_files(owner_id="owner", project_id="project", tag="v9.1")
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["count"], 2)

        info = s.get_file_info(fid)
        self.assertEqual(info["filename"], "notes.md")
        read = s.read_text_file(fid)
        self.assertIn("small persistent file", read["text"])
        encoded = s.get_file_base64(binary["id"])
        self.assertEqual(base64.b64decode(encoded["content_base64"]), b"\x00\x01\x02")

        updated = s.update_file_metadata(fid, filename="renamed.md", tags=["docs"])
        self.assertEqual(updated["filename"], "renamed.md")
        self.assertEqual(updated["tags"], ["docs"])

        status = s.file_store_status()
        self.assertEqual(status["files"], 2)
        self.assertTrue(s.delete_stored_file(fid)["ok"])
        self.assertTrue(s.delete_stored_file(binary["id"])["ok"])
        self.assertEqual(s.file_store_status()["files"], 0)

    def test_multiple_memories_with_same_tag_are_all_listed(self) -> None:
        s = self.s
        self.assertTrue(s.create_owner("owner", "Owner")["ok"])
        self.assertTrue(s.create_project("owner", "project", "Project")["ok"])
        first = s.create_memory(
            owner_id="owner", project_id="project", title="one", content="first", tags=["same-tag"]
        )
        second = s.create_memory(
            owner_id="owner", project_id="project", title="two", content="second", tags=["same-tag"]
        )
        rows = s.list_memories(owner_id="owner", project_id="project", tag="same-tag")
        self.assertEqual({row["id"] for row in rows}, {first["id"], second["id"]})
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
