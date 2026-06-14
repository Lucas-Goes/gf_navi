from __future__ import annotations

from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from app.config import settings


class VectorStore:
    def __init__(self, persist_path: str, model_name: str):
        try:
            self.model = SentenceTransformer(model_name)
        except Exception as e:
            raise RuntimeError(
                f"Falha ao carregar modelo de embeddings '{model_name}': {e}\n"
                "   Verifique a conexão com a internet ou defina EMBEDDING_MODEL no .env"
            )
        self.client = chromadb.PersistentClient(
            path=persist_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.memories_collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"},
        )
        self.documents_collection = self.client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )

    def embed_text(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts).tolist()

    def add_memory(self, memory_id: str, title: str, description: str, metadata: dict):
        text = f"{title}. {description}"
        embedding = self.embed_text(text)
        self.memories_collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[text],
        )

    def update_memory(
        self, memory_id: str, title: str, description: str, metadata: dict
    ):
        self.delete_memory(memory_id)
        self.add_memory(memory_id, title, description, metadata)

    def delete_memory(self, memory_id: str):
        try:
            self.memories_collection.delete(ids=[memory_id])
        except Exception as e:
            print(f"   ⚠️  Erro ao remover memória do índice vetorial: {e}")

    def search_memories(
        self, query: str, top_k: int = 5
    ) -> list[tuple[str, float, dict]]:
        query_emb = self.embed_text(query)
        results = self.memories_collection.query(
            query_embeddings=[query_emb],
            n_results=top_k,
        )
        return self._parse_results(results)

    def add_document(
        self, doc_id: str, title: str, content: str, metadata: dict
    ):
        embedding = self.embed_text(content)
        self.documents_collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[content],
        )

    def delete_document(self, doc_id: str):
        try:
            self.documents_collection.delete(ids=[doc_id])
        except Exception as e:
            print(f"   ⚠️  Erro ao remover documento do índice vetorial: {e}")

    def search_documents(
        self, query: str, top_k: int = 5
    ) -> list[tuple[str, float, dict]]:
        query_emb = self.embed_text(query)
        results = self.documents_collection.query(
            query_embeddings=[query_emb],
            n_results=top_k,
        )
        return self._parse_results(results)

    def hybrid_search(
        self, query: str, top_k_memories: int = 5, top_k_docs: int = 3
    ) -> tuple[list[tuple[str, float, dict]], list[tuple[str, float, dict]]]:
        memories = self.search_memories(query, top_k_memories)
        docs = self.search_documents(query, top_k_docs)
        return memories, docs

    def _parse_results(
        self, results
    ) -> list[tuple[str, float, dict]]:
        parsed = []
        if not results["ids"]:
            return parsed
        for i, doc_id in enumerate(results["ids"][0]):
            score = results["distances"][0][i] if results["distances"] else 0.0
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            parsed.append((doc_id, 1.0 - score, metadata))
        return parsed
