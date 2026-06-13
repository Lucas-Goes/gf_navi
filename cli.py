"""
Navi CLI — Cérebro Institucional

Uso:
  python cli.py add <texto>        Adicionar nova memória
  python cli.py correct <id> <texto>  Corrigir memória (faz merge automático)
  python cli.py ask <pergunta>     Consultar memórias
  python cli.py search <termo>     Buscar memórias (sem LLM)
  python cli.py list [--type T] [--period YYYY-MM]  Listar memórias
  python cli.py get <id>           Ver detalhes de uma memória
  python cli.py sync-docs          Sincronizar documentos
  python cli.py provider [nome]    Ver/trocar provider (nvidia, bedrock, ollama)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.models import Document, Preview
from app.services.llm import create_provider
from app.services.parser import ParserService
from app.services.synthesizer import SynthesizerService
from app.services.ingestion import IngestionService
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


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
    _, _, parser, ingestion, _, _ = services
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    print("\n🧠 Analisando texto e extraindo campos...\n")
    try:
        preview = parser.parse(text)
    except Exception as e:
        print(f"❌ Erro ao processar: {e}")
        print("   Verifique se o LLM está configurado corretamente.")
        return
    if not preview:
        print("❌ Não foi possível extrair os campos.")
        return

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
    else:
        ingestion.remove_preview(preview.preview_id)
        print("⏭️  Cancelado.")


CORRECT_PROMPT = """Você é um analista de memória institucional. O usuário quer corrigir/atualizar uma memória existente.

MEMÓRIA ORIGINAL:
Título: {title}
Tipo: {fact_type}
Período: {closing_period}
Descrição: {description}
Decidido por: {decided_by}
Solicitado por: {requested_by}
Aprovado por: {approved_by}

TEXTO DE CORREÇÃO DO USUÁRIO:
{correction_text}

Gere uma nova versão completa da memória (incorporando a correção) no JSON abaixo.
Preencha TODOS os campos. Se um campo não foi alterado, mantenha o valor original.
{json_schema}"""


def cmd_correct(args, services):
    sqlite, _, parser, ingestion, _, _ = services
    memory_id = args.id
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    old = sqlite.get_memory(memory_id)
    if not old:
        print(f"❌ Memória {memory_id} não encontrada.")
        return

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
    print(f"\n⚠️  Esta memória SUBSTITUIRÁ {memory_id[:8]} - {old.title}")

    confirm = input("\n❓ Confirmar correção? (s/N): ").strip().lower()
    if confirm == "s":
        memory = ingestion.confirm(preview)
        print(f"\n✅ Memória corrigida! Nova ID: {memory.id}")
        print(f"   Título: {memory.title}")
    else:
        ingestion.remove_preview(preview.preview_id)
        print("⏭️  Cancelado.")


def cmd_ask(args, services):
    _, _, _, _, search, synthesizer = services
    question = " ".join(args.text) if isinstance(args.text, list) else args.text

    print("\n🔍 Buscando memórias relevantes...\n")
    results = search.hybrid_search(
        question,
        top_k=5,
        fact_type=args.type,
        closing_period=args.period,
    )

    if not results:
        print("❌ Nenhuma memória encontrada.")
        return

    for r in results:
        _print_result(r)

    print("\n🤖 Sintetizando resposta...\n")
    try:
        answer = synthesizer.synthesize(question, results)
        print(answer)
    except Exception as e:
        print(f"❌ Erro ao consultar LLM: {e}")
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
        return

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


def cmd_sync_docs(args, services):
    sqlite, vector, _, _, _, _ = services
    docs_path = Path(settings.documents_path)
    docs_path.mkdir(parents=True, exist_ok=True)

    files = []
    for ext in ("*.pdf", "*.txt", "*.md"):
        files.extend(docs_path.glob(ext))

    if not files:
        print("📂 Nenhum documento encontrado em data/documents/")
        return

    print(f"\n📂 Processando {len(files)} arquivo(s)...\n")

    for filepath in files:
        if sqlite.document_exists(filepath.name):
            print(f"  ⏭️  {filepath.name} já processado, pulando.")
            continue

        print(f"  📄 {filepath.name}...", end=" ")

        source_type = filepath.suffix.lstrip(".")
        title = filepath.stem
        content = ""

        if source_type == "pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(filepath))
                content = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception as e:
                print(f"erro: {e}")
                continue
        else:
            content = filepath.read_text(encoding="utf-8", errors="replace")

        chunk_size = 1000
        chunks = [
            content[i : i + chunk_size]
            for i in range(0, len(content), chunk_size)
        ]

        for ci, chunk in enumerate(chunks):
            doc = Document(
                filename=filepath.name,
                source_type=source_type,
                title=title,
                content=chunk,
                chunk_index=ci,
            )
            sqlite.insert_document(doc)
            vector.add_document(
                doc_id=doc.id,
                title=title,
                content=chunk,
                metadata={
                    "document_id": doc.id,
                    "title": title,
                    "source_type": source_type,
                    "chunk_index": ci,
                },
            )

        print(f"ok ({len(chunks)} chunk(s))")

    print("\n✅ Sincronização concluída!")


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

    lines = env_path.read_text().splitlines() if env_path.exists() else []
    env_map = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        env_map[key.strip()] = val.strip()

    for lk, uk in key_mapping.items():
        if lk in conf:
            env_map[uk] = conf[lk]
        else:
            env_map.pop(uk, None)

    new_lines = [f"{k}={v}" for k, v in env_map.items()]
    env_path.write_text("\n".join(new_lines) + "\n")

    print(f"\n✅ Provider trocado para: {name}")
    print(f"   Modelo: {conf.get('llm_model_id', '—')}")
    print(f"   Endpoint: {conf.get('llm_endpoint_url', '—')}")
    print("   Reinicie o CLI para aplicar as mudanças.\n")


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

    p_correct = sub.add_parser("correct", help="Corrigir memória (faz merge com original)")
    p_correct.add_argument("id", help="ID da memória a corrigir")
    p_correct.add_argument("text", nargs="+", help="Texto da correção")

    p_prov = sub.add_parser("provider", help="Ver/trocar provider LLM")
    p_prov.add_argument("name", nargs="?", help="nvidia, bedrock, ollama")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    services = get_services()

    commands = {
        "add": cmd_add,
        "correct": cmd_correct,
        "ask": cmd_ask,
        "search": cmd_search,
        "list": cmd_list,
        "get": cmd_get,
        "sync-docs": cmd_sync_docs,
        "provider": cmd_provider,
    }

    if args.command == "provider":
        commands[args.command](args, None)
    else:
        commands[args.command](args, services)


if __name__ == "__main__":
    main()
