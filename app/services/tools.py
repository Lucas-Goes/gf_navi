"""Tool registry — TOOL_DEFINITIONS + _execute_tool + helpers.

Separado de ask_agent.py para evitar circular import com agent.py.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import requests

from app.config import settings
from app.models import FactType
from app.services.ingestion import IngestionService
from app.services.llm import create_provider
from app.services.parser import ParserService
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore

_SQLITE: SQLiteStore | None = None
_VECTOR: VectorStore | None = None
_LLM = None
_INGESTION: IngestionService | None = None
_SEARCH: SearchService | None = None


def _get_sqlite():
    global _SQLITE
    if _SQLITE is None:
        _SQLITE = SQLiteStore(settings.sqlite_path)
        _SQLITE.run_migrations()
    return _SQLITE


def _get_vector():
    global _VECTOR
    if _VECTOR is None:
        _VECTOR = VectorStore(settings.chroma_path, settings.embedding_model)
    return _VECTOR


def _get_llm():
    global _LLM
    if _LLM is None:
        _LLM = create_provider(settings)
    return _LLM


def _get_ingestion():
    global _INGESTION
    if _INGESTION is None:
        _INGESTION = IngestionService(_get_sqlite(), _get_vector())
    return _INGESTION


def _get_search():
    global _SEARCH
    if _SEARCH is None:
        _SEARCH = SearchService(_get_sqlite(), _get_vector())
    return _SEARCH


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
        tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
        tag_clauses = []
        for t in tag_list:
            tag_clauses.append("tags LIKE ?")
            params.append(f"%{t}%")
        conditions.append(f"({' OR '.join(tag_clauses)})")
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


def _build_chain(sqlite: SQLiteStore, memory_id: str, max_depth: int = 20) -> list[dict]:
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


def _search_memories(query: str, top_k: int = 3, fact_type: str | None = None,
                      closing_period: str | None = None,
                      tags: str | None = None) -> list[dict]:
    search = _get_search()
    sqlite = _get_sqlite()
    tag_filter = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else None
    results = search.hybrid_search(
        query=query, top_k=top_k, fact_type=fact_type, closing_period=closing_period,
        tags=tag_filter,
    )
    superseder_ids = {r.memory.supersedes_id for r in results if r.memory.supersedes_id}
    filtered = [r for r in results if r.memory.id not in superseder_ids]
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
    from app.doc_sync import _link_documents
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
    parser = ParserService(_get_llm())
    preview = parser.parse(text)
    if not preview:
        return {"error": "Não foi possível interpretar o texto fornecido."}
    preview.supersedes_id = existing.id
    ingestion = _get_ingestion()
    memory = ingestion.confirm(preview)
    from app.doc_sync import _link_documents
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
                   limit: int = 20) -> list[dict]:
    sqlite = _get_sqlite()
    tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()] if tags else None
    memories = sqlite.search_memories_sql(
        fact_type=fact_type, closing_period=closing_period, tags=tag_list, limit=limit,
    )
    results = []
    for m in memories:
        if active is not None and m.is_active != active:
            continue
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
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_sync_docs(_get_sqlite(), _get_vector())
    return {"output": buf.getvalue().strip()}


TOOL_DEFINITIONS = {
    "count_memories": {
        "description": "Contar memórias com filtros opcionais. Use quando perguntarem quantas memórias existem, totais, contagens.",
        "params": {
            "active": {"type": "boolean", "description": "Filtrar apenas ativas (true) ou inativas (false)", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo (rule_change, decision, implementation, incident, other)", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período no formato YYYY-MM", "required": False},
            "tags": {"type": "string", "description": "Filtrar por tags (separadas por vírgula)", "required": False},
        },
        "fn": _count_memories,
    },
    "search_memories": {
        "description": "Buscar memórias por texto com busca semântica (embedding + SQL). Use como fallback quando a pergunta não se encaixar em outras ferramentas.",
        "params": {
            "query": {"type": "string", "description": "Termo de busca", "required": True},
            "top_k": {"type": "integer", "description": "Número de resultados (max 10)", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período YYYY-MM", "required": False},
            "tags": {"type": "string", "description": "Filtrar por tags (separadas por vírgula, ex: compliance,credito)", "required": False},
        },
        "fn": _search_memories,
    },
    "get_memory_detail": {
        "description": "Obter detalhes completos de uma memória pelo ID ou prefixo.",
        "params": {
            "id": {"type": "string", "description": "ID completo ou prefixo de 8+ caracteres", "required": True},
        },
        "fn": _get_memory_detail,
    },
    "list_periods": {
        "description": "Listar todos os períodos de fechamento disponíveis com contagem de memórias.",
        "params": {},
        "fn": _list_periods,
    },
    "list_fact_types": {
        "description": "Listar todos os tipos de memória disponíveis com contagem.",
        "params": {},
        "fn": _list_fact_types,
    },
    "add_memory": {
        "description": "Adicionar uma nova memória institucional. Use quando o usuário pedir para adicionar, criar, registrar ou salvar uma memória.",
        "params": {
            "text": {"type": "string", "description": "Descrição completa da memória em linguagem natural", "required": True},
            "fact_type": {"type": "string", "description": "Tipo da memória (rule_change, decision, implementation, incident, other) — opcional, detectado automaticamente", "required": False},
            "closing_period": {"type": "string", "description": "Período YYYY-MM — opcional, detectado automaticamente", "required": False},
            "title": {"type": "string", "description": "Título opcional (máx 100 caracteres)", "required": False},
        },
        "fn": _add_memory,
    },
    "correct_memory": {
        "description": "Corrigir/substituir uma memória existente. Use quando o usuário pedir para corrigir, atualizar, alterar ou modificar uma memória.",
        "params": {
            "text": {"type": "string", "description": "Nova descrição corrigida em linguagem natural", "required": True},
            "id": {"type": "string", "description": "ID ou prefixo de 8+ caracteres da memória a ser corrigida (opcional — se omitido, o sistema infere automaticamente)", "required": False},
        },
        "fn": _correct_memory,
    },
    "list_memories": {
        "description": "Listar memórias com filtros opcionais. Use quando o usuário quiser ver, listar, exibir ou mostrar memórias.",
        "params": {
            "fact_type": {"type": "string", "description": "Filtrar por tipo", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período YYYY-MM", "required": False},
            "active": {"type": "boolean", "description": "Filtrar por ativas (true) ou inativas (false)", "required": False},
            "tags": {"type": "string", "description": "Filtrar por tags (separadas por vírgula)", "required": False},
            "limit": {"type": "integer", "description": "Máximo de resultados (max 50)", "required": False},
        },
        "fn": _list_memories,
    },
    "search_documents": {
        "description": "Buscar documentos anexados às memórias. Use quando o usuário quiser buscar, pesquisar ou encontrar documentos.",
        "params": {
            "query": {"type": "string", "description": "Termo de busca no nome ou conteúdo do documento", "required": True},
            "top_k": {"type": "integer", "description": "Número de resultados (max 10)", "required": False},
        },
        "fn": _search_documents,
    },
    "sync_documents": {
        "description": "Sincronizar documentos da pasta data/documents/ com o banco. Use quando o usuário pedir para sincronizar, atualizar ou recarregar documentos.",
        "params": {},
        "fn": _sync_documents,
    },
    "help": {
        "description": "Mostrar a lista de ferramentas disponíveis e como usar o assistente. Use quando o usuário pedir ajuda, help, o que você pode fazer, quais são suas funções.",
        "params": {},
        "fn": lambda: _format_tool_descriptions(),
    },
}


def _format_tool_descriptions() -> str:
    lines = []
    for name, t in TOOL_DEFINITIONS.items():
        params_desc = []
        for pname, pinfo in t["params"].items():
            req = " (obrigatório)" if pinfo.get("required") else ""
            params_desc.append(f"      - {pname}: {pinfo['description']}{req}")
        params_str = "\n".join(params_desc) if params_desc else "      (nenhum)"
        lines.append(f"- {name}: {t['description']}\n{params_str}")
    return "\n\n".join(lines)


def _execute_tool(tool: str, params: dict) -> Any:
    t = TOOL_DEFINITIONS.get(tool)
    if not t:
        return {"error": f"Ferramenta '{tool}' não encontrada."}
    fn = t["fn"]
    try:
        missing = [n for n, p in t["params"].items()
                   if p.get("required") and n not in params]
        if missing:
            return {"error": f"Parâmetros obrigatórios faltando: {', '.join(missing)}"}
        valid_params = {}
        for pname, pinfo in t["params"].items():
            if pname in params:
                value = params[pname]
                if pinfo.get("type") == "integer" and not isinstance(value, int):
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        pass
                valid_params[pname] = value
        return fn(**valid_params)
    except Exception as e:
        msg = str(e)
        if "validation error" in msg.lower() or "pydantic" in type(e).__module__:
            return {"error": "Erro de validação nos dados extraídos. Verifique se o texto contém informações suficientes (período, tipo, descrição)."}
        return {"error": msg}


STOPWORDS = set("""a ante ao aos após até com contra de desde em entre
para perante por sem sob sobre trás o a os as da das do dos dum duns
num nums numa um uma umas uns ele ela eles elas me te se nos vos
lhe lhes eu tu você vocês o a os as meu minha meus minhas teu tua
teus tuas seu sua seus suas nosso nossa nossos nossas isso isto esse
essa esses essas este esta estes estas aquele aquela aquelas aquilo
que qual quem como quanto quanta quantos quantas onde aonde donde
quando porque porquê pois já também ainda muito pouco mais menos
demais todo toda todos todas algum alguma alguns algumas nenhum nenhuma
nenhuns nenhumas certo certa certos certas outro outra outros outras
vário vária vários várias tanto tanta tantos quantas quanto quanta
quantos qualquer quaisquer cada qual seja seja se caso sim não nem
era são fora fosse fosse fossem fosseis fosseis temos têm tem havia
haja hajam hajas hajamos hajais haja são seja seja sejamos sejais
sejam seria seriam seria seriam será serão seria seriam era eram é
são está estão estava estavam esteve estivera estiveram estivera
esteve estiveram estiverem estejamos estejais esteja estejam esteja
fui foi fomos foram fora foram fosse fosse fossem fosseis fosseis
fosse fosse fossem fora foram irei irá irão iria iriam iria iriam
vá vão vamos vais vai vou vai vai vão vamos""".split())


def _remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _extract_keywords(text: str) -> str:
    plain = _remove_accents(text)
    words = plain.lower().split()
    keywords = [w.strip(""".,;:!?()[]{}"'""") for w in words
                if len(w) > 2 and w not in STOPWORDS and not w.startswith(("http", "www"))]
    return " ".join(keywords[:10])


def _is_empty_result(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, list) and len(result) == 0:
        return True
    if isinstance(result, dict) and result.get("total", 1) == 0:
        return True
    if isinstance(result, dict) and "error" in result:
        return True
    return False
