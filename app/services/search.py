from __future__ import annotations

import re
from typing import Optional

from app.models import Memory, SearchResult
from app.services.utils import normalize
from app.siglas import expand_terms
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


def _term_bonus(query: str, title: str, description: str, min_len: int = 2) -> float:
    norm_query = normalize(query)
    norm_title = normalize(title)
    norm_desc = normalize(description)
    text = f"{norm_title} {norm_desc}"
    hits = 0
    base_terms = norm_query.split()
    expanded_terms = expand_terms(base_terms)
    all_terms = expanded_terms if len(expanded_terms) > len(base_terms) else base_terms
    for term in all_terms:
        if len(term) < min_len:
            continue
        if term in text:
            hits += 1
    total = sum(1 for t in all_terms if len(t) >= min_len)
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
        active: Optional[bool] = None,
    ) -> list[SearchResult]:
        fetch_k = max(top_k * 5, 15)
        vector_memories, vector_docs = self.vector.hybrid_search(
            query, top_k_memories=fetch_k, top_k_docs=3
        )

        memory_ids = [mid for mid, _, _ in vector_memories]
        memories = self.sqlite.get_memories_by_ids(memory_ids)

        for sql_active in (True, False) if active is None else (active,):
            sql_memories = self.sqlite.search_memories_sql(
                fact_type=fact_type, closing_period=closing_period,
                tags=tags, active=sql_active, limit=fetch_k,
            )
            existing_ids = {m.id for m in memories}
            for m in sql_memories:
                if m.id not in existing_ids:
                    memories.append(m)
                    existing_ids.add(m.id)
                    memory_ids.append(m.id)

        memory_map = {m.id: m for m in memories}
        all_scores = {mid: score for mid, score, _ in vector_memories}

        results = []
        for mid in memory_ids:
            memory = memory_map.get(mid)
            if not memory:
                continue
            score = all_scores.get(mid, 0.0)

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

        if active is not None:
            results = [r for r in results if r.memory.is_active == active]

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]
