from __future__ import annotations

import json
import re
import time
from typing import Generator

from app.config import settings
from app.services.router import parse_slash, classify_with_llm, classify_by_rules
from app.services.tool_registry import (
    TOOL_DEFINITIONS, TOOL_LABELS, _execute_tool,
)
from app.services.tool_helpers import (
    _get_llm, _get_search, _get_sqlite,
    _store_preview, _confirm_preview, _cancel_preview, _get_ingestion,
)
from app.services.logger import logger
from app.services.parser import ParserService
from app.services.synthesis import synthesize_answer_stream
from app.services.utils import smart_truncate
from app.storage.vector_store import VectorStore
from app.services.search import SearchService


_AFFIRMATIONS = frozenset([
    "sim", "isso", "exato", "claro", "pode ser", "uhum", "isso mesmo",
    "sim isso", "isso ai", "isso aí", "com certeza", "pode ser isso",
    "certamente", "logico", "lógico", "sim quero", "quero sim",
    "boa", "bora", "vamos", "ok", "pode ser sim",
])


def _is_affirmation(text: str) -> bool:
    t = text.lower().strip().strip("?!.,;: ")
    return t in _AFFIRMATIONS


def _suggest_clarification(question: str, session_history: list[dict] | None) -> str | None:
    llm = _get_llm()
    history = _format_session_history(session_history or [])
    prompt = (
        f"Contexto da conversa:\n{history}\n\n"
        f'O usuário acabou de perguntar: "{question}"\n\n'
        "Com base no contexto, sugira a PERGUNTA COMPLETA que o usuário provavelmente quer fazer.\n"
        "PRESERVE todos os filtros da pergunta original (active, tags, fact_type, etc).\n"
        "NUNCA inverta filtros — se o usuario pediu ativas, nao sugira inativas.\n"
        "Retorne APENAS a pergunta sugerida, sem explicações ou formatação extra.\n"
        'Exemplo: quantas memorias sobre compliance existem?'
    )
    try:
        result = llm.invoke(prompt=prompt, max_tokens=100, temperature=0.3)
        result = result.strip().strip('"').strip("'")
        return result if result else None
    except Exception:
        return None


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
    q = question.lower().strip().strip("?!.,")
    if q in GREETINGS:
        return GREETINGS[q]
    for phrase, resp in GREETINGS.items():
        if phrase in q:
            return resp
    return None


_MAX_SESSION_TURNS = 10


def _format_previous_turn(session_history: list[dict] | None) -> str:
    if not session_history or len(session_history) == 0:
        return ""
    last = session_history[-1]
    cls = last.get("classification")
    if not cls:
        return ""
    tool = cls.get("tool", "")
    params = cls.get("params", {})
    result = last.get("assistant", "")[:200]
    lines = [
        "=== TURNO ANTERIOR ===",
        f'ferramenta: {tool}',
        f'parametros: {json.dumps(params, ensure_ascii=False)}',
        f'resultado: "{result}"',
        "=====================",
    ]
    return "\n".join(lines)


_CLOSING_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_params(tool: str, params: dict) -> dict:
    from app.services.tool_registry import TOOL_DEFINITIONS
    t = TOOL_DEFINITIONS.get(tool)
    if not t:
        return params
    valid_keys = set(t["params"].keys())
    cleaned = {k: v for k, v in params.items() if k in valid_keys}
    removed = set(params.keys()) - valid_keys
    if removed:
        logger.debug("validate_params(%s): removidos %s", tool, removed)
    if "closing_period" in cleaned:
        if not _CLOSING_PERIOD_RE.match(str(cleaned["closing_period"])):
            logger.warning("validate_params(%s): closing_period=%r invalido — removido",
                          tool, cleaned["closing_period"])
            del cleaned["closing_period"]
    return cleaned


class _DebugCapture:
    def __init__(self, llm):
        self._llm = llm
        self.calls: list[dict] = []

    def invoke(self, prompt, system_prompt="", **kwargs):
        call = {"prompt": prompt, "system_prompt": system_prompt, "params": kwargs}
        t0 = time.time()
        try:
            result = self._llm.invoke(prompt, system_prompt=system_prompt, **kwargs)
            call["response"] = result
            return result
        except Exception as e:
            call["error"] = str(e)
            raise
        finally:
            call["duration"] = round(time.time() - t0, 3)
            self.calls.append(call)


def _format_session_history(history: list[dict]) -> str:
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
    lines.append("Com base no histórico ACIMA, responda à PERGUNTA MAIS RECENTE do usuário.")
    return "\n".join(lines)


class AskAgent:
    _sessions: dict[str, list[dict]] = {}
    _debug_sessions: set[str] = set()

    def __init__(self, search: SearchService | None = None, vector: VectorStore | None = None):
        self.search = search
        self.vector = vector
        self._pending_clarifications: dict[str, str] = {}
        self._last_classification: dict | None = None

    def _preview_add(self, text: str, fact_type=None, closing_period=None, title=None) -> dict | None:
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
        sqlite = _get_sqlite()
        existing = None
        if id:
            existing = sqlite.get_memory(id)
            if not existing:
                return {"error": f"Memória com ID '{id}' não encontrada."}
        else:
            from app.services.tool_queries import _infer_memory
            inferred = _infer_memory(text)
            if not inferred:
                return {"error": "Não foi possível identificar qual memória corrigir. Forneça um ID."}
            existing_id = inferred.get("id", "")[:8]
            existing = sqlite.get_memory(existing_id)
            if not existing:
                return {"error": "Memória inferida não encontrada."}

        from app.prompts import load_prompt
        prompt = load_prompt("correct_prompt",
            title=existing.title,
            fact_type=existing.fact_type.value,
            closing_period=existing.closing_period,
            description=existing.description,
            decided_by=existing.decided_by or "",
            requested_by=existing.requested_by or "",
            approved_by=existing.approved_by or "",
            correction_text=text,
            json_schema=load_prompt("correct_schema"),
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

    def ask(self, question: str, session_id: str | None = None) -> Generator[str, None, None]:
        session_history: list[dict] | None = None
        if session_id:
            session_history = self._sessions.setdefault(session_id, [])

        pending = (session_id or "") in self._pending_clarifications
        if pending:
            orig = self._pending_clarifications.pop(session_id or "")
            if _is_affirmation(question):
                question = f"{orig} (contexto: usuário confirmou a abordagem)"
            else:
                question = f"{orig} (contexto: usuário respondeu: '{question}')"

        self._last_classification = None
        hist_block = _format_session_history(session_history or [])

        full = ""
        is_clarification = False
        for chunk in self._ask_raw(question, session_history, hist_block, session_id=session_id):
            if isinstance(chunk, dict):
                yield chunk
            else:
                full += chunk
                if chunk.startswith("🤔"):
                    is_clarification = True
                yield chunk

        if session_history is not None:
            _trimmed = full.rstrip()
            entry: dict = {"user": question, "assistant": _trimmed}
            if self._last_classification:
                entry["classification"] = self._last_classification
            if is_clarification and session_id:
                self._pending_clarifications[session_id] = question
            session_history.append(entry)
            if len(session_history) > _MAX_SESSION_TURNS:
                del session_history[:len(session_history) - _MAX_SESSION_TURNS]

    def _is_debug(self, session_id: str | None) -> bool:
        return bool(session_id and session_id in self._debug_sessions)

    def _ask_raw(self, question: str, session_history: list[dict] | None,
                 hist_block: str, session_id: str | None = None) -> Generator:
        self._last_classification = None
        debug = self._is_debug(session_id)

        if question.strip().lower() == "/debug":
            if session_id:
                if session_id in self._debug_sessions:
                    self._debug_sessions.discard(session_id)
                    yield "🔍 Modo debug **desativado**.\n"
                else:
                    self._debug_sessions.add(session_id)
                    yield "🔍 Modo debug **ativado**.\n"
            else:
                yield "❌ Modo debug requer uma sessão (use pelo chat).\n"
            return

        llm = _get_llm()
        dbg = _DebugCapture(llm) if debug else None

        slash = parse_slash(question)
        if slash:
            tool, params = slash

            if tool == "confirm_preview":
                yield "⚙️ Processando...\n"
                result = _confirm_preview(params.get("preview_id", ""))
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield "✅\n\n"
                yield from synthesize_answer_stream(question, result, "add_memory")
                return

            if tool == "cancel_preview":
                yield "⚙️ Processando...\n"
                result = _cancel_preview(params.get("preview_id", ""))
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield f"  ❌ Operação **{result.get('title', '')}** cancelada.\n"
                return

            if tool in ("add_memory", "correct_memory"):
                yield "⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"

                if tool == "add_memory":
                    preview_data = self._preview_add(**params)
                else:
                    preview_data = self._preview_correct(**params)

                if not preview_data:
                    yield "  ❌ Não foi possível interpretar o texto fornecido.\n"
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

            yield "⚙️ Processando...\n"
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

        greeting = _respond_greeting(question)
        if greeting:
            yield f"💬 {greeting}\n"
            return

        classification = classify_by_rules(question)
        prev_turn_block = _format_previous_turn(session_history)

        if not classification:
            active_llm = dbg or llm
            classification = classify_with_llm(active_llm, question,
                conversation_history=session_history, previous_turn=prev_turn_block)
        if classification:
            tool = classification.get("tool")
            if tool:
                classification["params"] = _validate_params(tool, classification.get("params", {}))
        self._last_classification = classification

        if debug:
            yield {"debug": {"label": "classificador",
                             "classification": classification}}

        if classification:
            intent = classification.get("intent")
            tool = classification.get("tool")

            if intent == "greeting":
                yield f"💬 {classification.get('response', 'Olá! Como posso ajudar?')}\n"
                return

            if intent == "help":
                yield "⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get('help', 'Ajuda')}...\n"
                result = _execute_tool("help", {})
                yield "✅\n\n"
                yield result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                return

            if tool and tool in TOOL_DEFINITIONS:
                params = classification.get("params", {})
                yield "⚙️ Processando...\n"
                yield f"  ⚙️ {TOOL_LABELS.get(tool, tool)}...\n"
                result = _execute_tool(tool, params)
                if isinstance(result, dict) and "error" in result:
                    yield "  ⚙️ Buscar memórias...\n"
                    result = _execute_tool("search_memories", {"query": question})
                    if isinstance(result, dict) and "error" not in result:
                        yield "✅\n\n"
                        yield from synthesize_answer_stream(question, result, "search_memories")
                    else:
                        yield f"  ❌ {result['error']}\n"
                    return
                yield "✅\n\n"
                yield from synthesize_answer_stream(question, result, tool)
                return

        yield "⚙️ Processando...\n"
        yield "  ⚙️ Buscar memórias...\n"
        result = _execute_tool("search_memories", {"query": question})
        if isinstance(result, dict) and "error" not in result:
            yield "✅\n\n"
            yield from synthesize_answer_stream(question, result, "search_memories")
        else:
            yield "❌ Não encontrei informações sobre isso nas memórias institucionais.\n"

    def ask_sync(self, question: str) -> str:
        return "".join(self.ask(question))
