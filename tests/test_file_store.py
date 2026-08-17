from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from postmaster.file_store import FileStore, FileStoreError


class FileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = FileStore(
            db_path=str(root / "files.db"),
            root=str(root / "files"),
            max_bytes=2048,
            max_total_bytes=8192,
            max_files=10,
            text_max_chars=1000,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_text_binary_metadata_dedup_and_delete(self) -> None:
        first = self.store.save_text(
            owner_id="owner",
            project_id="project",
            filename="notes.txt",
            content="hello persistent file store",
            tags=["Repo", "V9.1"],
            description="first",
        )
        second = self.store.save_text(
            owner_id="owner",
            project_id="project",
            filename="notes-copy.txt",
            content="hello persistent file store",
            tags=["repo"],
        )
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(self.store.status()["unique_blobs"], 1)
        self.assertEqual(len(self.store.list_files(owner_id="owner", project_id="project", tag="repo")), 2)

        text = self.store.read_text(first["id"])
        self.assertEqual(text["text"], "hello persistent file store")
        self.assertFalse(text["truncated"])

        raw = self.store.read_base64(first["id"])
        self.assertEqual(base64.b64decode(raw["content_base64"]), b"hello persistent file store")

        updated = self.store.update_metadata(
            first["id"], filename="renamed.md", media_type="text/markdown", tags=["docs"]
        )
        self.assertEqual(updated["filename"], "renamed.md")
        self.assertEqual(updated["tags"], ["docs"])

        deleted_first = self.store.delete(first["id"])
        self.assertFalse(deleted_first["blob_deleted"])
        deleted_second = self.store.delete(second["id"])
        self.assertTrue(deleted_second["blob_deleted"])
        self.assertEqual(self.store.status()["files"], 0)

    def test_base64_and_limits(self) -> None:
        saved = self.store.save_base64(
            owner_id="owner",
            filename="tiny.bin",
            content_base64=base64.b64encode(b"\x00\x01\x02").decode("ascii"),
        )
        self.assertEqual(saved["size_bytes"], 3)
        with self.assertRaises(FileStoreError):
            self.store.save_base64(owner_id="owner", filename="bad.bin", content_base64="not base64!!!")
        with self.assertRaises(FileStoreError):
            self.store.save_bytes(owner_id="owner", filename="too-big.bin", data=b"x" * 2049)
        with self.assertRaises(FileStoreError):
            self.store.save_text(owner_id="owner", filename="../escape.txt", content="no")

    def test_text_truncation_and_binary_rejection(self) -> None:
        text = self.store.save_text(owner_id="owner", filename="long.txt", content="x" * 1500)
        row = self.store.read_text(text["id"])
        self.assertTrue(row["truncated"])
        self.assertEqual(row["returned_chars"], 1000)

        binary = self.store.save_bytes(owner_id="owner", filename="bad.bin", data=b"\xff\xfe")
        with self.assertRaises(FileStoreError):
            self.store.read_text(binary["id"])


if __name__ == "__main__":
    unittest.main()
