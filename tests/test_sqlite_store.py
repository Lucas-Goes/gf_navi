from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.models import FactType, Memory, Document
from app.storage.sqlite_store import SQLiteStore


class TestSQLiteStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        self.store = SQLiteStore(self.db_path)
        self.store.run_migrations()
        self.conn = self.store.connect()

    def tearDown(self):
        self.store.close()
        for f in Path(self.tmp).glob("test.db*"):
            f.unlink()
        os.rmdir(self.tmp)

    def _make_memory(self, title="Test", fact_type="decision", period="2026-06", **kw):
        return Memory(
            fact_type=FactType(fact_type),
            closing_period=period,
            title=title,
            description=kw.pop("desc", title),
            **kw,
        )

    def test_insert_and_get_memory(self):
        m = self._make_memory(title="Memoria de teste", decided_by="Joao")
        self.store.insert_memory(m)
        retrieved = self.store.get_memory(m.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Memoria de teste")
        self.assertEqual(retrieved.decided_by, "Joao")

    def test_get_memory_not_found(self):
        self.assertIsNone(self.store.get_memory("nonexistent-id"))

    def test_get_memory_by_prefix(self):
        m = self._make_memory(title="Prefixo test")
        self.store.insert_memory(m)
        prefix = m.id[:8]
        retrieved = self.store.get_memory(prefix)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.id, m.id)

    def test_get_memory_short_prefix_returns_none(self):
        m = self._make_memory(title="Short prefix")
        self.store.insert_memory(m)
        self.assertIsNone(self.store.get_memory(m.id[:4]))

    def test_search_by_text(self):
        self.store.insert_memory(self._make_memory(title="Taxas de cambio", desc="Atualizacao das taxas"))
        self.store.insert_memory(self._make_memory(title="Outro assunto", desc="Nada a ver"))
        results = self.store.search_memories_sql(text_query="taxas")
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(any("Taxas" in r.title for r in results))

    def test_search_by_fact_type(self):
        self.store.insert_memory(self._make_memory(title="Decisao A", fact_type="decision"))
        self.store.insert_memory(self._make_memory(title="Regra B", fact_type="rule_change"))
        results = self.store.search_memories_sql(fact_type="rule_change")
        for r in results:
            self.assertEqual(r.fact_type.value, "rule_change")

    def test_search_by_period(self):
        self.store.insert_memory(self._make_memory(title="Junho", period="2026-06"))
        self.store.insert_memory(self._make_memory(title="Julho", period="2026-07"))
        results = self.store.search_memories_sql(closing_period="2026-06")
        for r in results:
            self.assertEqual(r.closing_period, "2026-06")

    def test_insert_and_get_document(self):
        doc = Document(filename="test.txt", source_type="txt", title="Teste", content="conteudo")
        self.store.insert_document(doc)
        retrieved = self.store.get_document(doc.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.filename, "test.txt")

    def test_document_exists(self):
        doc = Document(filename="exists.txt", source_type="txt", title="Exists", content="x")
        self.store.insert_document(doc)
        self.assertTrue(self.store.document_exists("exists.txt"))
        self.assertFalse(self.store.document_exists("missing.txt"))

    def test_search_documents_by_filename(self):
        Document(filename="relatorio.pdf", source_type="pdf", title="Relatorio", content="dados")
        for name in ["relatorio_mensal.pdf", "relatorio_anual.pdf", "planilha.xlsx"]:
            ext = name.split(".")[-1]
            self.store.insert_document(
                Document(filename=name, source_type=ext, title=name.split(".")[0], content="x")
            )
        results = self.store.search_documents_by_filename("relatorio")
        self.assertGreaterEqual(len(results), 2)

    def test_link_and_get_memory_documents(self):
        m = self._make_memory()
        self.store.insert_memory(m)
        d = Document(filename="doc.txt", source_type="txt", title="Documento", content="x")
        self.store.insert_document(d)
        self.store.link_memory_document(m.id, d.id)
        docs = self.store.get_documents_by_memory(m.id)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].id, d.id)

    def test_update_superseded_by(self):
        m1 = self._make_memory(title="Original")
        self.store.insert_memory(m1)
        m2 = self._make_memory(title="Correcao")
        self.store.insert_memory(m2)
        self.store.update_superseded_by(m1.id, m2.id)
        updated = self.store.get_memory(m1.id)
        self.assertFalse(updated.is_active)
        self.assertEqual(updated.superseded_by, m2.id)

    def test_delete_memory_removes_links(self):
        m = self._make_memory()
        self.store.insert_memory(m)
        d = Document(filename="doc.txt", source_type="txt", title="Doc", content="x")
        self.store.insert_document(d)
        self.store.link_memory_document(m.id, d.id)
        self.store.delete_memory(m.id)
        self.assertIsNone(self.store.get_memory(m.id))
        docs = self.store.get_documents_by_memory(m.id)
        self.assertEqual(len(docs), 0)

    def test_memory_isolation(self):
        m1 = self._make_memory(title="M1")
        m2 = self._make_memory(title="M2")
        self.store.insert_memory(m1)
        self.store.insert_memory(m2)
        all_m = self.store.search_memories_sql(limit=100)
        titles = [r.title for r in all_m]
        self.assertIn("M1", titles)
        self.assertIn("M2", titles)

    def test_get_memories_by_ids(self):
        m1 = self._make_memory(title="Batch1")
        m2 = self._make_memory(title="Batch2")
        self.store.insert_memory(m1)
        self.store.insert_memory(m2)
        result = self.store.get_memories_by_ids([m1.id, m2.id])
        self.assertEqual(len(result), 2)
