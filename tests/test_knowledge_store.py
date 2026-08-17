from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from postmaster.knowledge_store import KnowledgeStore


class KnowledgeStoreSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'knowledge.db')
        self.store = KnowledgeStore(db_path=self.db, chunk_chars=400, chunk_overlap=40)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_memory_crud_fts_history_and_export(self) -> None:
        item = self.store.create_item(
            kind='memory',
            owner_id='owner-test',
            project_id='project-test',
            title='Persistent project memory',
            content='Remember the deployment architecture and semantic retrieval decisions.',
            priority=0.8,
            tags=['architecture', 'context'],
            actor='ci',
        )
        self.assertEqual(item['revision'], 1)
        self.assertGreaterEqual(item['chunk_count'], 1)

        hits = self.store.lexical_search(
            'semantic retrieval', owner_id='owner-test', project_id='project-test', limit=10
        )
        self.assertTrue(any(row['item_id'] == item['id'] for row in hits))

        updated = self.store.update_item(
            item['id'], content='Remember the updated deployment architecture and hybrid retrieval decisions.', actor='ci'
        )
        self.assertEqual(updated['revision'], 2)
        revisions = self.store.revisions(item['id'])
        self.assertEqual([r['revision'] for r in revisions[:2]], [2, 1])

        bundle = self.store.export_bundle(owner_id='owner-test', project_id='project-test')
        self.assertEqual(len(bundle['items']), 1)
        self.assertEqual(bundle['items'][0]['id'], item['id'])


if __name__ == '__main__':
    unittest.main()
