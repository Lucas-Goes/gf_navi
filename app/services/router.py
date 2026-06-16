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

ROUTER_SYSTEM_PROMPT = """Você é um classificador de intenções para um assistente de memórias institucionais.

Analise a mensagem e classifique na PRIMEIRA opção correspondente, seguindo a ordem abaixo:

1. SAUDACAO: "oi", "bom dia", "boa tarde", "obrigado", "valeu", "tudo bem", "hey", "olá"
   Ex: "oi tudo bem?" → {"intent": "greeting", "response": "Olá! Como posso ajudar?"}
   Ex: "obrigado" → {"intent": "greeting", "response": "Por nada! Estou aqui para ajudar."}

2. HELP: perguntar sobre capacidades, comandos, como usar, o que faz
   Ex: "o que você faz?" → {"intent": "help", "tool": "help", "params": {}}
   Ex: "quais são os comandos?" → {"intent": "help", "tool": "help", "params": {}}

3. ACTION_ADD: pedir para adicionar/criar/registrar/inserir ALGO NOVO
   Ex: "adicione nova regra de crédito aprovada em junho" → {"intent": "action", "tool": "add_memory", "params": {"text": "adicione nova regra de crédito aprovada em junho"}}
   Ex: "cria memoria sobre reunião de hoje" → {"intent": "action", "tool": "add_memory", "params": {"text": "cria memoria sobre reunião de hoje"}}

4. ACTION_CORRECT: pedir para corrigir/atualizar/alterar/modificar existente
   Ex: "corrige a memoria sobre cadastro EP" → {"intent": "action", "tool": "correct_memory", "params": {"text": "corrige a memoria sobre cadastro EP"}}
   Ex: "atualiza a regra de compliance" → {"intent": "action", "tool": "correct_memory", "params": {"text": "atualiza a regra de compliance"}}

5. QUERY_LIST: pedir para listar/mostrar/exibir/ver memórias (sem assunto específico)
   Ex: "me mostre as ultimas memorias" → {"intent": "query", "tool": "list_memories", "params": {"limit": 5}}
   Ex: "liste as memorias" → {"intent": "query", "tool": "list_memories", "params": {"limit": 20}}
   Ex: "exiba memorias de junho" → {"intent": "query", "tool": "list_memories", "params": {"limit": 20, "closing_period": "2026-06"}}

6. QUERY_COUNT: perguntar quantas memórias existem, totais, contagens
   Ex: "quantas memorias temos?" → {"intent": "query", "tool": "count_memories", "params": {}}
   Ex: "quantas regras de compliance existem?" → {"intent": "query", "tool": "count_memories", "params": {"fact_type": "rule_change"}}

7. QUERY_TOPIC: pergunta sobre ASSUNTO ESPECÍFICO (regra, decisão, implementação)
   Ex: "o que mudou no cadastro EP?" → {"intent": "query", "tool": "search_memories", "params": {"query": "cadastro EP", "top_k": 3}}
   Ex: "qual a nova política de crédito?" → {"intent": "query", "tool": "search_memories", "params": {"query": "política de crédito", "top_k": 3}}
   Ex: "teve algum ponto sobre compliance?" → {"intent": "query", "tool": "search_memories", "params": {"query": "compliance", "top_k": 3}}

8. QUERY_ID: pergunta mencionando ID hexadecimal de 8+ caracteres
   Ex: "me mostre a memoria a9f51276" → {"intent": "query", "tool": "get_memory_detail", "params": {"id": "a9f51276"}}
   Ex: "detalhes de 687e911d" → {"intent": "query", "tool": "get_memory_detail", "params": {"id": "687e911d"}}

REGRA DE DESEMPATE: se encaixar em múltiplas opções, escolha a de número MAIOR.
Ex: "quantas memorias sobre cadastro EP?" → opção 6 (count) e 7 (search) → escolhe 7.

Retorne APENAS o JSON, sem texto adicional."""

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
        "search": ("search_memories", {"query": rest, "top_k": 3}),
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


def classify_with_llm(llm, question: str) -> dict | None:
    """
    Classifica a intenção da pergunta usando o LLM com ROUTER_SYSTEM_PROMPT.

    Retorna dict com {intent, tool, params} ou None se falhar.

    Usado em: ask_agent.py (segundo estágio do ask(), após parse_slash).
    """
    if not llm:
        return None
    try:
        raw = llm.invoke(
            prompt=f"Classifique: {question}",
            system_prompt=ROUTER_SYSTEM_PROMPT,
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
