"""
Navi CLI — Cérebro Institucional

Uso:
  python cli.py add <texto>        Adicionar nova memória
  python cli.py correct <id> <texto>  Corrigir memória (com ID explícito)
  python cli.py correct [-i ID] <texto>  Corrigir memória (ID opcional, infere se omitido)
  python cli.py ask <pergunta>     Consultar memórias
  python cli.py search <termo>     Buscar memórias (sem LLM)
  python cli.py list [--type T] [--period YYYY-MM]  Listar memórias
  python cli.py get <id>           Ver detalhes de uma memória
  python cli.py sync-docs          Sincronizar documentos
  python cli.py provider [nome]    Ver/trocar provider (nvidia, bedrock, ollama)
  python cli.py help               Mostrar esta ajuda
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.models import Preview
from app.services.llm import create_provider
from app.services.parser import ParserService
from app.services.synthesizer import SynthesizerService
from app.services.ingestion import IngestionService
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore
from app.db_viewer import _db_conn, cmd_db as db_cmd
from app.doc_sync import cmd_sync_docs, _link_documents


def get_services():
    sqlite = SQLiteStore(settings.sqlite_path)
    sqlite.run_migrations()
    vector = VectorStore(settings.chroma_path, settings.embedding_model)
    llm = create_provider(settings)
    parser = ParserService(llm)
    ingestion = IngestionService(sqlite, vector)
    search = SearchService(sqlite, vector)
    synthesizer = SynthesizerService(llm)
    return sqlite, vector, parser, ingestion, search, synthesizer


def cmd_add(args, services):
    sqlite, _, parser, ingestion, _, _ = services
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    print("\n🧠 Analisando texto e extraindo campos...\n")
    try:
        preview = parser.parse(text)
    except Exception as e:
        print(f"❌ Erro ao processar: {e}")
        print("   Verifique se o LLM está configurado corretamente.")
        sys.exit(1)
    if not preview:
        print("❌ Não foi possível extrair os campos.")
        sys.exit(1)

    ingestion.store_preview(preview)
    _show_preview(preview)

    if preview.confidence_score < 0.6:
        print("\n⚠️  Baixa confiança na extração. Deseja editar e tentar novamente?")
    if preview.is_correction and preview.supersedes_id:
        print(
            f"\n⚠️  CORREÇÃO DETECTADA: esta memória substituirá"
            f" {preview.supersedes_id[:8]}..."
        )
        if preview.superseded_memory_title:
            print(f"   Título original: {preview.superseded_memory_title}")

    confirm = input("\n❓ Confirmar salvamento? (s/N): ").strip().lower()
    if confirm == "s":
        memory = ingestion.confirm(preview)
        print(f"\n✅ Memória salva! ID: {memory.id}")
        print(f"   Título: {memory.title}")
        _link_documents(sqlite, text, memory.id, preview.supersedes_id)
    else:
        ingestion.remove_preview(preview.preview_id)
        print("⏭️  Cancelado.")


CORRECT_PROMPT = """Você é um analista de memória institucional. O usuário quer atualizar uma memória existente com novas informações.

MEMÓRIA ORIGINAL:
Título: {title}
Tipo: {fact_type}
Período: {closing_period}
Descrição: {description}
Decidido por: {decided_by}
Solicitado por: {requested_by}
Aprovado por: {approved_by}

INSTRUÇÃO DO USUÁRIO:
{correction_text}

Regras:
1. Incorpore as novas informações na descrição de forma natural e coesa, reescrevendo o texto completo — NÃO se limite a concatenar.
2. Ignore meta-instruções do tipo "adicione isso", "inclua aquilo", "atualize para". Extraia apenas o conteúdo factual relevante.
3. Se a instrução não alterar um campo específico, mantenha o valor original.
4. Se a instrução mencionar novos responsáveis (decidido/solicitado/aprovado), atualize os campos correspondentes.
5. Preserve o formato institucional e profissional.

Gere o JSON completo da nova versão:
{json_schema}"""


def _infer_memory(sqlite, search, text: str):
    """Tenta inferir qual memória o usuário quer corrigir via busca semântica.
    Prefere memórias ativas; se a melhor correspondência for obsoleta,
    sugere a versão que a substitui."""
    results = search.hybrid_search(text, top_k=3)
    if not results:
        return None

    for r in results[:3]:
        if r.memory.is_active:
            return r.memory

    best = results[0].memory
    if best.superseded_by:
        superseder = sqlite.get_memory(best.superseded_by)
        if superseder and superseder.is_active:
            print(f"   ⚠️  '{best.title}' já foi atualizada posteriormente.")
            print(f"   💡 Redirecionando para a versão mais recente: {superseder.title} ({superseder.id[:8]})")
            return superseder

    if best.is_active:
        return best

    print(f"   ⚠️  '{best.title}' está inativa e não possui uma versão substituta.")
    print(f"   ⚠️  Corrigir uma memória inativa pode causar inconsistências no histórico.")
    return best


def cmd_correct(args, services):
    sqlite, _, parser, ingestion, search, _ = services
    memory_id = args.id
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    if memory_id:
        old = sqlite.get_memory(memory_id)
        if not old:
            print(f"❌ Memória {memory_id} não encontrada.")
            sys.exit(1)
    else:
        first_word = args.text[0].lower()
        if len(first_word) == 8 and all(c in "0123456789abcdef" for c in first_word):
            candidate = sqlite.get_memory(first_word)
            if candidate:
                memory_id = first_word
                text = " ".join(args.text[1:])
                old = candidate

        if not memory_id:
            print("\n🔍 Nenhum ID informado. Buscando memória mais relevante...\n")
            old = _infer_memory(sqlite, search, text)
            if not old:
                print("❌ Não foi possível identificar qual memória corrigir.")
                sys.exit(1)
            print(f"🔍 Memória identificada:\n")
            print(f"   ID: {old.id[:8]}")
            print(f"   Título: {old.title}")
            print(f"   Tipo: {old.fact_type.value}")
            print(f"   Período: {old.closing_period}")
            print(f"   Descrição: {old.description[:200]}{'...' if len(old.description) > 200 else ''}")
            print()
            conf = input("❓ É esta memória que deseja corrigir? (s/N): ").strip().lower()
            if conf != "s":
                print("⏭️  Cancelado.")
                return

    if not old.is_active and old.superseded_by:
        latest = old
        chain_len = 0
        max_depth = 50
        while latest and latest.superseded_by and chain_len < max_depth:
            latest = sqlite.get_memory(latest.superseded_by)
            chain_len += 1
        if chain_len >= max_depth:
            print("⚠️  Cadeia de correção muito longa (>50). Entre em contato com o suporte.")
            return
        if latest:
            print(f"⚠️  Esta memória já foi atualizada {chain_len} vez(es).")
            print(f"   A versão mais recente é: {latest.title} ({latest.id[:8]})")
            if latest.registration_date:
                dt = latest.registration_date[:10]
                print(f"   Registrada em: {dt}")
            conf = input("   A correção será aplicada sobre a versão mais recente. Continuar? (S/n): ").strip().lower()
            if conf == "n":
                print("⏭️  Cancelado.")
                return
            old = latest

    print(f"\n📌 Memória original: {old.title} ({old.id[:8]})\n")

    json_schema = """{
  "title": "string",
  "fact_type": "rule_change | decision | implementation | incident | other",
  "closing_period": "YYYY-MM",
  "description": "string",
  "decided_by": "string | null",
  "requested_by": "string | null",
  "approved_by": "string | null",
  "metadata": "object | null",
  "confidence_score": 0.0-1.0
}"""

    prompt = CORRECT_PROMPT.format(
        title=old.title,
        fact_type=old.fact_type.value,
        closing_period=old.closing_period,
        description=old.description,
        decided_by=old.decided_by or "",
        requested_by=old.requested_by or "",
        approved_by=old.approved_by or "",
        correction_text=text,
        json_schema=json_schema,
    )

    print("🧠 Fazendo merge do conteúdo com a correção...\n")
    try:
        content = parser.llm.invoke(
            prompt=prompt,
            max_tokens=2000,
            temperature=0.1,
        )
        if not content or not content.strip():
            print("❌ LLM retornou resposta vazia")
            print(f"   Prompt enviado:\n{prompt[:500]}...")
            return
        preview = parser._parse_response(content, f"{old.description}\n{text}")
    except json.JSONDecodeError as e:
        print(f"❌ Erro ao interpretar JSON do LLM: {e}")
        print(f"   Resposta bruta: {content[:300]}")
        return
    except Exception as e:
        print(f"❌ Erro ao processar: {e}")
        return

    preview.supersedes_id = old.id
    preview.is_correction = True
    ingestion.store_preview(preview)

    _show_preview(preview)
    print(f"\n⚠️  Esta memória SUBSTITUIRÁ {old.id[:8]} - {old.title}")

    confirm = input("\n❓ Confirmar correção? (s/N): ").strip().lower()
    if confirm == "s":
        memory = ingestion.confirm(preview)
        print(f"\n✅ Memória corrigida! Nova ID: {memory.id}")
        print(f"   Título: {memory.title}")
        _link_documents(sqlite, text, memory.id, preview.supersedes_id)
    else:
        ingestion.remove_preview(preview.preview_id)
        print("⏭️  Cancelado.")


def cmd_ask(args, services):
    from app.services.ask_agent import AskAgent

    _, vector, _, _, search, _ = services
    question = " ".join(args.text) if isinstance(args.text, list) else args.text

    agent = AskAgent(search, vector)

    print()
    for chunk in agent.ask(question):
        print(chunk, end="", flush=True)
    print()


def cmd_search(args, services):
    _, _, _, _, search, _ = services
    query = " ".join(args.text) if isinstance(args.text, list) else args.text

    print("\n🔍 Buscando...\n")
    results = search.hybrid_search(
        query,
        top_k=args.top_k or 5,
        fact_type=args.type,
        closing_period=args.period,
    )

    if not results:
        print("❌ Nenhuma memória encontrada.")
        return

    for i, r in enumerate(results, 1):
        print(f"--- Resultado {i} (score: {r.score:.3f}) ---")
        _print_result(r)


def cmd_list(args, services):
    sqlite, _, _, _, _, _ = services
    memories = sqlite.search_memories_sql(
        fact_type=args.type,
        closing_period=args.period,
        limit=args.limit or 50,
    )

    if not memories:
        print("❌ Nenhuma memória encontrada.")
        return

    print(f"\n📋 {len(memories)} memória(s) encontrada(s):\n")
    for m in memories:
        status = " [CORRIGIDA]" if m.superseded_by else ""
        print(f"  {m.id[:8]}  {m.closing_period}  {m.fact_type.value:15s}  {m.title}{status}")


def cmd_get(args, services):
    sqlite, _, _, _, _, _ = services
    memory = sqlite.get_memory(args.id)
    if not memory:
        print("❌ Memória não encontrada.")
        sys.exit(1)

    print(f"\n📌 ID: {memory.id}")
    print(f"   Título: {memory.title}")
    print(f"   Tipo: {memory.fact_type.value}")
    print(f"   Período: {memory.closing_period}")
    print(f"   Descrição:\n{textwrap.indent(memory.description, '      ')}")
    print(f"   Decidido por: {memory.decided_by or '—'}")
    print(f"   Solicitado por: {memory.requested_by or '—'}")
    print(f"   Aprovado por: {memory.approved_by or '—'}")
    print(f"   Data registro: {memory.registration_date}")
    print(f"   Registrado por: {memory.registered_by}")
    print(f"   Ativo: {memory.is_active}")
    if memory.supersedes_id:
        print(f"   Substitui: {memory.supersedes_id}")
    if memory.superseded_by:
        print(f"   Substituído por: {memory.superseded_by}")

    docs = sqlite.get_documents_by_memory(memory.id)
    if docs:
        print(f"\n   📎 Documentos relacionados ({len(docs)}):")
        for d in docs:
            print(f"      - {d.title} ({d.filename})")


def _show_preview(preview: Preview):
    print(f"📝 PREVIEW:")
    print(f"   Título: {preview.title}")
    print(f"   Tipo: {preview.fact_type.value}")
    print(f"   Período: {preview.closing_period}")
    print(f"   Descrição:\n{textwrap.indent(preview.description, '      ')}")
    print(f"   Decidido por: {preview.decided_by or '—'}")
    print(f"   Solicitado por: {preview.requested_by or '—'}")
    print(f"   Aprovado por: {preview.approved_by or '—'}")
    print(f"   Confiança: {preview.confidence_score:.1%}")
    if preview.metadata:
        print(f"   Metadados: {preview.metadata}")


def _print_result(r):
    m = r.memory
    print(f"  [{m.id[:8]}] {m.title} ({m.closing_period})")
    print(f"         Tipo: {m.fact_type.value} | Score: {r.score:.3f}")
    if r.warnings:
        for w in r.warnings:
            print(f"         ⚠️  {w}")
    print(f"         {m.description[:200]}{'...' if len(m.description) > 200 else ''}")
    print()


PROVIDERS_FILE = Path(__file__).parent / "providers.json"


def cmd_provider(args, services):
    providers = json.loads(PROVIDERS_FILE.read_text())

    if not args.name:
        current = settings.llm_provider
        print(f"\n🔌 Provider atual: {current}")
        print(f"   Modelo: {settings.llm_model_id}")
        print(f"   Endpoint: {settings.llm_endpoint_url or '—'}")
        print(f"\n   Providers disponíveis: {', '.join(providers.keys())}")
        print(f"   Use: python cli.py provider <nome>\n")
        return

    name = args.name.lower()
    if name not in providers:
        print(f"❌ Provider '{name}' não encontrado.")
        print(f"   Disponíveis: {', '.join(providers.keys())}")
        return

    conf = providers[name]
    env_path = Path(__file__).parent / ".env"

    key_mapping = {
        "llm_provider": "LLM_PROVIDER",
        "llm_model_id": "LLM_MODEL_ID",
        "llm_endpoint_url": "LLM_ENDPOINT_URL",
        "llm_api_key": "LLM_API_KEY",
        "aws_region": "AWS_REGION",
        "aws_profile": "AWS_PROFILE",
    }

    env_keys = set(key_mapping.values())
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key, _, _ = stripped.partition("=")
        if key.strip() in env_keys:
            continue
        new_lines.append(line)

    for lk, uk in key_mapping.items():
        if lk in conf:
            new_lines.append(f"{uk}={conf[lk]}")

    env_path.write_text("\n".join(new_lines) + "\n")

    print(f"\n✅ Provider trocado para: {name}")
    print(f"   Modelo: {conf.get('llm_model_id', '—')}")
    print(f"   Endpoint: {conf.get('llm_endpoint_url', '—')}")
    print("   Reinicie o CLI para aplicar as mudanças.\n")


def _cli_help():
    pad = 23
    Console().print(
        "\n[bold cyan]Navi — Cérebro Institucional[/bold cyan]\n"
        f"  [cyan]add <texto>[/cyan]{" " * (pad - 11)} — Adicionar nova memória\n"
        f"  [cyan]ask <perg>[/cyan]{" " * (pad - 10)} — Perguntar sobre memórias\n"
        f"  [cyan]search <termo>[/cyan]{" " * (pad - 14)} — Buscar memórias (sem LLM)\n"
        f"  [cyan]list[/cyan]{" " * (pad - 4)} — Listar memórias\n"
        f"  [cyan]get <id>[/cyan]{" " * (pad - 8)} — Detalhes de uma memória\n"
        f"  [cyan]correct [-i ID] <texto>[/cyan]{" " * (pad - 23)} — Corrigir memória (ID opcional)\n"
        f"  [cyan]sync-docs[/cyan]{" " * (pad - 9)} — Sincronizar documentos\n"
        f"  [cyan]provider[/cyan]{" " * (pad - 8)} — Ver/trocar provider LLM\n"
        f"  [cyan]db <subcmd>[/cyan]{" " * (pad - 11)} — Visualizar/exportar banco\n"
        f"  [cyan]help[/cyan]{" " * (pad - 4)} — Mostrar esta ajuda\n"
        "\n[dim]Consulte [cyan]db help[/cyan] para detalhes do database viewer.[/dim]\n"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Navi — Cérebro Institucional (CLI)"
    )
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Adicionar nova memória")
    p_add.add_argument("text", nargs="+", help="Texto da memória")

    p_ask = sub.add_parser("ask", help="Perguntar sobre memórias")
    p_ask.add_argument("text", nargs="+", help="Pergunta em linguagem natural")
    p_ask.add_argument("--type", help="Filtrar por tipo")
    p_ask.add_argument("--period", help="Filtrar por período (YYYY-MM)")

    p_search = sub.add_parser("search", help="Buscar memórias (sem LLM)")
    p_search.add_argument("text", nargs="+", help="Termo de busca")
    p_search.add_argument("--type", help="Filtrar por tipo")
    p_search.add_argument("--period", help="Filtrar por período (YYYY-MM)")
    p_search.add_argument("--top-k", type=int, default=5)

    p_list = sub.add_parser("list", help="Listar memórias")
    p_list.add_argument("--type", help="Filtrar por tipo")
    p_list.add_argument("--period", help="Filtrar por período (YYYY-MM)")
    p_list.add_argument("--limit", type=int, default=50)

    p_get = sub.add_parser("get", help="Ver detalhes de uma memória")
    p_get.add_argument("id", help="ID da memória")

    sub.add_parser("sync-docs", help="Sincronizar documentos")

    p_correct = sub.add_parser("correct", help="Corrigir memória (ID opcional; se omitido, infere automaticamente)")
    p_correct.add_argument("-i", "--id", help="ID da memória a corrigir (opcional)")
    p_correct.add_argument("text", nargs="+", help="Texto da correção")

    p_prov = sub.add_parser("provider", help="Ver/trocar provider LLM")
    p_prov.add_argument("name", nargs="?", help="nvidia, bedrock, ollama")

    sub.add_parser("help", help="Mostrar esta ajuda")

    p_db = sub.add_parser("db", help="Visualizar/exportar banco de dados")
    p_db.add_argument("command_db", nargs=argparse.REMAINDER, default=[], help="Subcomando + argumentos (ex: memories, memory <id>, export json)")
    p_db.add_argument("--all", action="store_true", help="Incluir registros inativos")
    p_db.add_argument("--limit", type=int, default=100, help="Limite de linhas")
    p_db.add_argument("--format", choices=["json", "csv"], default="json", help="Formato de exportação")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "add": cmd_add,
        "correct": cmd_correct,
        "ask": cmd_ask,
        "search": cmd_search,
        "list": cmd_list,
        "get": cmd_get,
        "sync-docs": cmd_sync_docs,
        "provider": cmd_provider,
        "help": lambda a, s: _cli_help(),
    }

    if args.command == "db":
        conn = _db_conn()
        try:
            db_cmd(conn, args.command_db, args.all, args.limit, args.format)
        finally:
            conn.close()
    elif args.command in ("provider", "help"):
        commands[args.command](args, None)
    else:
        services = get_services()
        if args.command == "sync-docs":
            sqlite, vector, _, _, _, _ = services
            cmd_sync_docs(sqlite, vector)
        else:
            commands[args.command](args, services)


if __name__ == "__main__":
    main()
