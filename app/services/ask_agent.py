"""
Agente de conversação — orquestra o fluxo completo de atendimento a perguntas.

Fluxo principal (ask()):
  1. parse_slash() → se for /comando, executa diretamente
  2. _respond_greeting() → saudações simples
  3. classify_with_llm() → classifica intenção via LLM
  4. De acordo com a intenção:
     - greeting → resposta amigável
     - help → executa ferramenta de ajuda
     - single_tools (add, correct, sync, list_periods, list_types) → executa direto
     - demais ferramentas → delega para ReActAgent (encadeamento multi-passo)
  5. Se nada classificar → ReActAgent como fallback

Slash commands especiais:
  /confirm <id>  → confirma preview pendente (add/correct)
  /cancel <id>   → cancela preview pendente
  /add <texto>   → adiciona memória (com preview)
  /correct <id> <texto> → corrige memória (com preview)

Usado em: routes.py (API), cli.py (CLI).
"""

from __future__ import annotations

import json
from typing import Generator

from app.config import settings
from app.services.router import parse_slash, classify_with_llm
from app.services.agent import ReActAgent
from app.services.tools import (
    TOOL_DEFINITIONS, TOOL_LABELS, _execute_tool, _get_llm, _get_search, _get_sqlite,
    _store_preview, _confirm_preview, _cancel_preview, _get_ingestion,
)
from app.services.parser import ParserService
from app.services.synthesis import synthesize_answer_stream
from app.services.utils import smart_truncate
from app.storage.vector_store import VectorStore
from app.services.search import SearchService


GREETINGS = {
    "oi": "Olá! Como posso ajudar com as memórias institucionais?",
    "bom dia": "Bom dia! Como posso ajudá-lo hoje?",
    "boa tarde": "Boa tarde! Em que posso ajudar?",
    "boa noite": "Boa noite! Como posso ajudar?",
    "obrigado": "Por nada! Estou aqui para ajudar.",
    "valeu": "Disponível! Qual sua dúvida?",
    "tudo bem": "Tudo bem! Como posso ajudar?",
}


def _respond_greeting(question: str) -> str | None:
    """
    Verifica se a pergunta é uma saudação conhecida e retorna a resposta.

    Usa correspondência exata (após strip/lower) e depois substring.
    É chamado antes do classificador LLM para responder rápido sem custo de API.
    """
    q = question.lower().strip().strip("?!.,")
    if q in GREETINGS:
        return GREETINGS[q]
    for phrase, resp in GREETINGS.items():
        if phrase in q:
            return resp
    return None


SINGLE_TOOLS = {"help", "add_memory", "correct_memory", "sync_documents",
                 "list_periods", "list_fact_types"}


class AskAgent:
    """
    Orquestrador principal de conversação.

    Uso:
      agent = AskAgent()
      for chunk in agent.ask("quantas memórias existem?"):
          print(chunk, end="")
    """

    def __init__(self, search: SearchService | None = None, vector: VectorStore | None = None):
        self.search = search
        self.vector = vector

    def _preview_add(self, text: str, fact_type=None, closing_period=None, title=None) -> dict | None:
        """
        Cria um preview de adição de memória sem confirmar no banco.
        Usado em: slash /add e classificador add_memory.
        """
        from app.models import FactType
        parser = ParserService(_get_llm())
        preview = parser.parse(text)
        if not preview:
            return None
        if fact_type:
            try:
                preview.fact_type = FactType(fact_type)
            except ValueError:
                pass
        if closing_period:
            preview.closing_period = closing_period
        if title:
            preview.title = smart_truncate(title, 50)
        preview_id = _store_preview("add", text, preview)
        return {
            "preview_id": preview_id,
            "title": preview.title,
            "fact_type": preview.fact_type.value,
            "closing_period": preview.closing_period,
            "tags": preview.tags,
            "description": preview.description,
        }

    def _preview_correct(self, text: str, id: str | None = None) -> dict | None:
        """
        Cria um preview de correção de memória sem confirmar no banco.
        Usado em: slash /correct e classificador correct_memory.
        """
        sqlite = _get_sqlite()
        existing = None
        if id:
            existing = sqlite.get_memory(id)
            if not existing:
                return {"error": f"Memória com ID '{id}' não encontrada."}
        else:
            from app.services.tools import _infer_memory
            inferred = _infer_memory(text)
            if not inferred:
                return {"error": "Não foi possível identificar qual memória corrigir. Forneça um ID."}
            existing_id = inferred.get("id", "")[:8]
            existing = sqlite.get_memory(existing_id)
            if not existing:
                return {"error": "Memória inferida não encontrada."}

        from app.services.tools import CORRECT_PROMPT, CORRECT_JSON_SCHEMA
        prompt = CORRECT_PROMPT.format(
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
            content = parser.llm.invoke(prompt=prompt, max_tokens=2000, temperature=0.1)
            if not content or not content.strip():
                return {"error": "LLM retornou resposta vazia."}
            preview = parser._parse_response(content, f"{existing.description}\n{text}")
        except Exception as e:
            return {"error": f"Erro ao processar correção: {e}"}

        preview.supersedes_id = existing.id
        preview.is_correction = True
        preview_id = _store_preview("correct", text, preview)
        return {
            "preview_id": preview_id,
            "title": preview.title,
            "fact_type": preview.fact_type.value,
            "closing_period": preview.closing_period,
            "tags": preview.tags,
            "description": preview.description,
            "supersedes_id": existing.id,
            "supersedes_title": existing.title,
        }

    def ask(self, question: str) -> Generator[str, None, None]:
        """
        Processa uma pergunta e produz chunks de texto formatado (streaming).

        Fluxo:
          1. /comando → executa direto (add, correct com preview; confirm, cancel)
          2. Saudação → resposta inline
          3. LLM classifica → executa ferramenta ou delega ao ReActAgent
          4. Fallback → ReActAgent
        """
        from app.models import FactType
        slash = parse_slash(question)
        if slash:
            tool, params = slash

            # /confirm <id> — confirma preview pendente
            if tool == "confirm_preview":
                yield f"⚙️ Processando...\n"
                result = _confirm_preview(params.get("preview_id", ""))
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield "✅\n\n"
                yield from synthesize_answer_stream(question, result, "add_memory")
                return

            # /cancel <id> — cancela preview pendente
            if tool == "cancel_preview":
                yield f"⚙️ Processando...\n"
                result = _cancel_preview(params.get("preview_id", ""))
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield f"  ❌ Operação **{result.get('title', '')}** cancelada.\n"
                return

            # /add <texto> ou /correct ... — mostra preview antes de confirmar
            if tool in ("add_memory", "correct_memory"):
                yield f"⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"

                if tool == "add_memory":
                    preview_data = self._preview_add(**params)
                else:
                    preview_data = self._preview_correct(**params)

                if not preview_data:
                    yield f"  ❌ Não foi possível interpretar o texto fornecido.\n"
                    return
                if isinstance(preview_data, dict) and "error" in preview_data:
                    yield f"  ❌ {preview_data['error']}\n"
                    return

                pid = preview_data["preview_id"]
                yield "\n📋 **Prévia da operação**\n\n"
                yield from synthesize_answer_stream(question, preview_data, f"{tool}_preview")
                yield f"\n\nPara **confirmar**, digite `/confirm {pid}`\n"
                yield f"Para **cancelar**, digite `/cancel {pid}`\n"
                return

            # Demais /comandos (search, list, get, count, help, sync-docs)
            yield f"⚙️ Processando...\n"
            yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"
            result = _execute_tool(tool, params)
            if isinstance(result, dict) and "error" in result:
                yield f"  ❌ {result['error']}\n"
                return
            yield "✅\n\n"
            if isinstance(result, str):
                yield result
            else:
                yield from synthesize_answer_stream(question, result, tool)
            return

        # Saudação
        greeting = _respond_greeting(question)
        if greeting:
            yield f"💬 {greeting}\n"
            return

        # Classificação via LLM
        llm = _get_llm()
        classification = classify_with_llm(llm, question)

        if classification:
            intent = classification.get("intent")
            tool = classification.get("tool")

            if intent == "greeting":
                yield f"💬 {classification.get('response', 'Olá! Como posso ajudar?')}\n"
                return

            if intent == "help":
                yield f"⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get('help', 'Ajuda')}...\n"
                result = _execute_tool("help", {})
                yield "✅\n\n"
                yield result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                return

            if tool in SINGLE_TOOLS:
                params = classification.get("params", {})
                # add_memory e correct_memory passam pelo fluxo de preview
                if tool in ("add_memory", "correct_memory"):
                    yield f"⚙️ Processando...\n"
                    yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"
                    if tool == "add_memory":
                        preview_data = self._preview_add(**params)
                    else:
                        preview_data = self._preview_correct(**params)
                    if not preview_data:
                        yield f"  ❌ Não foi possível interpretar o texto fornecido.\n"
                        return
                    if isinstance(preview_data, dict) and "error" in preview_data:
                        yield f"  ❌ {preview_data['error']}\n"
                        return
                    pid = preview_data["preview_id"]
                    yield "\n📋 **Prévia da operação**\n\n"
                    yield from synthesize_answer_stream(question, preview_data, f"{tool}_preview")
                    yield f"\n\nPara **confirmar**, digite `/confirm {pid}`\n"
                    yield f"Para **cancelar**, digite `/cancel {pid}`\n"
                    return

                # Demais single tools (sync_documents, list_periods, list_fact_types)
                yield f"⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"
                result = _execute_tool(tool, params)
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield "✅\n\n"
                yield from synthesize_answer_stream(question, result, tool)
                return

            # Ferramentas que podem exigir encadeamento (search, list, count, get, etc.)
            if tool and tool in TOOL_DEFINITIONS:
                params = classification.get("params", {})
                agent = ReActAgent(question, first_tool=tool, first_params=params)
                yield from agent.run()
                return

        # Fallback: ReActAgent sem first_tool (LLM decide o primeiro passo)
        yield from ReActAgent(question).run()

    def ask_sync(self, question: str) -> str:
        """Versão síncrona (não-streaming) de ask(). Usado em testes."""
        return "".join(self.ask(question))
