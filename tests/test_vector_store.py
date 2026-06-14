from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.storage.vector_store import VectorStore


class TestVectorStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.chroma_path = os.path.join(self.tmp, "chroma")
        self.model_name = "paraphrase-multilingual-MiniLM-L12-v2"
        self.store = VectorStore(self.chroma_path, self.model_name)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_embed_text_returns_list(self):
        emb = self.store.embed_text("teste de embedding")
        self.assertIsInstance(emb, list)
        self.assertGreater(len(emb), 0)
        self.assertIsInstance(emb[0], float)

    def test_add_and_search_memory(self):
        self.store.add_memory(
            memory_id="test-id-1",
            title="Taxas de cambio",
            description="Atualizacao das taxas de cambio para julho",
            metadata={"memory_id": "test-id-1", "fact_type": "decision"},
        )
        results = self.store.search_memories("taxas de cambio", top_k=5)
        self.assertGreaterEqual(len(results), 1)
        ids = [r[0] for r in results]
        self.assertIn("test-id-1", ids)

    def test_delete_memory_does_not_raise(self):
        self.store.add_memory(
            memory_id="del-test",
            title="Para deletar",
            description="teste",
            metadata={"memory_id": "del-test", "fact_type": "other"},
        )
        self.store.delete_memory("del-test")
        results = self.store.search_memories("deletar", top_k=5)
        ids = [r[0] for r in results]
        self.assertNotIn("del-test", ids)

    def test_add_and_search_documents(self):
        self.store.add_document(
            doc_id="doc-1",
            title="Relatorio",
            content="Relatorio mensal de fechamento de junho",
            metadata={"document_id": "doc-1", "title": "Relatorio"},
        )
        results = self.store.search_documents("relatorio mensal", top_k=5)
        self.assertGreaterEqual(len(results), 1)
        ids = [r[0] for r in results]
        self.assertIn("doc-1", ids)

    def test_delete_document_does_not_raise(self):
        self.store.add_document(
            doc_id="doc-del",
            title="Delete",
            content="conteudo para deletar",
            metadata={"document_id": "doc-del"},
        )
        self.store.delete_document("doc-del")
        results = self.store.search_documents("deletar", top_k=5)
        ids = [r[0] for r in results]
        self.assertNotIn("doc-del", ids)

    def test_hybrid_search_returns_both(self):
        self.store.add_memory(
            memory_id="hyb-mem",
            title="Memoria hibrida",
            description="teste de busca hibrida",
            metadata={"memory_id": "hyb-mem", "fact_type": "decision"},
        )
        self.store.add_document(
            doc_id="hyb-doc",
            title="Documento hibrido",
            content="teste de busca hibrida em documentos",
            metadata={"document_id": "hyb-doc"},
        )
        memories, docs = self.store.hybrid_search("busca hibrida", top_k_memories=5, top_k_docs=5)
        mem_ids = [r[0] for r in memories]
        doc_ids = [r[0] for r in docs]
        self.assertIn("hyb-mem", mem_ids)
        self.assertIn("hyb-doc", doc_ids)

    def test_update_memory_replaces(self):
        self.store.add_memory(
            memory_id="upd-test",
            title="Versao 1",
            description="descricao original",
            metadata={"memory_id": "upd-test", "fact_type": "rule_change"},
        )
        self.store.update_memory(
            memory_id="upd-test",
            title="Versao 2",
            description="descricao atualizada e diferente",
            metadata={"memory_id": "upd-test", "fact_type": "rule_change"},
        )
        results = self.store.search_memories("descricao atualizada e diferente", top_k=5)
        ids = [r[0] for r in results]
        self.assertIn("upd-test", ids)

    def test_embed_texts_batch(self):
        texts = ["texto um", "texto dois", "texto tres"]
        embs = self.store.embed_texts(texts)
        self.assertEqual(len(embs), 3)
        for emb in embs:
            self.assertIsInstance(emb, list)
            self.assertGreater(len(emb), 0)

    def test_search_returns_scores(self):
        self.store.add_memory(
            memory_id="score-test",
            title="Teste unico",
            description="conteudo muito especifico e unico para busca",
            metadata={"memory_id": "score-test"},
        )
        results = self.store.search_memories("conteudo muito especifico e unico para busca", top_k=5)
        self.assertGreaterEqual(len(results), 1)
        doc_id, score, meta = results[0]
        self.assertGreaterEqual(score, 0.5)
