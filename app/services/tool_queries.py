from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

from app.models import FactType
from app.prompts import load_prompt
from app.services.parser import ParserService
from app.services.utils import clean_tags, remove_accents
from app.siglas import expand_query as expand_siglas_query
from app.siglas import expand_tags_grouped
from app.doc_sync import _link_documents
from app.services.tool_helpers import (
    _get_sqlite, _get_vector, _get_llm, _get_search, _get_ingestion,
)

CORRECT_JSON_SCHEMA = load_prompt("correct_schema")


def _count_memories(active: bool | None = None, fact_type: str | None = None,
                    closing_period: str | None = None,
                    tags: str | None = None) -> dict:
    sqlite = _get_sqlite()
    conditions = []
    params = []
    if active is True:
        conditions.append("is_active = 1")
    elif active is False:
        conditions.append("is_active = 0")
    if fact_type:
        conditions.append("fact_type = ?")
        params.append(fact_type)
    if closing_period:
        conditions.append("closing_period = ?")
        params.append(closing_period)
    if tags:
        tag_list = clean_tags(tags)
        groups = expand_tags_grouped(tag_list)
        group_clauses = []
        for group in groups:
            or_clauses = []
            for t in group:
                or_clauses.append("',' || tags || ',' LIKE '%,' || ? || ',%'")
                params.append(t)
            group_clauses.append(f"({' OR '.join(or_clauses)})")
        conditions.append(f"({' AND '.join(group_clauses)})")
    where = " AND ".join(conditions) if conditions else "1=1"
    conn = sqlite.connect()
    row = conn.execute(
        f"SELECT COUNT(*) as c FROM memories WHERE {where}", params
    ).fetchone()
    total = row["c"]
    parts = []
    if active is True:
        parts.append("ativas")
    elif active is False:
        parts.append("inativas")
    if fact_type:
        parts.append(f"tipo {fact_type}")
    if closing_period:
        parts.append(f"período {closing_period}")
    if tags:
        parts.append(f"tags: {tags}")
    label = f" ({', '.join(parts)})" if parts else ""
    return {"total": total, "label": label}


def _build_chain(sqlite, memory_id: str, max_depth: int = 20) -> list[dict]:
    chain = []
    current_id = memory_id
    depth = 0
    while current_id and depth < max_depth:
        m = sqlite.get_memory(current_id)
        if not m:
            break
        chain.append({
            "id": m.id,
            "title": m.title,
            "fact_type": m.fact_type.value,
            "closing_period": m.closing_period,
            "tags": m.tags,
            "description": m.description[:300],
            "registration_date": m.registration_date,
            "is_active": m.is_active,
            "supersedes_id": m.supersedes_id,
            "registered_by": m.registered_by,
        })
        current_id = m.supersedes_id
        depth += 1
    return chain


def _search_memories(query: str, top_k: int = 50, fact_type: str | None = None,
                     closing_period: str | None = None,
                     tags: str | None = None,
                     active: bool | None = None,
                     latest_only: bool = False) -> list[dict]:
    search = _get_search()
    sqlite = _get_sqlite()
    tag_filter = clean_tags(tags) if tags else None
    expanded_query = expand_siglas_query(query)
    results = search.hybrid_search(
        query=expanded_query, top_k=top_k, fact_type=fact_type, closing_period=closing_period,
        tags=tag_filter, active=active,
    )

    filtered = results
    if latest_only:
        superseder_ids = {r.memory.supersedes_id for r in results if r.memory.supersedes_id}
        filtered = [r for r in filtered if r.memory.id not in superseder_ids]

    norm_query = remove_accents(query.lower())
    norm_query = re.sub(r"[^a-z0-9]", " ", norm_query)
    long_terms = [t for t in norm_query.split() if len(t) >= 2]
    if long_terms:
        from app.siglas import expand_terms as expand_siglas_terms
        term_groups = [expand_siglas_terms([t]) for t in long_terms]

        def _has_all_terms(text: str) -> bool:
            normalized = remove_accents(text.lower())
            words = re.sub(r"[^a-z0-9]", " ", normalized).split()
            return all(any(t in words for t in group) for group in term_groups)
        filtered = [
            r for r in filtered
            if _has_all_terms(r.memory.title) or _has_all_terms(r.memory.description)
        ]

    return [
        {
            "id": r.memory.id,
            "title": r.memory.title,
            "score": round(r.score, 3),
            "fact_type": r.memory.fact_type.value,
            "closing_period": r.memory.closing_period,
            "tags": r.memory.tags,
            "description": r.memory.description[:500],
            "decided_by": r.memory.decided_by,
            "requested_by": r.memory.requested_by,
            "approved_by": r.memory.approved_by,
            "is_active": r.memory.is_active,
            "registered_by": r.memory.registered_by,
            "registration_date": r.memory.registration_date,
            "supersedes_id": r.memory.supersedes_id,
            "superseded_by": r.memory.superseded_by,
            "warnings": r.warnings,
            "documents": [{"title": d.title, "filename": d.filename}
                         for d in r.related_documents],
            "correction_chain": _build_chain(sqlite, r.memory.id),
        }
        for r in filtered
    ]


def _get_memory_detail(id: str) -> dict | None:
    sqlite = _get_sqlite()
    m = sqlite.get_memory(id)
    if not m:
        return None
    docs = sqlite.get_documents_by_memory(m.id)
    return {
        "id": m.id,
        "title": m.title,
        "fact_type": m.fact_type.value,
        "closing_period": m.closing_period,
        "tags": m.tags,
        "description": m.description,
        "decided_by": m.decided_by,
        "requested_by": m.requested_by,
        "approved_by": m.approved_by,
        "registration_date": m.registration_date,
        "is_active": m.is_active,
        "supersedes_id": m.supersedes_id,
        "superseded_by": m.superseded_by,
        "documents": [{"title": d.title, "filename": d.filename} for d in docs],
    }


def _list_periods() -> list[dict]:
    sqlite = _get_sqlite()
    conn = sqlite.connect()
    rows = conn.execute(
        "SELECT closing_period, COUNT(*) as c FROM memories GROUP BY closing_period ORDER BY closing_period DESC"
    ).fetchall()
    return [{"period": r["closing_period"], "count": r["c"]} for r in rows]


def _list_fact_types() -> list[dict]:
    sqlite = _get_sqlite()
    conn = sqlite.connect()
    rows = conn.execute(
        "SELECT fact_type, COUNT(*) as c FROM memories GROUP BY fact_type ORDER BY c DESC"
    ).fetchall()
    return [{"type": r["fact_type"], "count": r["c"]} for r in rows]


def _add_memory(text: str, fact_type: str | None = None,
                closing_period: str | None = None,
                title: str | None = None) -> dict:
    parser = ParserService(_get_llm())
    preview = parser.parse(text)
    if not preview:
        return {"error": "Não foi possível interpretar o texto fornecido."}
    if fact_type:
        try:
            preview.fact_type = FactType(fact_type)
        except ValueError:
            pass
    if closing_period:
        preview.closing_period = closing_period
    if title:
        preview.title = title[:100]
    ingestion = _get_ingestion()
    memory = ingestion.confirm(preview)
    _link_documents(_get_sqlite(), text, memory.id, preview.supersedes_id)
    return {
        "id": memory.id,
        "title": memory.title,
        "fact_type": memory.fact_type.value,
        "closing_period": memory.closing_period,
        "tags": memory.tags,
        "description": memory.description[:200],
        "is_active": memory.is_active,
    }


def _infer_memory(text: str) -> dict | None:
    search = _get_search()
    sqlite = _get_sqlite()
    results = search.hybrid_search(text, top_k=5)
    for r in results:
        if r.memory.is_active:
            return _get_memory_detail(r.memory.id)
    if not results:
        return None
    best = results[0].memory
    if best.superseded_by:
        superseder = sqlite.get_memory(best.superseded_by)
        if superseder and superseder.is_active:
            return _get_memory_detail(superseder.id)
    if not best.is_active:
        return None
    return _get_memory_detail(best.id)


def _correct_memory(text: str, id: str | None = None) -> dict:
    sqlite = _get_sqlite()
    if id:
        existing = sqlite.get_memory(id)
        if not existing:
            return {"error": f"Memória com ID '{id}' não encontrada."}
    else:
        inferred = _infer_memory(text)
        if not inferred:
            return {"error": "Não foi possível identificar qual memória corrigir. Forneça um ID."}
        existing_id = inferred.get("id", "")[:8]
        existing = sqlite.get_memory(existing_id)
        if not existing:
            return {"error": "Memória inferida não encontrada."}

    prompt = load_prompt("correct_prompt",
        title=existing.title,
        fact_type=existing.fact_type.value,
        closing_period=existing.closing_period,
        description=existing.description,
        decided_by=existing.decided_by or "",
        requested_by=existing.requested_by or "",
        approved_by=existing.approved_by or "",
        correction_text=text,
        json_schema=CORRECT_JSON_SCHEMA,
    )

    parser = ParserService(_get_llm())
    try:
        content = parser.llm.invoke(
            prompt=prompt,
            max_tokens=2000,
            temperature=0.1,
        )
        if not content or not content.strip():
            return {"error": "LLM retornou resposta vazia."}
        preview = parser._parse_response(content, f"{existing.description}\n{text}")
    except Exception as e:
        return {"error": f"Erro ao processar correção: {e}"}

    preview.supersedes_id = existing.id
    preview.is_correction = True
    ingestion = _get_ingestion()
    ingestion.store_preview(preview)
    memory = ingestion.confirm(preview)
    _link_documents(sqlite, text, memory.id, preview.supersedes_id)
    return {
        "id": memory.id,
        "supersedes_id": existing.id,
        "title": memory.title,
        "fact_type": memory.fact_type.value,
        "closing_period": memory.closing_period,
        "tags": memory.tags,
        "description": memory.description[:200],
        "is_active": memory.is_active,
    }


def _list_memories(fact_type: str | None = None,
                   closing_period: str | None = None,
                   active: bool | None = None,
                   tags: str | None = None,
                   limit: int = 20,
                   reverse: bool = False,
                   offset: int = 0) -> list[dict]:
    sqlite = _get_sqlite()
    tag_list = clean_tags(tags) if tags else None
    memories = sqlite.search_memories_sql(
        fact_type=fact_type, closing_period=closing_period, tags=tag_list, limit=limit,
        active=active, reverse=reverse, offset=offset,
    )
    results = []
    for m in memories:
        results.append({
            "id": m.id[:8],
            "title": m.title,
            "fact_type": m.fact_type.value,
            "closing_period": m.closing_period,
            "tags": m.tags,
            "description": m.description[:200],
            "decided_by": m.decided_by,
            "registered_by": m.registered_by,
            "registration_date": m.registration_date,
            "is_active": m.is_active,
        })
    return results


def _search_documents(query: str, top_k: int = 5) -> list[dict]:
    sqlite = _get_sqlite()
    docs = sqlite.search_documents(text_query=query, limit=top_k)
    return [
        {"id": d.id[:8], "title": d.title, "filename": d.filename, "source_type": d.source_type}
        for d in docs
    ]


def _sync_documents() -> dict:
    from app.doc_sync import cmd_sync_docs
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_sync_docs(_get_sqlite(), _get_vector())
    return {"output": buf.getvalue().strip()}
