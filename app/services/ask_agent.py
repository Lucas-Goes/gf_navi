from __future__ import annotations

import json
from typing import Generator

from app.config import settings
from app.services.router import parse_slash, classify_with_llm
from app.services.agent import ReActAgent
from app.services.tools import TOOL_DEFINITIONS, _execute_tool, _get_llm
from app.services.synthesis import synthesize_answer_stream
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
    def __init__(self, search: SearchService | None = None, vector: VectorStore | None = None):
        self.search = search
        self.vector = vector

    def ask(self, question: str) -> Generator[str, None, None]:
        slash = parse_slash(question)
        if slash:
            tool, params = slash
            yield f"⚙️ Processando...\n"
            yield f"  ⚙️ Passo 1: {tool}...\n"
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
                yield f"  ⚙️ Passo 1: help...\n"
                result = _execute_tool("help", {})
                yield "✅\n\n"
                yield result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                return

            if tool in SINGLE_TOOLS:
                params = classification.get("params", {})
                yield f"⚙️ Processando...\n"
                yield f"  ⚙️ Passo 1: {tool}...\n"
                result = _execute_tool(tool, params)
                if isinstance(result, dict) and "error" in result:
                    yield f"  ❌ {result['error']}\n"
                    return
                yield "✅\n\n"
                yield from synthesize_answer_stream(question, result, tool)
                return

            if tool and tool in TOOL_DEFINITIONS:
                params = classification.get("params", {})
                agent = ReActAgent(question, first_tool=tool, first_params=params)
                yield from agent.run()
                return

        yield from ReActAgent(question).run()

    def ask_sync(self, question: str) -> str:
        return "".join(self.ask(question))
