"""
Tool registry — TOOL_DEFINITIONS, TOOL_LABELS, HELP_COMMANDS, _execute_tool.

Contém a definição de todas as ferramentas que o AskAgent
podem executar. Centraliza também o vocabulário amigável (TOOL_LABELS)
para exibição ao usuário.

Usado em: agent.py, ask_agent.py.
"""

from __future__ import annotations

from typing import Any

from app.services.tool_helpers import _get_sqlite, _get_llm, _get_search
from app.services.tool_queries import (
    _count_memories,
    _search_memories,
    _get_memory_detail,
    _list_periods,
    _list_fact_types,
    _add_memory,
    _correct_memory,
    _list_memories,
    _search_documents,
    _sync_documents,
)

REACT_EXCLUDED: set[str] = {"count_memories"}

TOOL_LABELS: dict[str, str] = {
    "add_memory": "Adicionar memória",
    "correct_memory": "Corrigir memória",
    "search_memories": "Buscar memórias",
    "list_memories": "Listar memórias",
    "get_memory_detail": "Detalhes da memória",
    "count_memories": "Contar memórias",
    "list_periods": "Listar períodos",
    "list_fact_types": "Listar tipos",
    "search_documents": "Buscar documentos",
    "sync_documents": "Sincronizar documentos",
    "help": "Ajuda",
    "confirm_preview": "Confirmar operação",
    "cancel_preview": "Cancelar operação",
}


TOOL_DEFINITIONS = {
    "count_memories": {
        "description": "Contar memórias com filtros determinísticos (ativo/inativo, tipo, período, tags). NÃO usa busca por texto — só filtros exatos. Para contar memórias sobre um assunto, use search_memories.",
        "params": {
            "active": {"type": "boolean", "description": "Filtrar apenas ativas (true) ou inativas (false)", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo (rule_change, decision, implementation, incident, other)", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período no formato YYYY-MM", "required": False},
            "tags": {"type": "string", "description": "Filtrar por tags (separadas por vírgula)", "required": False},
        },
        "fn": _count_memories,
    },
    "search_memories": {
        "description": "Buscar memórias por texto com busca semântica (embedding + SQL). Use quando precisar encontrar memórias por assunto/tópico.",
        "params": {
            "query": {"type": "string", "description": "Termo de busca", "required": True},
            "top_k": {"type": "integer", "description": "Número de resultados a retornar", "required": False},
            "fact_type": {"type": "string", "description": "Filtrar por tipo", "required": False},
            "closing_period": {"type": "string", "description": "Filtrar por período YYYY-MM", "required": False},
            "tags": {"type": "string", "description": "Filtrar por tags (separadas por vírgula, ex: compliance,credito)", "required": False},
            "active": {"type": "boolean", "description": "Filtrar apenas ativas (true) ou inativas (false)", "required": False},
            "latest_only": {"type": "boolean", "description": "Se true, retorna apenas a versão mais recente de cada cadeia de correções (remove versões substituídas)", "required": False},
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
            "reverse": {"type": "boolean", "description": "Se true, ordena da mais antiga para a mais nova", "required": False},
            "offset": {"type": "integer", "description": "Quantos resultados pular (para paginação, ex: offset=1 para segunda memória)", "required": False},
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
        "fn": lambda: _format_user_help(),
    },
}


def _format_tool_descriptions() -> str:
    lines = []
    for name, t in TOOL_DEFINITIONS.items():
        if name in REACT_EXCLUDED:
            continue
        params_desc = []
        for pname, pinfo in t["params"].items():
            req = " (obrigatório)" if pinfo.get("required") else ""
            params_desc.append(f"      - {pname}: {pinfo['description']}{req}")
        params_str = "\n".join(params_desc) if params_desc else "      (nenhum)"
        lines.append(f"- {name}: {t['description']}\n{params_str}")
    return "\n\n".join(lines)


HELP_COMMANDS = [
    {
        "cmd": "/add",
        "desc": "Adicionar nova memória institucional",
        "usage": "/add <texto>",
        "params": [
            ("--type", "Tipo da memória", "opcional"),
            ("--title", "Título personalizado", "opcional"),
        ],
        "examples": [
            "/add Nova regra de crédito aprovada em junho/2026",
            "/add Decisão sobre limite de exposição --type decision",
        ],
    },
    {
        "cmd": "/correct",
        "desc": "Corrigir/substituir uma memória existente",
        "usage": "/correct [id] <texto>",
        "params": [
            ("id", "ID da memória a corrigir (8+ caracteres)", "opcional — infere se omitido"),
        ],
        "examples": [
            "/correct a9f51276 O limite foi alterado para 5MM",
            "/correct Atualizar o percentual de PDD para 2.5%",
        ],
    },
    {
        "cmd": "/search",
        "desc": "Buscar memórias por assunto",
        "usage": "/search <termo>",
        "params": [
            ("--type", "Filtrar por tipo (rule_change, decision, etc.)", "opcional"),
            ("--period", "Filtrar por período (YYYY-MM)", "opcional"),
            ("--tags", "Filtrar por tags (separadas por vírgula)", "opcional"),
        ],
        "examples": [
            "/search compliance",
            "/search regra de crédito --type rule_change",
            "/search PDD --period 2026-06",
        ],
    },
    {
        "cmd": "/list",
        "desc": "Listar memórias registradas",
        "usage": "/list [--type T] [--period YYYY-MM] [--tags TAGS]",
        "params": [
            ("--type", "Filtrar por tipo", "opcional"),
            ("--period", "Filtrar por período (YYYY-MM)", "opcional"),
            ("--tags", "Filtrar por tags (separadas por vírgula)", "opcional"),
            ("--active", "Filtrar por ativas (true) ou inativas (false)", "opcional"),
        ],
        "examples": [
            "/list",
            "/list --type decision --period 2026-06",
            "/list --tags compliance,credito",
            "/list --active false",
        ],
    },
    {
        "cmd": "/get",
        "desc": "Ver detalhes completos de uma memória",
        "usage": "/get <id>",
        "params": [
            ("id", "ID da memória (8+ caracteres)", "obrigatório"),
        ],
        "examples": [
            "/get a9f51276",
            "/get 687e9",
        ],
    },
    {
        "cmd": "/count",
        "desc": "Contar memórias",
        "usage": "/count [--type T] [--period YYYY-MM] [--tags TAGS]",
        "params": [
            ("--type", "Filtrar por tipo", "opcional"),
            ("--period", "Filtrar por período (YYYY-MM)", "opcional"),
            ("--tags", "Filtrar por tags (separadas por vírgula)", "opcional"),
        ],
        "examples": [
            "/count",
            "/count --type incident",
            "/count --period 2026-06",
        ],
    },
    {
        "cmd": "/sync-docs",
        "desc": "Sincronizar documentos da pasta data/documents/",
        "usage": "/sync-docs",
        "params": [],
        "examples": ["/sync-docs"],
    },
    {
        "cmd": "/help",
        "desc": "Mostrar esta ajuda",
        "usage": "/help",
        "params": [],
        "examples": ["/help"],
    },
]


def _format_user_help() -> str:
    lines = ["**Navi** — Assistente de memória institucional\n"]
    lines.append("Você pode conversar em **linguagem natural** ou usar **comandos diretos**:\n")

    for c in HELP_COMMANDS:
        lines.append(f"---")
        lines.append(f"`{c['usage']}`")
        lines.append(f"_{c['desc']}_\n")
        if c["params"]:
            lines.append("| Parâmetro | Descrição |")
            lines.append("|---|---|")
            for name, desc, req in c["params"]:
                lines.append(f"| `{name}` | {desc} | _{req}_ |")
            lines.append("")
        if c["examples"]:
            lines.append("Exemplos:")
            for ex in c["examples"][:2]:
                lines.append(f"> `{ex}`")
            lines.append("")

    lines.append("---")
    lines.append("**Filtros comuns:** `--type`, `--period`, `--tags`, `--active`")
    lines.append("**Tipos:** rule_change, decision, implementation, incident, other")
    lines.append("\n**Dica:** Comece digitando `/` no chat para ver os comandos disponíveis.")
    return "\n".join(lines)


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
        unknown = []
        for pname, pinfo in t["params"].items():
            if pname in params:
                value = params[pname]
                if pinfo.get("type") == "boolean" and isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
                if pinfo.get("type") == "integer" and not isinstance(value, int):
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        pass
                valid_params[pname] = value
        for k in params:
            if k not in t["params"]:
                unknown.append(k)
        result = fn(**valid_params)
        if unknown:
            known_names = list(t["params"].keys())
            tool_name = TOOL_LABELS.get(tool, tool)
            if isinstance(result, dict):
                result["_dropped_params"] = unknown
            elif isinstance(result, list):
                result = {"results": result, "_dropped_params": unknown, "_from_tool": tool}
            else:
                result = {"result": result, "_dropped_params": unknown, "_from_tool": tool}
        return result
    except Exception as e:
        msg = str(e)
        if "validation error" in msg.lower() or "pydantic" in type(e).__module__:
            return {"error": "Erro de validação nos dados extraídos. Verifique se o texto contém informações suficientes (período, tipo, descrição)."}
        return {"error": msg}


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
