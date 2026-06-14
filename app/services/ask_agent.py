from __future__ import annotations

import json
from typing import Any, Generator

from app.config import settings
from app.services.llm import create_provider
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


TOOL_SYSTEM_PROMPT = """Você é um assistente que escolhe ferramentas para responder perguntas sobre um banco de memórias institucionais.

Ferramentas disponíveis:

{tool_descriptions}

REGRAS:
- Escolha a ferramenta MAIS ADEQUADA para a pergunta.
- Se a pergunta pedir contagem, use count_memories.
- Se a pergunta pedir busca por assunto, use search_memories.
- Se a pergunta pedir detalhes de uma memória específica, use get_memory_detail.
- Retorne APENAS um JSON válido, sem texto adicional, no formato:
{{"tool": "nome_da_ferramenta", "params": {{...}}}}
- Se nenhuma ferramenta for adequada, use search_memories com a pergunta como query."""

SYNTHESIS_SYSTEM_PROMPT = """Você é um assistente institucional que responde perguntas sobre memórias de fechamento mensal de um banco.

Com base no resultado da consulta abaixo, responda a pergunta do usuário de forma clara e objetiva em português.

Se o resultado não for suficiente para responder, diga honestamente que não encontrou a informação.

Use markdown para formatar a resposta quando apropriado."""


TOOL_DEFINITIONS = {}

_SQLITE: SQLiteStore | None = None
_LLM = None


def _get_sqlite():
    global _SQLITE
    if _SQLITE is None:
        _SQLITE = SQLiteStore(settings.sqlite_path)
        _SQLITE.run_migrations()
    return _SQLITE


def _get_llm():
    global _LLM
    if _LLM is None:
        _LLM = create_provider(settings)
    return _LLM


def _count_memories(active: bool | None = None, fact_type: str | None = None,
                     closing_period: str | None = None) -> dict:
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
    label = f" ({', '.join(parts)})" if parts else ""
    return {"total": total, "label": label}


def _search_memories(query: str, top_k: int = 5, fact_type: str | None = None,
                      closing_period: str | None = None) -> list[dict]:
    sqlite = _get_sqlite()
    memories = sqlite.search_memories_sql(
        fact_type=fact_type,
        closing_period=closing_period,
        text_query=query,
        limit=top_k,
    )
    results = []
    for m in memories:
        results.append({
            "id": m.id[:8],
            "title": m.title,
            "fact_type": m.fact_type.value,
            "closing_period": m.closing_period,
            "description": m.description[:300],
            "decided_by": m.decided_by,
            "is_active": m.is_active,
        })
    return results


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


TOOL_DEFINITIONS = {
    "count_memories": {
        "description": "Contar memórias com filtros opcionais. Use quando perguntarem quantas memórias existem, totais, contagens.",
        "params": {
            "active": {"type": "boolean", "description": "Filtrar apenas ativas (true) ou inativas (false)", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo (rule_change, decision, implementation, incident, other)", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período no formato YYYY-MM", "required": False},
        },
        "fn": _count_memories,
    },
    "search_memories": {
        "description": "Buscar memórias por texto. Use como fallback quando a pergunta não se encaixar em outras ferramentas.",
        "params": {
            "query": {"type": "string", "description": "Termo de busca", "required": True},
            "top_k": {"type": "integer", "description": "Número de resultados (max 10)", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período YYYY-MM", "required": False},
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


def _call_llm(prompt: str, system: str = "", max_tokens: int = 1000) -> str:
    llm = _get_llm()
    return llm.invoke(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.1)


def _call_llm_stream(prompt: str, system: str = "", max_tokens: int = 2000) -> Generator[str, None, None]:
    llm = _get_llm()
    yield from llm.invoke_stream(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.3)


def _select_tool(question: str) -> tuple[str, dict] | None:
    tool_desc = _format_tool_descriptions()
    prompt = TOOL_SYSTEM_PROMPT.format(tool_descriptions=tool_desc)
    user_prompt = f"Pergunta do usuário: {question}\n\nQual ferramenta usar?"
    try:
        raw = _call_llm(prompt=user_prompt, system=prompt, max_tokens=500)
        raw = raw.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        data = json.loads(raw)
        tool = data.get("tool")
        params = data.get("params", {})
        if tool in TOOL_DEFINITIONS:
            return tool, params
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return None


def _execute_tool(tool: str, params: dict) -> Any:
    t = TOOL_DEFINITIONS.get(tool)
    if not t:
        return {"error": f"Ferramenta '{tool}' não encontrada."}
    fn = t["fn"]
    try:
        valid_params = {}
        for pname in t["params"]:
            if pname in params:
                valid_params[pname] = params[pname]
        return fn(**valid_params)
    except Exception as e:
        return {"error": str(e)}


def _synthesize_answer(question: str, tool_result: Any) -> str:
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
    return _call_llm(prompt=prompt, system=SYNTHESIS_SYSTEM_PROMPT, max_tokens=1000)


def _synthesize_answer_stream(question: str, tool_result: Any) -> Generator[str, None, None]:
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
    yield from _call_llm_stream(prompt=prompt, system=SYNTHESIS_SYSTEM_PROMPT, max_tokens=2000)


class AskAgent:
    def __init__(self, search: SearchService | None = None, vector: VectorStore | None = None):
        self.search = search
        self.vector = vector

    def ask(self, question: str) -> Generator[str, None, None]:
        tool_desc = _format_tool_descriptions()
        user_prompt = f"Pergunta do usuário: {question}\n\nQual ferramenta usar?"
        try:
            raw = _call_llm(prompt=user_prompt,
                           system=TOOL_SYSTEM_PROMPT.format(tool_descriptions=tool_desc),
                           max_tokens=500)
            raw = raw.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                raw = raw[start:end + 1]
            data = json.loads(raw)
            tool = data.get("tool")
            params = data.get("params", {})
        except (json.JSONDecodeError, KeyError, TypeError):
            yield "❌ Não foi possível determinar qual ferramenta usar para responder."
            return

        if tool not in TOOL_DEFINITIONS:
            yield "❌ Ferramenta desconhecida."
            return

        yield f"🔍 Consultando..."

        result = _execute_tool(tool, params)

        if isinstance(result, dict) and "error" in result:
            yield f" {result['error']}\n"
            return

        yield f" ✅\n\n"

        yield from _synthesize_answer_stream(question, result)

    def ask_sync(self, question: str) -> str:
        return "".join(self.ask(question))
