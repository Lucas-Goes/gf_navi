from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Generator

import requests

from app.services.tools import _get_llm

SYNTHESIS_SYSTEM_PROMPT = """Você é Navi, assistente de memória institucional do maior banco da América Latina. Responda em português, com segurança e concisão.

Com base no contexto de memórias fornecido, escreva APENAS 1-2 frases respondendo DIRETAMENTE à pergunta do usuário. Use apenas a PRIMEIRA memória (a mais relevante). Não inclua formatação, títulos, detalhes ou blocos — apenas a resposta natural.

REGRAS:
- NUNCA misture informações de memórias diferentes em uma mesma frase.
- Se houver mais de uma memória com TAGS diferentes, elas são sobre assuntos distintos — NÃO as misture.
- A primeira memória do contexto é a única referência para a resposta.
- Se o usuário perguntar sobre "ponto", "algo", "teve" — responda com o que encontrar.
- Se não houver dados relevantes, diga apenas que não encontrou."""


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


def _build_search_context(result: list[dict]) -> str:
    if not result:
        return "(nenhum resultado)"
    parts = []
    for r in result:
        lines = [
            "---",
            f"id: {r['id']}",
            f"title: {r['title']}",
            f"score: {r.get('score', '—')}",
            f"fact_type: {r['fact_type']}",
            f"closing_period: {r['closing_period']}",
            f"tags: {', '.join(r.get('tags', [])) or '—'}",
            f"description: {r.get('description', '')[:500]}",
            f"decided_by: {r.get('decided_by') or '—'}",
            f"requested_by: {r.get('requested_by') or '—'}",
            f"approved_by: {r.get('approved_by') or '—'}",
            f"is_active: {r.get('is_active', True)}",
            f"registered_by: {r.get('registered_by') or '—'}",
            f"registration_date: {r.get('registration_date') or '—'}",
            f"supersedes_id: {r.get('supersedes_id') or '—'}",
            f"superseded_by: {r.get('superseded_by') or '—'}",
        ]
        chain = r.get("correction_chain")
        if chain:
            lines.append("correction_chain:")
            for item in chain:
                item_tags = item.get('tags', [])
                tag_str = f" tags={','.join(item_tags)}" if item_tags else ""
                lines.append(f"  - id={item['id']} title={item['title']} date={item.get('registration_date','')} active={item['is_active']}{tag_str}")
        if r.get("warnings"):
            lines.append(f"warnings: {'; '.join(r['warnings'])}")
        if r.get("documents"):
            for d in r["documents"]:
                lines.append(f"doc: {d['title']} ({d['filename']})")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _format_details(mem: dict) -> str:
    dt = mem.get("registration_date", "")
    if dt and len(dt) >= 19:
        try:
            dt = datetime.fromisoformat(dt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    status = "✅ Ativa" if mem.get("is_active", True) else "❌ Inativa"
    tags = mem.get("tags", [])
    tags_str = f"🏷️  {', '.join(tags)}" if tags else ""
    lines = [
        "━━━ Detalhes ━━━",
        f"ID        {mem['id']}",
        f"Tipo      {mem['fact_type']}",
        f"Período   {mem['closing_period']}",
        f"Situação  {status}",
    ]
    if tags_str:
        lines.append(tags_str)
    lines.extend([
        f"Registrado por  {mem.get('registered_by') or '—'}",
        f"Data      {dt}",
        f"Decidido por  {mem.get('decided_by') or '—'}",
        f"Solicitado por  {mem.get('requested_by') or '—'}",
        f"Aprovado por  {mem.get('approved_by') or '—'}",
    ])
    return "\n".join(lines)
    return "\n".join(lines)


def _format_chain(chain: list[dict]) -> str:
    if not chain or len(chain) < 2:
        return ""
    lines = ["━━━ Histórico de Correções ━━━"]
    items = list(reversed(chain))
    for i, item in enumerate(items):
        v = i + 1
        short_id = item["id"][:8]
        dt = (item.get("registration_date") or "")[:10]
        marker = " ← atual" if i == len(items) - 1 else ""
        item_tags = item.get("tags", [])
        tag_str = f" [{', '.join(item_tags)}]" if item_tags else ""
        lines.append(f"v{v} · {short_id} · {dt}{tag_str}")
        lines.append(f"{item['title']}{marker}")
        if i < len(items) - 1:
            lines.append(f"└─ substituída por v{v + 1}")
    return "\n".join(lines)


def synthesize_answer_stream(question: str, tool_result: Any, tool_name: str = "") -> Generator[str, None, None]:
    if tool_name == "search_memories" and isinstance(tool_result, list) and tool_result:
        mem = tool_result[0]
        chain = mem.get("correction_chain", [])
        context = _build_search_context(tool_result)
        prompt = (
            f"Contexto das memórias institucionais:\n{context}\n\n"
            f"Pergunta do usuário:\n{question}\n\n"
            f"Resposta (apenas 1-2 frases, sem formatação):"
        )
        answer_parts = list(_call_llm_stream(
            prompt=prompt, system=SYNTHESIS_SYSTEM_PROMPT, max_tokens=500
        ))
        answer = "".join(answer_parts).strip().strip('"').strip("'")
        title = mem["title"]
        header = f"📌 **{title}**\n"
        chain_block = _format_chain(chain)
        details_block = _format_details(mem)
        yield f"\n{header}\n{answer}\n\n"
        if chain_block:
            yield f"{chain_block}\n\n"
        yield f"{details_block}\n"
    elif isinstance(tool_result, list) and not tool_result:
        yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
    else:
        result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
        prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
        yield from _call_llm_stream(prompt=prompt, system=SYNTHESIS_SYSTEM_PROMPT, max_tokens=2000)
