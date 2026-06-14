from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.models import FactType, Preview
from app.services.ingestion import IngestionService
from app.storage.sqlite_store import SQLiteStore


class TestIngestionService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        self.sqlite = SQLiteStore(self.db_path)
        self.sqlite.run_migrations()
        self.vector = MagicMock()
        self.service = IngestionService(self.sqlite, self.vector)

    def tearDown(self):
        self.sqlite.close()
        for f in os.listdir(self.tmp):
            os.unlink(os.path.join(self.tmp, f))
        os.rmdir(self.tmp)

    def _preview(self, **kw):
        return Preview(
            title=kw.pop("title", "Test"),
            fact_type=kw.pop("fact_type", FactType.decision),
            closing_period=kw.pop("period", "2026-06"),
            description=kw.pop("desc", "Descricao de teste"),
            **kw,
        )

    def test_store_and_get_preview(self):
        p = self._preview()
        self.service.store_preview(p)
        retrieved = self.service.get_preview(p.preview_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Test")

    def test_remove_preview(self):
        p = self._preview()
        self.service.store_preview(p)
        self.service.remove_preview(p.preview_id)
        self.assertIsNone(self.service.get_preview(p.preview_id))

    def test_confirm_creates_memory(self):
        p = self._preview(title="Confirmacao teste")
        self.service.store_preview(p)
        self.vector.add_memory.return_value = None
        memory = self.service.confirm(p)
        self.assertEqual(memory.title, "Confirmacao teste")
        self.assertTrue(memory.is_active)
        self.assertIsNotNone(memory.id)
        retrieved = self.sqlite.get_memory(memory.id)
        self.assertIsNotNone(retrieved)

    def test_confirm_saves_to_vector(self):
        p = self._preview()
        self.service.store_preview(p)
        self.service.confirm(p)
        self.vector.add_memory.assert_called_once()

    def test_confirm_vector_failure_rollback(self):
        p = self._preview(title="Rollback test")
        self.service.store_preview(p)
        self.vector.add_memory.side_effect = Exception("ChromaDB error")
        with self.assertRaises(Exception):
            self.service.confirm(p)
        all_memories = self.sqlite.search_memories_sql(text_query="Rollback", limit=10)
        self.assertNotIn("Rollback test", [m.title for m in all_memories])

    def test_confirm_supersedes(self):
        orig = self._preview(title="Original")
        self.service.store_preview(orig)
        orig_mem = self.service.confirm(orig)
        corr = self._preview(title="Correcao", supersedes_id=orig_mem.id, is_correction=True)
        self.service.store_preview(corr)
        self.service.confirm(corr)
        retrieved = self.sqlite.get_memory(orig_mem.id)
        self.assertFalse(retrieved.is_active)
        self.assertIsNotNone(retrieved.superseded_by)

    def test_confirm_removes_preview(self):
        p = self._preview()
        self.service.store_preview(p)
        pid = p.preview_id
        self.service.confirm(p)
        self.assertIsNone(self.service.get_preview(pid))

    def test_get_user_fallback(self):
        with patch("os.getlogin", side_effect=OSError), patch.dict(os.environ, {}, clear=True):
            user = self.service._get_user()
            self.assertEqual(user, "unknown")

    def test_get_user_from_env(self):
        with patch("os.getlogin", side_effect=OSError), patch.dict(os.environ, {"USER": "testuser"}):
            user = self.service._get_user()
            self.assertEqual(user, "testuser")

    def test_get_user_normal(self):
        with patch("os.getlogin", return_value="reallogin"):
            user = self.service._get_user()
            self.assertEqual(user, "reallogin")
