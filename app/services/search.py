from __future__ import annotations

from typing import Optional

from app.models import Memory, SearchResult
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


class SearchService:
    def __init__(self, sqlite: SQLiteStore, vector: VectorStore):
        self.sqlite = sqlite
        self.vector = vector

    def hybrid_search(
        self,
        query: str,
        top_k: int = 5,
        fact_type: Optional[str] = None,
        closing_period: Optional[str] = None,
    ) -> list[SearchResult]:
        vector_memories, vector_docs = self.vector.hybrid_search(
            query, top_k_memories=top_k, top_k_docs=3
        )

        memory_ids = [mid for mid, _, _ in vector_memories]
        memories = self.sqlite.get_memories_by_ids(memory_ids)

        if fact_type or closing_period:
            sql_memories = self.sqlite.search_memories_sql(
                fact_type=fact_type,
                closing_period=closing_period,
                limit=top_k,
            )
            existing_ids = {m.id for m in memories}
            for m in sql_memories:
                if m.id not in existing_ids:
                    memories.append(m)
                    existing_ids.add(m.id)

        memory_map = {m.id: m for m in memories}
        id_to_score = {mid: score for mid, score, _ in vector_memories}

        results = []
        for mid in memory_ids:
            memory = memory_map.get(mid)
            if not memory:
                continue
            score = id_to_score.get(mid, 0.0)
            warnings = []

            if memory.superseded_by:
                superseder = self.sqlite.get_memory(memory.superseded_by)
                if superseder:
                    warnings.append(
                        f"Esta memória foi corrigida por {superseder.title} ({memory.superseded_by[:8]}...)"
                    )

            docs = self.sqlite.get_documents_by_memory(mid)

            results.append(SearchResult(
                memory=memory,
                score=score,
                warnings=warnings,
                related_documents=docs,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
