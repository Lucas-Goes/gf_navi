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
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import textwrap
from pathlib import Path

from rich.console import Console
from rich.table import Table

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
            print(f"   ⚠️  A memória '{best.title}' foi corrigida por uma versão mais recente.")
            print(f"   💡 Usando a versão ativa: {superseder.title} ({superseder.id[:8]})")
            return superseder

    return best


def cmd_correct(args, services):
    sqlite, _, parser, ingestion, search, _ = services
    memory_id = args.id
    text = " ".join(args.text) if isinstance(args.text, list) else args.text

    if memory_id:
        old = sqlite.get_memory(memory_id)
        if not old:
            print(f"❌ Memória {memory_id} não encontrada.")
            return
    else:
        first_word = args.text[0].lower()
        if len(first_word) >= 6 and all(c in "0123456789abcdef" for c in first_word):
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
                return
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


def _db_conn():
    import sqlite3
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    return conn


def _db_memories(conn, show_all: bool = False, limit: int = 100):
    if show_all:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memories WHERE is_active = 1 ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return rows


def _db_table(console: Console, title: str, columns: list[str], rows: list[tuple]):
    table = Table(title=title, title_style="bold cyan", border_style="dim")
    for col in columns:
        table.add_column(col, style="white", no_wrap=(col in ("ID", "Período", "Tipo", "Ativo", "Chunks")))
    for row in rows:
        table.add_row(*[str(v) if v is not None else "—" for v in row])
    console.print(table)


def cmd_db(args, services):
    import sqlite3

    conn = _db_conn()
    parts = args.command_db
    cmd = parts[0] if parts else None
    extra = parts[1:] if len(parts) > 1 else []

    if cmd == "tables":
        console = Console()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table = Table(title="Tabelas", title_style="bold cyan", border_style="dim")
        table.add_column("Tabela", style="green")
        table.add_column("SQL (CREATE)", style="dim", max_width=80)
        for r in rows:
            name = r["name"]
            sql = conn.execute(
                f"SELECT sql FROM sqlite_master WHERE name = ?", (name,)
            ).fetchone()
            create_sql = sql["sql"] if sql and sql["sql"] else ""
            table.add_row(name, create_sql)
        console.print(table)

    elif cmd == "memories":
        console = Console()
        rows = _db_memories(conn, show_all=args.all, limit=args.limit or 100)
        if not rows:
            console.print("[yellow]Nenhuma memória encontrada.[/yellow]")
            return
        _db_table(console, f"Memórias ({len(rows)} registro(s))", ["ID", "Título", "Tipo", "Período", "Ativo", "Substituído por"], [
            (r["id"][:8], r["title"][:45], r["fact_type"], r["closing_period"],
             "✅" if r["is_active"] else "❌",
             r["superseded_by"][:8] if r["superseded_by"] else "—")
            for r in rows
        ])

    elif cmd == "documents":
        console = Console()
        rows = conn.execute(
            "SELECT id, filename, source_type, title, chunk_index, substr(content, 1, 60) as preview FROM documents ORDER BY filename, chunk_index LIMIT ?",
            (args.limit or 100,),
        ).fetchall()
        if not rows:
            console.print("[yellow]Nenhum documento encontrado.[/yellow]")
            return
        _db_table(console, f"Documentos ({len(rows)} chunk(s))", ["ID", "Arquivo", "Tipo", "Chunk", "Preview"], [
            (r["id"][:8], r["filename"], r["source_type"], str(r["chunk_index"]), r["preview"])
            for r in rows
        ])

    elif cmd == "links":
        console = Console()
        rows = conn.execute(
            """SELECT md.memory_id, m.title as mem_title, m.is_active,
                      md.document_id, d.filename as doc_name
               FROM memory_documents md
               JOIN memories m ON m.id = md.memory_id
               JOIN documents d ON d.id = md.document_id
               LIMIT ?""",
            (args.limit or 100,),
        ).fetchall()
        if not rows:
            console.print("[yellow]Nenhum vínculo encontrado.[/yellow]")
            return
        _db_table(console, f"Vínculos Memória ↔ Documento ({len(rows)})", ["Memória", "Título", "Ativa", "Documento", "Arquivo"], [
            (r["memory_id"][:8], r["mem_title"][:40], "✅" if r["is_active"] else "❌", r["document_id"][:8], r["doc_name"])
            for r in rows
        ])

    elif cmd == "memory":
        entry_id = extra[0] if extra else None
        if not entry_id:
            Console().print("[red]Informe o ID da memória: db memory <id>[/red]")
            return
        console = Console()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ? OR id LIKE ? || '%'",
            (entry_id, entry_id),
        ).fetchone()
        if not row:
            console.print(f"[red]Memória {entry_id} não encontrada.[/red]")
            return
        console.print(f"\n[bold cyan]📌 Memória: {row['title']}[/bold cyan]")
        console.print(f"   [dim]ID:[/dim] {row['id']}")
        console.print(f"   [dim]Tipo:[/dim] {row['fact_type']}")
        console.print(f"   [dim]Período:[/dim] {row['closing_period']}")
        console.print(f"   [dim]Ativo:[/dim] {'✅ Sim' if row['is_active'] else '❌ Não'}")
        console.print(f"   [dim]Registrado por:[/dim] {row['registered_by']}")
        console.print(f"   [dim]Data registro:[/dim] {row['registration_date'][:19]}")
        console.print(f"   [dim]Criado em:[/dim] {row['created_at'][:19]}")
        console.print(f"   [dim]Atualizado em:[/dim] {row['updated_at'][:19]}")
        console.print(f"   [dim]Decidido por:[/dim] {row['decided_by'] or '—'}")
        console.print(f"   [dim]Solicitado por:[/dim] {row['requested_by'] or '—'}")
        console.print(f"   [dim]Aprovado por:[/dim] {row['approved_by'] or '—'}")
        if row["supersedes_id"]:
            console.print(f"   [dim]Substitui:[/dim] {row['supersedes_id']}")
        if row["superseded_by"]:
            console.print(f"   [dim]Substituído por:[/dim] {row['superseded_by']}")
        if row["metadata"]:
            console.print(f"   [dim]Metadados:[/dim] {row['metadata']}")
        console.print(f"\n   [bold]Descrição:[/bold]")
        console.print(textwrap.indent(row["description"], "      "))
        docs = conn.execute(
            "SELECT d.title, d.filename FROM documents d JOIN memory_documents md ON d.id = md.document_id WHERE md.memory_id = ?",
            (row["id"],),
        ).fetchall()
        if docs:
            console.print(f"\n   [bold]📎 Documentos relacionados:[/bold]")
            for d in docs:
                console.print(f"      - {d['title']} ({d['filename']})")
        print()

    elif cmd == "document":
        entry_id = extra[0] if extra else None
        if not entry_id:
            Console().print("[red]Informe o ID do documento: db document <id>[/red]")
            return
        console = Console()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            console.print(f"[red]Documento {entry_id} não encontrado.[/red]")
            return
        console.print(f"\n[bold cyan]📄 Documento: {row['title']}[/bold cyan]")
        console.print(f"   [dim]ID:[/dim] {row['id']}")
        console.print(f"   [dim]Arquivo:[/dim] {row['filename']}")
        console.print(f"   [dim]Tipo:[/dim] {row['source_type']}")
        console.print(f"   [dim]Chunk:[/dim] {row['chunk_index']}")
        console.print(f"   [dim]Criado em:[/dim] {row['created_at'][:19]}")
        console.print(f"\n   [bold]Conteúdo:[/bold]")
        console.print(textwrap.indent(row["content"], "      "))
        print()

    elif cmd == "export":
        fmt = extra[0] if extra else args.format
        out_dir = Path(settings.sqlite_path).parent / "export"
        out_dir.mkdir(parents=True, exist_ok=True)
        tables_info = [
            ("memories", "SELECT * FROM memories ORDER BY created_at DESC"),
            ("documents", "SELECT * FROM documents ORDER BY filename, chunk_index"),
            ("memory_documents", "SELECT * FROM memory_documents"),
        ]
        if fmt == "json":
            for name, sql in tables_info:
                rows = conn.execute(sql).fetchall()
                data = [dict(r) for r in rows]
                if name == "memories":
                    for d in data:
                        if "metadata" in d and d["metadata"]:
                            try:
                                d["metadata"] = json.loads(d["metadata"])
                            except (json.JSONDecodeError, TypeError):
                                pass
                filepath = out_dir / f"{name}.json"
                filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✅ Export JSON salvo em: {out_dir}")
        elif fmt == "csv":
            for name, sql in tables_info:
                rows = conn.execute(sql).fetchall()
                if not rows:
                    continue
                filepath = out_dir / f"{name}.csv"
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(rows[0].keys())
                    for r in rows:
                        writer.writerow([str(v) if v is not None else "" for v in r])
            print(f"✅ Export CSV salvo em: {out_dir}")

    elif cmd == "query":
        sql = " ".join(extra) if extra else None
        if not sql:
            Console().print("[red]Informe a SQL: db query <sql>[/red]")
            return
        console = Console()
        try:
            cursor = conn.execute(sql)
            rows = cursor.fetchall()
            if not rows:
                console.print("[yellow]Nenhum resultado.[/yellow]")
            else:
                columns = [desc[0] for desc in cursor.description]
                _db_table(console, f"Query ({len(rows)} resultado(s))", columns, [tuple(r) for r in rows])
        except sqlite3.Error as e:
            console.print(f"[red]Erro SQL: {e}[/red]")

    else:
        console = Console()
        counts = {}
        for name in ("memories", "documents", "memory_documents"):
            row = conn.execute(f"SELECT COUNT(*) as c FROM {name}").fetchone()
            counts[name] = row["c"]
        active = conn.execute("SELECT COUNT(*) as c FROM memories WHERE is_active = 1").fetchone()["c"]
        inactive = conn.execute("SELECT COUNT(*) as c FROM memories WHERE is_active = 0").fetchone()["c"]

        console.print("\n[bold cyan]🗄️  Navi — Database Dashboard[/bold cyan]")
        console.print(f"   [dim]Banco:[/dim] {settings.sqlite_path}")
        console.print()
        table = Table(title="Contagens", border_style="dim")
        table.add_column("Tabela", style="green")
        table.add_column("Registros", style="white", justify="right")
        table.add_row("memories (total)", str(counts["memories"]))
        table.add_row("  ├ ativas", str(active))
        table.add_row("  └ inativas", str(inactive))
        table.add_row("documents", str(counts["documents"]))
        table.add_row("memory_documents", str(counts["memory_documents"]))
        console.print(table)
        console.print()
        console.print("[dim]Comandos disponíveis:[/dim]")
        console.print("  [cyan]db tables[/cyan]          — Listar tabelas com schema")
        console.print("  [cyan]db memories[/cyan]         — Listar memórias ativas")
        console.print("  [cyan]db memories --all[/cyan]   — Listar todas (ativas e inativas)")
        console.print("  [cyan]db documents[/cyan]        — Listar documentos")
        console.print("  [cyan]db links[/cyan]            — Vínculos memória ↔ documento")
        console.print("  [cyan]db memory <id>[/cyan]      — Detalhes de uma memória")
        console.print("  [cyan]db document <id>[/cyan]    — Detalhes de um documento")
        console.print("  [cyan]db export json[/cyan]      — Exportar tudo para JSON")
        console.print("  [cyan]db export csv[/cyan]       — Exportar tudo para CSV")
        console.print("  [cyan]db query <sql>[/cyan]      — Executar SQL arbitrário")
        print()

    conn.close()


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

    p_db = sub.add_parser("db", help="Visualizar/exportar banco de dados")
    p_db.add_argument("command_db", nargs="*", default=[], help="Subcomando + argumentos (ex: memories, memory <id>, export json)")
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
        "db": cmd_db,
    }

    if args.command in ("provider", "db"):
        commands[args.command](args, None)
    else:
        services = get_services()
        commands[args.command](args, services)


if __name__ == "__main__":
    main()
