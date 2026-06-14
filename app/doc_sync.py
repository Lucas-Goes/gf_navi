from __future__ import annotations

import re
from pathlib import Path

from app.config import settings
from app.models import Document
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


def cmd_sync_docs(sqlite: SQLiteStore, vector: VectorStore):
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

        chunk_size = 1500
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


def _find_document_refs(text: str) -> list[str]:
    patterns = [
        r'doc[:\s]+([\w\-_.]+\.(?:txt|pdf|md))',
        r'documento[:\s]+([\w\-_.]+\.(?:txt|pdf|md))',
        r'arquivo[:\s]+([\w\-_.]+\.(?:txt|pdf|md))',
    ]
    refs = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            refs.append(m.group(1))
    return refs


def _link_documents(sqlite: SQLiteStore, text: str, memory_id: str, supersedes_id: str | None = None):
    if supersedes_id:
        seen = set()
        chain = [supersedes_id]
        depth = 0
        while chain and depth < 50:
            depth += 1
            cur = chain.pop()
            docs = sqlite.get_documents_by_memory(cur)
            for d in docs:
                if d.id not in seen:
                    sqlite.link_memory_document(memory_id, d.id)
                    seen.add(d.id)
            mem = sqlite.get_memory(cur)
            if mem and mem.supersedes_id and mem.supersedes_id not in seen:
                chain.append(mem.supersedes_id)
        if seen:
            plural = "s" if len(seen) > 1 else ""
            print(f"   🔗 Herdado(s) {len(seen)} vínculo{plural} de documento{plural} da cadeia de correção")

    refs = _find_document_refs(text)
    if not refs:
        return
    for ref in refs:
        docs = sqlite.search_documents_by_filename(ref)
        if docs:
            doc_ids = set()
            for d in docs:
                sqlite.link_memory_document(memory_id, d.id)
                doc_ids.add(d.id)
            print(f"   📎 Vinculado ao documento: {docs[0].filename}"
                  f"{' (+ {} chunk(s))'.format(len(doc_ids) - 1) if len(doc_ids) > 1 else ''}")
        else:
            resp = input(f"   ⚠️  Documento '{ref}' não encontrado. [a]dicionar / [p]ular / [e]ditar nome? (a/P/e): ").strip().lower()
            if resp == "a":
                print("   Use 'python cli.py sync-docs' após adicionar o arquivo em data/documents/")
            elif resp == "e":
                novo = input("      Nome correto do documento: ").strip()
                if novo:
                    docs = sqlite.search_documents_by_filename(novo)
                    if docs:
                        for d in docs:
                            sqlite.link_memory_document(memory_id, d.id)
                        print(f"   📎 Vinculado ao documento: {docs[0].filename}")
                    else:
                        print(f"   ⚠️  Documento '{novo}' também não encontrado.")
            else:
                print(f"   ⏭️  Vínculo com '{ref}' pulado.")
