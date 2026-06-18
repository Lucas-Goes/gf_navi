from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Generator

import requests

from app.services.tool_helpers import _get_llm
from app.prompts import load_prompt


def _call_llm_stream(prompt: str, system: str = "", max_tokens: int = 2000) -> Generator[str, None, None]:
    llm = _get_llm()
    try:
        yield from llm.invoke_stream(prompt=prompt, system_prompt=system, max_tokens=max_tokens, temperature=0.1)
    except requests.exceptions.Timeout:
        yield "\n\n❌ O serviço LLM não respondeu a tempo. Verifique se a API key é válida ou tente outro provedor."
    except Exception as e:
        yield f"\n\n❌ Erro no LLM: {e}"


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


def _format_chain(chain: list[dict]) -> str:
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


def _synthesize_help(tool_result: Any, question: str) -> Generator[str, None, None]:
    result_str = json.dumps(tool_result, ensure_ascii=False) if not isinstance(tool_result, str) else tool_result
    yield result_str + "\n"


def _synthesize_detail(mem: dict) -> Generator[str, None, None]:
    if not mem:
        yield "Memória não encontrada.\n"
        return
    yield f"{_format_details(mem)}\n"


def _synthesize_count(result: dict) -> Generator[str, None, None]:
    total = result.get("total", 0)
    label = result.get("label", "")
    if total == 0:
        yield f"📊 Nenhuma memória encontrada{label}.\n"
        return
    yield f"📊 **{total}** memória(s) encontrada(s){label}.\n"


def _synthesize_add(mem: dict) -> Generator[str, None, None]:
    yield f"✅ Memória **{mem['title']}** adicionada com sucesso (ID: {mem['id'][:8]}).\n"
    yield f"{_format_details(mem)}\n"


def _synthesize_correct(mem: dict) -> Generator[str, None, None]:
    yield f"✅ Memória **{mem['title']}** corrigida com sucesso (ID: {mem['id'][:8]}).\n"
    if mem.get("supersedes_id"):
        yield f"Substitui: {mem['supersedes_id'][:8]}\n"
    yield f"{_format_details(mem)}\n"


def _synthesize_preview_add(preview_data: dict) -> Generator[str, None, None]:
    yield f"**+ Adicionar:** {preview_data['title']}\n\n"
    yield f"**Tipo:** {preview_data['fact_type']}  ·  **Período:** {preview_data['closing_period']}\n"
    tags = preview_data.get("tags", [])
    if tags:
        yield f"**Tags:** `{'`, `'.join(tags)}`\n"
    desc = preview_data.get("description", "")
    if desc:
        yield f"\n**Descrição:** {desc[:500]}\n"


def _synthesize_preview_correct(preview_data: dict) -> Generator[str, None, None]:
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
    if not result:
        yield "Nenhum período encontrado.\n"
        return
    yield "📅 **Períodos disponíveis:**\n\n"
    for r in result:
        yield f"- **{r['period']}**: {r['count']} memória(s)\n"
    yield "\n"


def _synthesize_types(result: list) -> Generator[str, None, None]:
    if not result:
        yield "Nenhum tipo encontrado.\n"
        return
    yield "📋 **Tipos de memória disponíveis:**\n\n"
    for r in result:
        yield f"- **{r['type']}**: {r['count']} memória(s)\n"
    yield "\n"


def _synthesize_sync(result: dict) -> Generator[str, None, None]:
    yield "📎 Sincronização concluída.\n"
    output = result.get("output", "")
    if output:
        yield f"```\n{output}\n```\n"


def _synthesize_generic(tool_result: Any, question: str) -> Generator[str, None, None]:
    count = len(tool_result) if isinstance(tool_result, list) else 1
    result_str = json.dumps(tool_result, ensure_ascii=False, indent=2)
    prompt = load_prompt("synthesis_generic", question=question, result=result_str, count=count)
    yield from _call_llm_stream(prompt=prompt, system=load_prompt("synthesis_system"), max_tokens=2000)


def synthesize_answer_stream(question: str, tool_result: Any, tool_name: str = "") -> Generator[str, None, None]:
    dropped = None
    if isinstance(tool_result, dict):
        dropped = tool_result.pop("_dropped_params", None)
        tool_result.pop("_from_tool", None)
        inner = tool_result.get("results") or tool_result.get("result")
        if inner is not None:
            tool_result = inner
    if dropped:
        yield f"⚠️ *'{', '.join(dropped)}' ignorado — {tool_name} não aceita esse parâmetro.*\n\n"

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

    # Busca semântica → LLM responde em linguagem natural (filtra falsos positivos, conta, resume)
    if tool_name == "search_memories" and isinstance(tool_result, list):
        if not tool_result:
            yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
            return
        yield from _synthesize_generic(tool_result, question)
        return

    # Listagem → LLM responde em linguagem natural
    if tool_name == "list_memories" and isinstance(tool_result, list):
        if not tool_result:
            yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
            return
        yield from _synthesize_generic(tool_result, question)
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

    # Fallback
    if isinstance(tool_result, list) and not tool_result:
        yield "Não encontrei registros sobre isso nas memórias institucionais.\n"
        return

    yield from _synthesize_generic(tool_result, question)
