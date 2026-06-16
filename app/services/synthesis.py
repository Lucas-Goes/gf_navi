"""
Synthesis — gera respostas em linguagem natural a partir de resultados de ferramentas.

Cada tipo de resultado (search, list, count, add, detail, etc.) tem um handler
específico que formata a resposta para o usuário.

Fluxo:
  1. synthesize_answer_stream() recebe question + tool_result + tool_name
  2. Roteia para o handler apropriado baseado em tool_name e tipo do resultado
  3. Cada handler decide formato: lista markdown, LLM inline, detalhes formatados
  4. Para resultados complexos (search, details), usa LLM para resumir

Usado em: ReActAgent.run(), AskAgent.ask(), CLI.
"""

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
    """
    Chama o LLM (retorna string completa, sem streaming).

    Usado em: ReActAgent.run() (para decidir próximo passo), e internamente
    em synthesis para respostas não-streaming.

    Raises RuntimeError em caso de timeout ou erro.
    """
    llm = _get_llm()
    try:
        return llm.invoke(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.1)
    except requests.exceptions.Timeout:
        raise RuntimeError("O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor.")
    except Exception as e:
        raise RuntimeError(f"Erro no LLM: {e}")


def _call_llm_stream(prompt: str, system: str = "", max_tokens: int = 2000) -> Generator[str, None, None]:
    """
    Chama o LLM com streaming.

    Usado em: synthesize_answer_stream() para respostas formatadas.
    """
    llm = _get_llm()
    try:
        yield from llm.invoke_stream(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.3)
    except requests.exceptions.Timeout:
        yield "\n\n❌ O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor."
    except Exception as e:
        yield f"\n\n❌ Erro no LLM: {e}"


def _build_search_context(result: list[dict]) -> str:
    """
    Constrói o contexto formatado para o prompt de síntese a partir de
    resultados de busca (search_memories).

    Inclui title, score, fact_type, tags, description, responsáveis, chain
    de correções, warnings e documentos relacionados.

    Usado em: _synthesize_search_result().
    """
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
    """
    Formata detalhes completos de uma memória para exibição.

    Usado em: _synthesize_search_result(), _synthesize_detail_result(),
    _synthesize_add_result(), _synthesize_correct_result().
    """
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


def _format_chain(chain: list[dict]) -> str:
    """
    Formata o histórico de correções (cadeia de supersedes) para exibição.

    Usado em: _synthesize_search_result().
    """
    if not chain or len(chain) < 2:
        return ""
    lines = ["━━━ Histórico de Correções ━━━"]
    n = len(chain)
    for i, item in enumerate(chain):
        v = n - i
        short_id = item["id"][:8]
        dt = (item.get("registration_date") or "")[:10]
        is_active = item.get("is_active", False)
        marker = " ← atual" if is_active else ""
        item_tags = item.get("tags", [])
        tag_str = f" [{', '.join(item_tags)}]" if item_tags else ""
        lines.append(f"v{v} · {short_id} · {dt}{tag_str}{marker}")
        lines.append(f"{item['title']}")
        if not is_active and i > 0:
            prev_v = v + 1
            prev_id = chain[i - 1]["id"][:8]
            lines.append(f"substituída por: v{prev_v} · {prev_id}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── Handlers específicos ──


def _synthesize_help(tool_result: Any, question: str) -> Generator[str, None, None]:
    """Exibe a mensagem de ajuda."""
    result_str = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result
    yield result_str + "\n"


def _synthesize_list(tool_result: list) -> Generator[str, None, None]:
    """Exibe listas de memórias (list_memories) em formato markdown."""
    if not tool_result:
        yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
        return
    yield "📋 **Memórias encontradas:**\n\n"
    for r in tool_result:
        tags_str = f" [`{', '.join(r.get('tags', []))}`]" if r.get("tags") else ""
        yield f"- **{r['title']}** ({r['closing_period']}, {r['fact_type']}){tags_str}\n"
    yield "\n"


def _synthesize_search(tool_result: list, question: str) -> Generator[str, None, None]:
    """
    Exibe resultado de search_memories com LLM inline + detalhes.

    Mostra:
    - Título e resumo da memória mais relevante
    - Histórico de correções (se houver)
    - Detalhes completos
    - Outros resultados (se houver mais de um)
    """
    if not tool_result:
        yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
        return
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
    if len(tool_result) > 1:
        yield "\n🔍 **Outros resultados:**\n\n"
        for r in tool_result[1:]:
            tags_str = f" [`{', '.join(r.get('tags', []))}`]" if r.get("tags") else ""
            yield f"- **{r['title']}** ({r['closing_period']}, {r['fact_type']}, score: {r.get('score', '—')}){tags_str}\n"
        yield "\n"


def _synthesize_detail(mem: dict) -> Generator[str, None, None]:
    """Exibe detalhes de uma memória (get_memory_detail)."""
    if not mem:
        yield "Memória não encontrada.\n"
        return
    yield f"{_format_details(mem)}\n"


def _synthesize_count(result: dict) -> Generator[str, None, None]:
    """Exibe contagem de memórias (count_memories)."""
    total = result.get("total", 0)
    label = result.get("label", "")
    yield f"📊 **{total}** memória(s) encontrada(s){label}.\n"


def _synthesize_add(mem: dict) -> Generator[str, None, None]:
    """Exibe confirmação de adição (add_memory)."""
    yield f"✅ Memória **{mem['title']}** adicionada com sucesso (ID: {mem['id'][:8]}).\n"
    yield f"{_format_details(mem)}\n"


def _synthesize_correct(mem: dict) -> Generator[str, None, None]:
    """Exibe confirmação de correção (correct_memory)."""
    yield f"✅ Memória **{mem['title']}** corrigida com sucesso (ID: {mem['id'][:8]}).\n"
    if mem.get("supersedes_id"):
        yield f"Substitui: {mem['supersedes_id'][:8]}\n"
    yield f"{_format_details(mem)}\n"


def _synthesize_preview_add(preview_data: dict) -> Generator[str, None, None]:
    """Exibe preview de adição (/add)."""
    yield f"**+ Adicionar:** {preview_data['title']}\n\n"
    yield f"**Tipo:** {preview_data['fact_type']}  ·  **Período:** {preview_data['closing_period']}\n"
    tags = preview_data.get("tags", [])
    if tags:
        yield f"**Tags:** `{'`, `'.join(tags)}`\n"
    desc = preview_data.get("description", "")
    if desc:
        yield f"\n**Descrição:** {desc[:500]}\n"


def _synthesize_preview_correct(preview_data: dict) -> Generator[str, None, None]:
    """Exibe preview de correção (/correct)."""
    yield f"**✏️ Corrigir:** {preview_data['title']}\n\n"
    yield f"**Tipo:** {preview_data['fact_type']}  ·  **Período:** {preview_data['closing_period']}\n"
    tags = preview_data.get("tags", [])
    if tags:
        yield f"**Tags:** `{'`, `'.join(tags)}`\n"
    desc = preview_data.get("description", "")
    if desc:
        yield f"\n**Nova descrição:** {desc[:500]}\n"
    if preview_data.get("supersedes_title"):
        yield f"\n*Substitui: {preview_data['supersedes_title']} ({preview_data['supersedes_id'][:8]})*\n"


def _synthesize_periods(result: list) -> Generator[str, None, None]:
    """Exibe lista de períodos (list_periods)."""
    if not result:
        yield "Nenhum período encontrado.\n"
        return
    yield "📅 **Períodos disponíveis:**\n\n"
    for r in result:
        yield f"- **{r['period']}**: {r['count']} memória(s)\n"
    yield "\n"


def _synthesize_types(result: list) -> Generator[str, None, None]:
    """Exibe lista de tipos (list_fact_types)."""
    if not result:
        yield "Nenhum tipo encontrado.\n"
        return
    yield "📋 **Tipos de memória disponíveis:**\n\n"
    for r in result:
        yield f"- **{r['type']}**: {r['count']} memória(s)\n"
    yield "\n"


def _synthesize_sync(result: dict) -> Generator[str, None, None]:
    """Exibe resultado de sincronização (sync_documents)."""
    yield f"📎 Sincronização concluída.\n"
    output = result.get("output", "")
    if output:
        yield f"```\n{output}\n```\n"


def _synthesize_generic(tool_result: Any, question: str) -> Generator[str, None, None]:
    """
    Fallback genérico: serializa o resultado e pede ao LLM para resumir.

    Usado para resultados estruturados que não têm handler específico.
    """
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    prompt = f"Pergunta do usuário: {question}\n\nResultado da consulta:\n{result_str}\n\nResponda em português:"
    yield from _call_llm_stream(prompt=prompt, system=SYNTHESIS_SYSTEM_PROMPT, max_tokens=2000)


# ── Roteador principal ──

def synthesize_answer_stream(question: str, tool_result: Any, tool_name: str = "") -> Generator[str, None, None]:
    """
    Roteia o resultado de uma ferramenta para o handler de síntese apropriado.

    Args:
        question: Pergunta original do usuário.
        tool_result: Resultado retornado pela ferramenta.
        tool_name: Nome da ferramenta executada.

    Yields: Chunks de texto formatado (Markdown simples).

    Usado em: ReActAgent.run(), AskAgent.ask(), cli.py.
    """
    # Help
    if tool_name == "help":
        yield from _synthesize_help(tool_result, question)
        return

    # Preview de adição / correção
    if tool_name == "add_memory_preview":
        yield from _synthesize_preview_add(tool_result)
        return
    if tool_name == "correct_memory_preview":
        yield from _synthesize_preview_correct(tool_result)
        return

    # Busca semântica
    if tool_name == "search_memories" and isinstance(tool_result, list):
        yield from _synthesize_search(tool_result, question)
        return

    # Listagens
    if tool_name == "list_memories" and isinstance(tool_result, list):
        yield from _synthesize_list(tool_result)
        return

    # Detalhe
    if tool_name == "get_memory_detail":
        yield from _synthesize_detail(tool_result)
        return

    # Contagem
    if tool_name == "count_memories":
        yield from _synthesize_count(tool_result)
        return

    # Adição / Correção
    if tool_name == "add_memory":
        yield from _synthesize_add(tool_result)
        return
    if tool_name == "correct_memory":
        yield from _synthesize_correct(tool_result)
        return

    # Períodos e tipos
    if tool_name == "list_periods" and isinstance(tool_result, list):
        yield from _synthesize_periods(tool_result)
        return
    if tool_name == "list_fact_types" and isinstance(tool_result, list):
        yield from _synthesize_types(tool_result)
        return

    # Sincronização
    if tool_name == "sync_documents":
        yield from _synthesize_sync(tool_result)
        return

    # Fallback: qualquer resultado não-listado
    if isinstance(tool_result, list) and not tool_result:
        yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
        return

    yield from _synthesize_generic(tool_result, question)
