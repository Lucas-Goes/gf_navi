from __future__ import annotations

import re
import unicodedata
from typing import Optional

from app.models import Memory, SearchResult
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _term_bonus(query: str, title: str, description: str, min_len: int = 2) -> float:
    norm_query = _normalize(query)
    norm_title = _normalize(title)
    norm_desc = _normalize(description)
    text = f"{norm_title} {norm_desc}"
    hits = 0
    for term in norm_query.split():
        if len(term) < min_len:
            continue
        if term in text:
            hits += 1
    total = sum(1 for t in norm_query.split() if len(t) >= min_len)
    return hits / total if total else 0.0


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
        tags: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        fetch_k = max(top_k * 5, 15)
        vector_memories, vector_docs = self.vector.hybrid_search(
            query, top_k_memories=fetch_k, top_k_docs=3
        )

        memory_ids = [mid for mid, _, _ in vector_memories]
        memories = self.sqlite.get_memories_by_ids(memory_ids)

        if fact_type or closing_period or tags:
            sql_memories = self.sqlite.search_memories_sql(
                fact_type=fact_type,
                closing_period=closing_period,
                tags=tags,
                limit=fetch_k,
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

            term_boost = _term_bonus(query, memory.title, memory.description)
            combined = score + term_boost * 0.8

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
                score=combined,
                warnings=warnings,
                related_documents=docs,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        has_match = any(
            _term_bonus(query, r.memory.title, r.memory.description) > 0
            for r in results
        )
        if has_match:
            results = [
                r for r in results
                if _term_bonus(query, r.memory.title, r.memory.description) > 0
            ]
        return results[:top_k]
