from __future__ import annotations

import json
from typing import Any, Generator

from app.services.tools import (
    TOOL_DEFINITIONS, _execute_tool, _format_tool_descriptions,
    _is_empty_result, _extract_keywords,
)
from app.services.synthesis import synthesize_answer_stream, _call_llm

MAX_STEPS = 5

REACT_SYSTEM_PROMPT = """Você é Navi, um agente que responde perguntas sobre memórias institucionais do maior banco da América Latina.

Você tem acesso a ferramentas. Decida qual o PRÓXIMO passo para responder.

Já foram executados alguns passos. Analise os resultados e decida:
- Se já tem dados suficientes para responder → {"answer": "resposta...", "done": true}
- Se precisa de outra ferramenta → {"thought": "preciso de...", "tool": "nome", "params": {...}}

Exemplos de encadeamento:
- "mostre detalhes da memória sobre cadastro EP"
  Passo 1: search_memories(query="cadastro EP") → acha ID
  Passo 2: get_memory_detail(id="687e911d") → descrição completa
  Passo 3: answer com os dados → done

- "quantas memórias temos e liste as 2 últimas"
  Passo 1: count_memories() → total=15
  Passo 2: list_memories(limit=2) → últimas 2
  Passo 3: answer → done

Ferramentas disponíveis:
{tool_descriptions}

Retorne APENAS JSON, sem texto adicional."""


def _summarize(tool: str, result: Any) -> str:
    if isinstance(result, list):
        if not result:
            return "Nenhum resultado encontrado."
        items = []
        for r in result[:3]:
            title = r.get("title", r.get("name", str(r.get("id", ""))[:8]))
            items.append(title)
        suffix = f" (+{len(result)-3} outros)" if len(result) > 3 else ""
        return f"{len(result)} resultado(s): {', '.join(items)}{suffix}"
    if isinstance(result, dict):
        if "total" in result:
            return f"Total: {result['total']}{result.get('label', '')}"
        if "error" in result:
            return f"Erro: {result['error']}"
        return str(result)[:200]
    return str(result)[:200]


def _build_history_text(history: list[dict]) -> str:
    if not history:
        return "(nenhum passo executado ainda)"
    lines = []
    for i, h in enumerate(history, 1):
        lines.append(f"Passo {i}: {h['tool']}({h['params']})")
        lines.append(f"  → {_summarize(h['tool'], h['result'])}")
    return "\n".join(lines)


def _check_repeated(history: list[dict]) -> bool:
    if len(history) < 2:
        return False
    last = history[-1]
    for h in history[:-1]:
        if h["tool"] == last["tool"] and h["params"] == last["params"]:
            return True
    return False


class ReActAgent:
    def __init__(self, question: str, first_tool: str | None = None, first_params: dict | None = None):
        self.question = question
        self.history: list[dict] = []
        self.first_tool = first_tool
        self.first_params = first_params or {}

    def run(self) -> Generator[str, None, None]:
        yield "⚙️ Processando...\n"

        step = 0
        tool_descriptions = _format_tool_descriptions()

        if self.first_tool:
            step += 1
            yield f"  ⚙️ Passo {step}: {self.first_tool}...\n"
            result = _execute_tool(self.first_tool, self.first_params)
            if isinstance(result, dict) and "error" in result:
                yield f"  ❌ {result['error']}\n"
                return
            self.history.append({
                "tool": self.first_tool,
                "params": self.first_params,
                "result": result,
            })
            if _is_empty_result(result):
                new_query = _extract_keywords(self.question) or self.question
                yield f"  ⚙️ Passo {step} sem resultados. Refinando busca...\n"
                result2 = _execute_tool("search_memories", {"query": new_query})
                if not (isinstance(result2, dict) and "error" in result2):
                    self.history.append({
                        "tool": "search_memories",
                        "params": {"query": new_query},
                        "result": result2,
                    })

            if self._has_result():
                yield "✅\n\n"
                yield from synthesize_answer_stream(self.question, self._get_last_result(), self._get_last_tool())
                return

        while step < MAX_STEPS:
            step += 1
            history_text = _build_history_text(self.history)
            user_prompt = (
                f"Histórico de passos:\n{history_text}\n\n"
                f"Pergunta original: {self.question}\n\n"
                f"Qual o próximo passo?"
            )
            try:
                raw = _call_llm(
                    prompt=user_prompt,
                    system=REACT_SYSTEM_PROMPT.format(tool_descriptions=tool_descriptions),
                    max_tokens=400,
                )
                raw = raw.strip()
                start = raw.find("{")
                end = raw.rfind("}")
                if start == -1 or end == -1:
                    raise ValueError("JSON não encontrado")
                data = json.loads(raw[start:end + 1])
            except Exception:
                if self._has_result():
                    break
                yield "❌ Não foi possível processar sua pergunta. Tente reformular.\n"
                return

            if data.get("done"):
                answer = data.get("answer", "")
                if answer:
                    yield f"✅\n\n{answer}\n"
                elif self._has_result():
                    yield "✅\n\n"
                    yield from synthesize_answer_stream(
                        self.question, self._get_last_result(), self._get_last_tool()
                    )
                else:
                    yield "❌ Não encontrei informações suficientes.\n"
                return

            tool = data.get("tool")
            params = data.get("params", {})
            if tool not in TOOL_DEFINITIONS:
                if self._has_result():
                    break
                yield "❌ Ferramenta desconhecida.\n"
                return

            yield f"  ⚙️ Passo {step}: {tool}...\n"
            result = _execute_tool(tool, params)

            if isinstance(result, dict) and "error" in result:
                yield f"  ❌ {result['error']}\n"
                if step >= MAX_STEPS or not self._has_result():
                    return
                continue

            self.history.append({
                "tool": tool,
                "params": params,
                "result": result,
            })

            if _check_repeated(self.history):
                yield "  ⚠️ Detectei repetição. Finalizando com dados disponíveis.\n"
                break

        if self._has_result():
            yield "✅\n\n"
            yield from synthesize_answer_stream(
                self.question, self._get_last_result(), self._get_last_tool()
            )
        else:
            yield "❌ Não foi possível processar sua pergunta.\n"

    def _has_result(self) -> bool:
        return any(
            not _is_empty_result(h["result"])
            for h in self.history
        )

    def _get_last_result(self) -> Any:
        for h in reversed(self.history):
            if not _is_empty_result(h["result"]):
                return h["result"]
        return None

    def _get_last_tool(self) -> str:
        for h in reversed(self.history):
            if not _is_empty_result(h["result"]):
                return h["tool"]
        return ""
