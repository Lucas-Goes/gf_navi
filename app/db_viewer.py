from __future__ import annotations

import csv
import json
import sqlite3
import textwrap
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app.config import settings


def _db_conn():
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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


def cmd_db(conn, parts: list[str], show_all: bool, limit: int, fmt: str):
    force = "--force" in parts
    parts = [p for p in parts if p != "--force"]
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
                "SELECT sql FROM sqlite_master WHERE name = ?", (name,)
            ).fetchone()
            create_sql = sql["sql"] if sql and sql["sql"] else ""
            table.add_row(name, create_sql)
        console.print(table)

    elif cmd == "memories":
        console = Console()
        rows = _db_memories(conn, show_all=show_all, limit=limit)
        if not rows:
            console.print("[yellow]Nenhuma memória encontrada.[/yellow]")
            return
        _db_table(console, f"Memórias ({len(rows)} registro(s))", ["ID", "Título", "Tipo", "Período", "Tags", "Ativo", "Substituído por"], [
            (r["id"][:8], r["title"][:45], r["fact_type"], r["closing_period"],
             r["tags"] if r["tags"] else "—",
             "✅" if r["is_active"] else "❌",
             r["superseded_by"][:8] if r["superseded_by"] else "—")
            for r in rows
        ])

    elif cmd == "documents":
        console = Console()
        rows = conn.execute(
            "SELECT id, filename, source_type, title, chunk_index, substr(content, 1, 60) as preview FROM documents ORDER BY filename, chunk_index LIMIT ?",
            (limit,),
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
            (limit,),
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
            "SELECT * FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row and len(entry_id) >= 8:
            rows = conn.execute(
                "SELECT * FROM memories WHERE id LIKE ? || '%'", (entry_id,)
            ).fetchall()
            if len(rows) == 1:
                row = rows[0]
            elif len(rows) > 1:
                matches = ", ".join(r["id"][:8] for r in rows[:5])
                console.print(f"[red]Prefixo '{entry_id}' é ambíguo. IDs: {matches}[/red]")
                console.print("[red]Use o ID completo (8+ caracteres).[/red]")
                return
        if not row:
            console.print(f"[red]Memória {entry_id} não encontrada.[/red]")
            return
        console.print(f"\n[bold cyan]📌 Memória: {row['title']}[/bold cyan]")
        console.print(f"   [dim]ID:[/dim] {row['id']}")
        console.print(f"   [dim]Tipo:[/dim] {row['fact_type']}")
        console.print(f"   [dim]Período:[/dim] {row['closing_period']}")
        console.print(f"   [dim]Tags:[/dim] {row['tags'] if row['tags'] else '—'}")
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
        fmt_export = extra[0] if extra else fmt
        out_dir = Path(settings.sqlite_path).parent / "export"
        out_dir.mkdir(parents=True, exist_ok=True)
        tables_info = [
            ("memories", "SELECT * FROM memories ORDER BY created_at DESC"),
            ("documents", "SELECT * FROM documents ORDER BY filename, chunk_index"),
            ("memory_documents", "SELECT * FROM memory_documents"),
        ]
        if fmt_export == "json":
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
        elif fmt_export == "csv":
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

    elif cmd == "help":
        pad = 25
        Console().print(
            "\n[bold cyan]🗄️  Navi — Database Viewer[/bold cyan]\n"
            f"  [cyan]db[/cyan]{" " * (pad - 2)} — Dashboard com contagens\n"
            f"  [cyan]db help[/cyan]{" " * (pad - 7)} — Mostrar esta ajuda\n"
            f"  [cyan]db tables[/cyan]{" " * (pad - 9)} — Listar tabelas com schema SQL\n"
            f"  [cyan]db memories[/cyan]{" " * (pad - 11)} — Listar memórias ativas\n"
            f"  [cyan]db memories --all[/cyan]{" " * (pad - 17)} — Listar todas (ativas e inativas)\n"
            f"  [cyan]db documents[/cyan]{" " * (pad - 12)} — Listar documentos/chunks\n"
            f"  [cyan]db links[/cyan]{" " * (pad - 8)} — Vínculos memória ↔ documento\n"
            f"  [cyan]db memory <id>[/cyan]{" " * (pad - 14)} — Detalhes completos de uma memória\n"
            f"  [cyan]db document <id>[/cyan]{" " * (pad - 16)} — Conteúdo completo de um documento\n"
            f"  [cyan]db export json[/cyan]{" " * (pad - 14)} — Exportar tudo para JSON\n"
            f"  [cyan]db export csv[/cyan]{" " * (pad - 13)} — Exportar tudo para CSV\n"
            f"  [cyan]db query <sql>[/cyan]{" " * (pad - 14)} — Executar SELECT\n"
            f"  [cyan]db query --force <sql>[/cyan]{" " * (pad - 22)} — Executar qualquer SQL (INSERT/UPDATE/DELETE)\n"
        )

    elif cmd == "query":
        sql = " ".join(extra) if extra else None
        if not sql:
            Console().print("[red]Informe a SQL: db query <sql>[/red]")
            return
        if not sql.strip().upper().startswith("SELECT") and not force:
            Console().print("[red]Apenas consultas SELECT são permitidas. Use --force para executar mesmo assim.[/red]")
            return
        if force and not sql.strip().upper().startswith("SELECT"):
            conf = input("\n⚠️  Você está prestes a executar um comando de escrita no banco. Tem certeza? (s/N): ").strip().lower()
            if conf != "s":
                Console().print("[yellow]Comando cancelado.[/yellow]")
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
        pad = 25
        console.print(f"  [cyan]db tables[/cyan]{" " * (pad - 9)} — Listar tabelas com schema")
        console.print(f"  [cyan]db memories[/cyan]{" " * (pad - 11)} — Listar memórias ativas")
        console.print(f"  [cyan]db memories --all[/cyan]{" " * (pad - 17)} — Listar todas (ativas e inativas)")
        console.print(f"  [cyan]db documents[/cyan]{" " * (pad - 12)} — Listar documentos")
        console.print(f"  [cyan]db links[/cyan]{" " * (pad - 8)} — Vínculos memória ↔ documento")
        console.print(f"  [cyan]db memory <id>[/cyan]{" " * (pad - 14)} — Detalhes de uma memória")
        console.print(f"  [cyan]db document <id>[/cyan]{" " * (pad - 16)} — Detalhes de um documento")
        console.print(f"  [cyan]db export json[/cyan]{" " * (pad - 14)} — Exportar tudo para JSON")
        console.print(f"  [cyan]db export csv[/cyan]{" " * (pad - 13)} — Exportar tudo para CSV")
        console.print(f"  [cyan]db query <sql>[/cyan]{" " * (pad - 14)} — Executar SELECT")
        console.print(f"  [cyan]db query --force <sql>[/cyan]{" " * (pad - 22)} — Executar qualquer SQL (DML)")
        print()
