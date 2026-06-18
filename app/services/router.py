"""
Roteador de intenções — classifica mensagens do usuário em comandos ou intenções.

Fluxo:
  1. parse_slash() tenta interpretar como /comando
  2. classify_with_llm() classifica linguagem natural via LLM

A classificação por LLM usa ROUTER_SYSTEM_PROMPT com 8 categorias:
  SAUDACAO → greeting
  HELP     → help
  ACTION_ADD → add_memory
  ACTION_CORRECT → correct_memory
  QUERY_LIST → list_memories
  QUERY_COUNT → count_memories
  QUERY_TOPIC → search_memories
  QUERY_ID → get_memory_detail
"""

from __future__ import annotations

import re
import json
from typing import Any

from app.prompts import load_prompt

# Mapeamento de flag -> nome de parâmetro
_FLAG_MAP = {
    "--type": "fact_type",
    "--period": "closing_period",
    "--tags": "tags",
    "--limit": "limit",
    "--active": "active",
}


def _parse_flags(rest: str) -> tuple[dict[str, Any], str]:
    """
    Extrai flags no estilo `--key value` do final do texto.

    Ex: "nova regra --type rule_change" → ({"fact_type": "rule_change"}, "nova regra")
    Ex: "compliance --type decision" → ({"fact_type": "decision"}, "compliance")

    Usado em: parse_slash() para comandos com filtros.
    """
    params = {}
    text = rest

    # Procura flags no formato --key "value" ou --key value
    for flag, param_name in _FLAG_MAP.items():
        pattern = re.compile(
            rf"{re.escape(flag)}\s+"
            rf'(?:"([^"]+)"|'  # quoted value
            rf"'([^']+)'|"  # single-quoted value
            rf"([\w\-.,/]+))",  # unquoted value
            re.I,
        )
        m = pattern.search(text)
        if m:
            value = m.group(1) or m.group(2) or m.group(3)
            if param_name == "limit":
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    pass
            params[param_name] = value
            text = text[:m.start()].strip() + " " + text[m.end():].strip()
            text = text.strip()

    # Remove trailing/extra spaces from leftover text
    text = re.sub(r"\s+", " ", text).strip()
    return params, text


def parse_slash(text: str) -> tuple[str, dict] | None:
    """
    Interpreta mensagens que começam com / como comandos diretos.
    Ex: "/add texto" → ("add_memory", {"text": "texto"})

    Suporta flags no final: `--type`, `--period`, `--tags`, `--limit`, `--active`.

    Usado em: ask_agent.py (primeiro estágio do ask()).
    """
    if not text.startswith("/"):
        return None
    parts = text[1:].strip().split(maxsplit=1)
    if not parts or not parts[0]:
        return None
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    SLASH_MAP = {
        "add": ("add_memory", {"text": rest}),
        "correct": _parse_correct(rest),
        "search": ("search_memories", {"query": rest}),
        "list": ("list_memories", {"limit": 20}),
        "get": ("get_memory_detail", {"id": rest.strip()}),
        "count": ("count_memories", {}),
        "help": ("help", {}),
        "sync-docs": ("sync_documents", {}),
        "confirm": ("confirm_preview", {"preview_id": rest.strip()}),
        "cancel": ("cancel_preview", {"preview_id": rest.strip()}),
    }
    if cmd not in SLASH_MAP:
        return None

    tool, params = SLASH_MAP[cmd]

    # Extrai flags para comandos que suportam filtros
    if tool in ("add_memory", "correct_memory", "search_memories", "list_memories", "count_memories"):
        flags, clean_text = _parse_flags(rest)
        if "text" in params:
            params["text"] = clean_text
        elif "query" in params:
            params["query"] = clean_text
        params.update(flags)
        if tool == "list_memories" and not flags.get("limit"):
            params.setdefault("limit", 20)

    return (tool, params)


def _parse_correct(rest: str) -> tuple[str, dict]:
    """
    Interpreta o argumento do /correct:
      /correct <id> <texto> → correction com ID explícito
      /correct <texto>      → correction sem ID (inferência automática)

    Usado em: parse_slash().
    """
    parts = rest.strip().split(maxsplit=1)
    if len(parts) == 2 and re.match(r"^[0-9a-f-]{8,}$", parts[0], re.I):
        return ("correct_memory", {"id": parts[0], "text": parts[1]})
    return ("correct_memory", {"text": rest})


def _format_conversation_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["Histórico recente da conversa:"]
    for entry in history:
        lines.append(f"Usuário: {entry['user']}")
        cls = entry.get("classification")
        if cls:
            tool = cls.get("tool", "")
            params = cls.get("params", {})
            if tool:
                params_str = ", ".join(f"{k}={v}" for k, v in params.items())
                lines.append(f"  → consulta: {tool}({params_str})")
        lines.append(f"Assistente: {entry['assistant']}")
    lines.append("---")
    lines.append("Com base no histórico ACIMA, classifique APENAS a PERGUNTA MAIS RECENTE do usuário.")
    return "\n".join(lines)


def classify_by_rules(question: str) -> dict | None:
    """
    Classificador rule-based para padrões comuns de perguntas.
    Roda antes do LLM classifier para evitar custo de API em perguntas simples.

    Retorna dict com {intent, tool, params} ou None se não casar nenhum padrão.
    """
    q = question.lower().strip().strip("?!.,;:")

    # Padrões de contagem: "quantas", "quantos", "conte", "total de"
    m = re.match(r'^(quantas|quantos|conte|total de|numero de|qual o total)\b', q)
    if m:
        params = {}
        text_query = None

        # Extrai active de palavras-chave
        if re.search(r'\binativas?\b|\binativo\b', q):
            params["active"] = False
        elif re.search(r'\bativas?\b|\bativo\b', q):
            params["active"] = True

        # Extrai fact_type se mencionado
        for ft in ("rule_change", "decision", "implementation", "incident", "other"):
            if ft in q:
                params["fact_type"] = ft
                break

        # Extrai assunto (texto após "sobre", "de", "referente a")
        assunto = re.split(r'\b(sobre|de|referente a|acerca de)\b', q, maxsplit=1)
        if len(assunto) > 1:
            text_query = assunto[-1].strip().strip("?!.,;:")
            # Limpa palavras de contagem que possam ter ficado
            text_query = re.sub(r'^(quantas|quantos|conte|total de|numero de)\s+', '', text_query).strip()

        # Com assunto textual → search_memories (count é só para filtros determinísticos)
        if text_query and len(text_query) > 2:
            search_params = {"query": text_query}
            if "active" in params:
                search_params["active"] = params["active"]
            if "fact_type" in params:
                search_params["fact_type"] = params["fact_type"]
            return {"intent": "tool", "tool": "search_memories", "params": search_params}

        # Sem assunto → count_memories puro (ex: "quantas ativas?")
        return {"intent": "tool", "tool": "count_memories", "params": params}

    # Padrões de versão atual: "qual a... atual/vigente/mais recente/ultima versao"
    if re.search(r'\b(qual|quais)\b', q) and re.search(r'\b(atual|vigente|mais recente|ultima versao|versao atual)\b', q):
        assunto = re.split(r'\b(sobre|de|referente a|acerca de)\b', q, maxsplit=1)
        if len(assunto) > 1:
            text_query = assunto[-1].strip().strip("?!.,;:")
            if len(text_query) > 2:
                return {"intent": "tool", "tool": "search_memories", "params": {"query": text_query, "latest_only": True}}

    # Padrões de listagem: "liste", "mostre", "quais", "exiba", "lista"
    if re.match(r'^(liste|mostre|quais|exiba|lista|exibir|listar)\b', q):
        params = {}
        if re.search(r'\binativas?\b|\binativo\b', q):
            params["active"] = False
        elif re.search(r'\bativas?\b|\bativo\b', q):
            params["active"] = True
        return {"intent": "tool", "tool": "list_memories", "params": params}

    # Padrões de busca: "fale sobre", "busque", "pesquise", "o que", "me diga"
    if re.match(r'^(fale sobre|busque|pesquise|o que|me diga|encontre|procure|pesquisar|buscar)\b', q):
        # Extrai query removendo o verbo inicial
        query = re.sub(r'^(fale sobre|busque|pesquise|o que|me diga|encontre|procure|pesquisar|buscar)\s+', '', q)
        if len(query) > 2:
            return {"intent": "tool", "tool": "search_memories", "params": {"query": query}}

    return None


def classify_with_llm(llm, question: str, conversation_history: list | None = None,
                      previous_turn: str = "") -> dict | None:
    """
    Classifica a intenção da pergunta usando o LLM com ROUTER_SYSTEM_PROMPT.

    Retorna dict com {intent, tool, params} ou None se falhar.

    Usado em: ask_agent.py (segundo estágio do ask(), após parse_slash).
    """
    if not llm:
        return None
    try:
        hist_block = _format_conversation_history(conversation_history or [])
        raw = llm.invoke(
            prompt=load_prompt("router_classify", question=question),
            system_prompt=load_prompt("router_system",
                conversation_history=hist_block,
                previous_turn=previous_turn),
            max_tokens=300,
            temperature=0.1,
        )
        raw = raw.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return None
        result = json.loads(raw[start:end + 1])
        intent = result.get("intent")
        # greetings não têm chave "tool" — é esperado
        if intent not in ("greeting", "help") and not result.get("tool"):
            return None
        return result
    except Exception:
        return None
