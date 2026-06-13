"""
Navi CLI — Cérebro Institucional

Uso:
  python cli.py add <texto>        Adicionar nova memória
  python cli.py ask <pergunta>     Consultar memórias
  python cli.py search <termo>     Buscar memórias (sem LLM)
  python cli.py list [--type T] [--period YYYY-MM]  Listar memórias
  python cli.py get <id>           Ver detalhes de uma memória
  python cli.py sync-docs          Sincronizar documentos
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.models import Document, FactType, Preview
from app.services.bedrock_parser import BedrockParser
from app.services.bedrock_synthesizer import BedrockSynthesizer
from app.services.ingestion import IngestionService
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


def get_services():
    sqlite = SQLiteStore(settings.sqlite_path)
    sqlite.run_migrations()
    vector = VectorStore(settings.chroma_path, settings.embedding_model)
    parser = BedrockParser()
    ingestion = IngestionService(sqlite, vector)
    search = SearchService(sqlite, vector)
    synthesizer = BedrockSynthesizer()
    return sqlite, vector, parser, ingestion, search, synthesizer


def cmd_add(args, services):
    _, _, parser, ingestion, _, _ = services
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    print("\n🧠 Analisando texto e extraindo campos...\n")
    try:
        preview = parser.parse(text)
    except Exception as e:
        print(f"❌ Erro ao processar: {e}")
        print("   Verifique se o AWS Bedrock está configurado (aws configure).")
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
    answer = synthesizer.synthesize(question, results)
    print(answer)
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
    from app.services.search import SearchResult

    m = r.memory
    print(f"  [{m.id[:8]}] {m.title} ({m.closing_period})")
    print(f"         Tipo: {m.fact_type.value} | Score: {r.score:.3f}")
    if r.warnings:
        for w in r.warnings:
            print(f"         ⚠️  {w}")
    print(f"         {m.description[:200]}{'...' if len(m.description) > 200 else ''}")
    print()


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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    services = get_services()

    commands = {
        "add": cmd_add,
        "ask": cmd_ask,
        "search": cmd_search,
        "list": cmd_list,
        "get": cmd_get,
        "sync-docs": cmd_sync_docs,
    }

    commands[args.command](args, services)


if __name__ == "__main__":
    main()
