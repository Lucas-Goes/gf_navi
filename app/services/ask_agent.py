from __future__ import annotations

import json
import re
import unicodedata
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Generator

import requests

from app.config import settings
from app.models import FactType
from app.services.ingestion import IngestionService
from app.services.llm import create_provider
from app.services.parser import ParserService
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore

TOOL_SYSTEM_PROMPT = """Você é um assistente que escolhe ferramentas para responder perguntas sobre um banco de memórias institucionais.

Ferramentas disponíveis:

{tool_descriptions}

REGRAS:
- Escolha a ferramenta MAIS ADEQUADA para a pergunta.
- Se a pergunta for uma contagem, use count_memories.
- Se a pergunta for uma busca por assunto, use search_memories.
- Se o usuário quiser ADICIONAR uma nova memória, use add_memory.
- Se o usuário quiser CORRIGIR uma memória existente, use correct_memory.
- Se o usuário quiser LISTAR memórias, use list_memories.
- Se o usuário quiser BUSCAR em documentos, use search_documents.
- Se o usuário quiser SINCRONIZAR documentos, use sync_documents.
- Se o usuário quiser detalhes de uma memória específica, use get_memory_detail.
- Retorne APENAS um JSON válido, sem texto adicional, no formato:
{{"tool": "nome_da_ferramenta", "params": {{...}}}}
- Se nenhuma ferramenta for adequada, use search_memories com a pergunta como query."""

SYNTHESIS_SYSTEM_PROMPT = """Você é um assistente institucional que responde perguntas sobre memórias de fechamento mensal de um banco.

Com base no RESULTADO DA CONSULTA abaixo (retornado por uma ferramenta), responda a pergunta do usuário de forma clara, direta e objetiva em português.

REGRAS:
- CONFIE no resultado da consulta. Se a tool retornou dados USE-OS.
- Se o resultado tem um "total": N, responda com esse número. Não diga que não encontrou.
- Se o resultado é uma lista, apresente os itens de forma legível.
- Se houver erro, explique o que aconteceu.
- Se você não tem ferramenta para fazer o que o usuário pede, explique quais ferramentas você tem disponíveis.
- Use markdown para formatar (negrito, listas, blocos de código)."""

TOOL_LIST_PROMPT = """Você tem acesso às seguintes ferramentas:
- count_memories: contar memórias com filtros
- search_memories: buscar memórias por texto
- get_memory_detail: detalhes de uma memória específica
- list_periods: listar períodos de fechamento
- list_fact_types: listar tipos de memória
- add_memory: ADICIONAR uma nova memória
- correct_memory: CORRIGIR uma memória existente
- list_memories: LISTAR memórias com filtros
- search_documents: buscar em documentos
- sync_documents: sincronizar documentos"""

_SQLITE: SQLiteStore | None = None
_VECTOR: VectorStore | None = None
_LLM = None
_INGESTION: IngestionService | None = None


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
    return {
        "id": memory.id,
        "title": memory.title,
        "fact_type": memory.fact_type.value,
        "closing_period": memory.closing_period,
        "description": memory.description[:200],
        "is_active": memory.is_active,
    }


def _correct_memory(id: str, text: str) -> dict:
    sqlite = _get_sqlite()
    existing = sqlite.get_memory(id)
    if not existing:
        return {"error": f"Memória com ID '{id}' não encontrada."}

    parser = ParserService(_get_llm())
    preview = parser.parse(text)
    if not preview:
        return {"error": "Não foi possível interpretar o texto fornecido."}

    preview.supersedes_id = existing.id

    ingestion = _get_ingestion()
    memory = ingestion.confirm(preview)
    return {
        "id": memory.id,
        "supersedes_id": existing.id,
        "title": memory.title,
        "fact_type": memory.fact_type.value,
        "closing_period": memory.closing_period,
        "description": memory.description[:200],
        "is_active": memory.is_active,
    }


def _list_memories(fact_type: str | None = None,
                   closing_period: str | None = None,
                   active: bool | None = None,
                   limit: int = 20) -> list[dict]:
    sqlite = _get_sqlite()
    memories = sqlite.search_memories_sql(
        fact_type=fact_type,
        closing_period=closing_period,
        limit=limit,
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
        {
            "id": d.id[:8],
            "title": d.title,
            "filename": d.filename,
            "source_type": d.source_type,
        }
        for d in docs
    ]


def _sync_documents() -> dict:
    from app.doc_sync import cmd_sync_docs
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_sync_docs(_get_sqlite(), _get_vector())
    output = buf.getvalue()
    return {"output": output.strip()}


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
            "id": {"type": "string", "description": "ID ou prefixo de 8+ caracteres da memória a ser corrigida", "required": True},
            "text": {"type": "string", "description": "Nova descrição corrigida em linguagem natural", "required": True},
        },
        "fn": _correct_memory,
    },
    "list_memories": {
        "description": "Listar memórias com filtros opcionais. Use quando o usuário quiser ver, listar, exibir ou mostrar memórias.",
        "params": {
            "fact_type": {"type": "string", "description": "Filtrar por tipo", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período YYYY-MM", "required": False},
            "active": {"type": "boolean", "description": "Filtrar por ativas (true) ou inativas (false)", "required": False},
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


def _call_llm(prompt: str, system: str = "", max_tokens: int = 1000) -> str:
    llm = _get_llm()
    try:
        return llm.invoke(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.1)
    except requests.exceptions.Timeout:
        raise RuntimeError("O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor.")
    except Exception as e:
        raise RuntimeError(f"Erro no LLM: {e}")


def _call_llm_stream(prompt: str, system: str = "", max_tokens: int = 2000) -> Generator[str, None, None]:
    llm = _get_llm()
    try:
        yield from llm.invoke_stream(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.3)
    except requests.exceptions.Timeout:
        yield "\n\n❌ O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor."
    except Exception as e:
        yield f"\n\n❌ Erro no LLM: {e}"


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


STOPWORDS = set("""a ante ao aos após até com contra de desde em entre
para perante por sem sob sobre trás o a os as da das do dos dum duns
num nums numa um uma umas uns ele ela eles elas me te se nos vos
lhe lhes eu tu você vocês o a os as meu minha meus minhas teu tua
teus tuas seu sua seus suas nosso nossa nossos nossas isso isto esse
essa esses essas este esta estes estas aquele aquela aqueles aquelas
aquilo que qual quem como quanto quanta quantos quantas onde aonde
donde quando porque porquê pois já também ainda muito pouco mais menos
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
        for pname in t["params"]:
            if pname in params:
                valid_params[pname] = params[pname]
        return fn(**valid_params)
    except Exception as e:
        return {"error": str(e)}


def _synthesize_answer(question: str, tool_result: Any) -> str:
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    system = f"{SYNTHESIS_SYSTEM_PROMPT}\n\n{TOOL_LIST_PROMPT}"
    prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
    return _call_llm(prompt=prompt, system=system, max_tokens=1000)


def _synthesize_answer_stream(question: str, tool_result: Any) -> Generator[str, None, None]:
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    system = f"{SYNTHESIS_SYSTEM_PROMPT}\n\n{TOOL_LIST_PROMPT}"
    prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
    yield from _call_llm_stream(prompt=prompt, system=system, max_tokens=2000)


class AskAgent:
    def __init__(self, search: SearchService | None = None, vector: VectorStore | None = None):
        self.search = search
        self.vector = vector

    def _route_question(self, question: str) -> tuple[str, dict] | None:
        q = question.lower().strip()

        help_words = ["help", "ajuda", "pode fazer", "ferramentas", "funções", "o que você",
                      "como funciona", "capacidades", "comandos"]
        if any(w in q for w in help_words):
            return ("help", {})

        add_words = ["adiciona", "adicione", "cria", "crie", "registra", "insere",
                     "nova memória", "novo registro", "adicionar memória", "criar memória"]
        if any(w in q for w in add_words):
            return ("add_memory", {"text": question})

        correct_words = ["corrige", "corrija", "corrigir", "correção",
                         "corrigir memória", "corrigir memoria"]
        if any(w in q for w in correct_words):
            return ("correct_memory", {"text": question})

        return None

    def ask(self, question: str) -> Generator[str, None, None]:
        routed = self._route_question(question)
        if routed:
            tool, params = routed
        else:
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
                if tool not in TOOL_DEFINITIONS:
                    raise KeyError(tool)
            except Exception:
                tool = "search_memories"
                params = {"query": _extract_keywords(question) or question}

        if tool not in TOOL_DEFINITIONS:
            yield "❌ Ferramenta desconhecida."
            return

        yield f"🔍 Consultando..."

        result = _execute_tool(tool, params)

        if isinstance(result, dict) and "error" in result:
            yield f" {result['error']}\n"
            return

        if _is_empty_result(result) and tool not in ("help", "count_memories", "list_periods", "list_fact_types", "add_memory", "correct_memory", "sync_documents"):
            new_query = _extract_keywords(question) or question
            result = _execute_tool("search_memories", {"query": new_query})

        yield f" ✅\n\n"

        if isinstance(result, str):
            yield result
        else:
            yield from _synthesize_answer_stream(question, result)

    def ask_sync(self, question: str) -> str:
        return "".join(self.ask(question))
