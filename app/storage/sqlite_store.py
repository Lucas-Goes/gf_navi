from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models import Document, FactType, Memory, MemoryDocument
from app.services.logger import logger
from app.services.utils import remove_accents
from app.siglas import expand_tags_grouped


class SQLiteStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.create_function("noaccent", 1, remove_accents)
        return self.conn

    def begin(self):
        conn = self.connect()
        conn.execute("BEGIN")

    def rollback(self):
        if self.conn:
            self.conn.rollback()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def run_migrations(self):
        conn = self.connect()
        cursor = conn.cursor()

        fact_types = ", ".join(f"'{e.value}'" for e in FactType)
        cursor.executescript(f"""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                fact_type TEXT NOT NULL CHECK(fact_type IN ({fact_types})),
                closing_period TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                decided_by TEXT,
                requested_by TEXT,
                approved_by TEXT,
                metadata TEXT,
                supersedes_id TEXT,
                superseded_by TEXT,
                registration_date TEXT NOT NULL,
                registered_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (supersedes_id) REFERENCES memories(id),
                FOREIGN KEY (superseded_by) REFERENCES memories(id)
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_documents (
                memory_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                PRIMARY KEY (memory_id, document_id),
                FOREIGN KEY (memory_id) REFERENCES memories(id),
                FOREIGN KEY (document_id) REFERENCES documents(id)
            );

            CREATE INDEX IF NOT EXISTS idx_memories_closing_period ON memories(closing_period);
            CREATE INDEX IF NOT EXISTS idx_memories_fact_type ON memories(fact_type);
            CREATE INDEX IF NOT EXISTS idx_memories_is_active ON memories(is_active);
            CREATE INDEX IF NOT EXISTS idx_memories_supersedes ON memories(supersedes_id);
            CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents(source_type);
        """)
        conn.commit()

        try:
            conn.execute("ALTER TABLE memories ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    def insert_memory(self, memory: Memory) -> Memory:
        conn = self.connect()
        conn.execute(
            """INSERT INTO memories
               (id, fact_type, closing_period, title, description, tags,
                decided_by, requested_by, approved_by, metadata,
                supersedes_id, superseded_by, registration_date,
                registered_by, created_at, updated_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id, memory.fact_type.value, memory.closing_period,
                memory.title, memory.description,
                ",".join(memory.tags),
                memory.decided_by,
                memory.requested_by, memory.approved_by,
                json.dumps(memory.metadata) if memory.metadata else None,
                memory.supersedes_id, memory.superseded_by,
                memory.registration_date, memory.registered_by,
                memory.created_at, memory.updated_at,
                1 if memory.is_active else 0,
            ),
        )
        conn.commit()
        return memory

    def _resolve_prefix(self, prefix: str, table: str = "memories") -> Optional[str]:
        if not prefix or len(prefix) < 8:
            return None
        conn = self.connect()
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE id LIKE ? || '%'", (prefix,)
        ).fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
        if len(rows) > 1:
            matches = ", ".join(r["id"][:8] for r in rows[:5])
            logger.warning("Prefixo '%s' é ambíguo. Múltiplas memórias: %s", prefix, matches)
        return None

    def get_memory(self, memory_id: str) -> Optional[Memory]:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row:
            return self._row_to_memory(row)
        resolved = self._resolve_prefix(memory_id, "memories")
        if resolved:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (resolved,)
            ).fetchone()
            return self._row_to_memory(row) if row else None
        return None

    def get_memories_by_ids(self, ids: list[str]) -> list[Memory]:
        if not ids:
            return []
        conn = self.connect()
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def search_memories_sql(
        self, fact_type: Optional[str] = None,
        closing_period: Optional[str] = None,
        text_query: Optional[str] = None,
        tags: Optional[list[str]] = None,
        limit: int = 20,
        active: Optional[bool] = None,
        reverse: bool = False,
        offset: int = 0,
    ) -> list[Memory]:
        conn = self.connect()
        if active is False:
            conditions = ["is_active = 0"]
        elif active is True:
            conditions = ["is_active = 1"]
        else:
            conditions = ["is_active = 1"]  # default: só ativas
        params = []

        if fact_type:
            conditions.append("fact_type = ?")
            params.append(fact_type)
        if closing_period:
            conditions.append("closing_period = ?")
            params.append(closing_period)
        if tags:
            groups = expand_tags_grouped(tags)
            group_clauses = []
            for group in groups:
                or_clauses = []
                for t in group:
                    or_clauses.append("',' || tags || ',' LIKE '%,' || ? || ',%'")
                    params.append(t)
                group_clauses.append(f"({' OR '.join(or_clauses)})")
            conditions.append(f"({' AND '.join(group_clauses)})")
        if text_query:
            tokens = [t.strip() for t in text_query.split() if t.strip()]
            if len(tokens):
                clauses = []
                for token in tokens:
                    clauses.append("(noaccent(title) LIKE noaccent(?) OR noaccent(description) LIKE noaccent(?)"
                                   " OR ',' || tags || ',' LIKE '%,' || noaccent(?) || ',%')")
                    params.extend([f"%{token}%", f"%{token}%", f"%{token}%"])
                conditions.append(f"({' AND '.join(clauses)})")

        where = " AND ".join(conditions)
        order = "created_at ASC" if reverse else "created_at DESC"
        limit_val = limit if limit is not None else 50
        rows = conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*params, limit_val, offset),
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def delete_memory(self, memory_id: str):
        conn = self.connect()
        conn.execute("DELETE FROM memory_documents WHERE memory_id = ?", (memory_id,))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()

    def update_superseded_by(self, memory_id: str, superseded_by_id: str):
        conn = self.connect()
        resolved = self._resolve_prefix(memory_id, "memories")
        if not resolved:
            row = conn.execute(
                "SELECT id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if not row:
                return
            resolved = row["id"]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE memories SET superseded_by = ?, is_active = 0, updated_at = ? WHERE id = ?",
            (superseded_by_id, now, resolved),
        )
        conn.commit()

    def insert_document(self, doc: Document) -> Document:
        conn = self.connect()
        conn.execute(
            """INSERT INTO documents
               (id, filename, source_type, title, content, chunk_index, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc.id, doc.filename, doc.source_type, doc.title,
             doc.content, doc.chunk_index, doc.created_at),
        )
        conn.commit()
        return doc

    def get_document(self, doc_id: str) -> Optional[Document]:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return self._row_to_document(row) if row else None

    def get_documents_by_ids(self, ids: list[str]) -> list[Document]:
        if not ids:
            return []
        conn = self.connect()
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM documents WHERE id IN ({placeholders})", ids
        ).fetchall()
        return [self._row_to_document(r) for r in rows]

    def get_documents_by_memory(self, memory_id: str) -> list[Document]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT d.* FROM documents d
               JOIN memory_documents md ON d.id = md.document_id
               WHERE md.memory_id = ?""",
            (memory_id,),
        ).fetchall()
        return [self._row_to_document(r) for r in rows]

    def link_memory_document(self, memory_id: str, document_id: str):
        conn = self.connect()
        conn.execute(
            "INSERT OR IGNORE INTO memory_documents (memory_id, document_id) VALUES (?, ?)",
            (memory_id, document_id),
        )
        conn.commit()

    def search_documents_by_filename(self, term: str) -> list[Document]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM documents WHERE filename LIKE ? ORDER BY filename, chunk_index",
            (f"{term}%",),
        ).fetchall()
        return [self._row_to_document(r) for r in rows]

    def document_exists(self, filename: str) -> bool:
        conn = self.connect()
        row = conn.execute(
            "SELECT id FROM documents WHERE filename = ? LIMIT 1", (filename,)
        ).fetchone()
        return row is not None

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        raw_tags = row["tags"] or ""
        parsed_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        return Memory(
            id=row["id"],
            fact_type=row["fact_type"],
            closing_period=row["closing_period"],
            title=row["title"],
            description=row["description"],
            tags=parsed_tags,
            decided_by=row["decided_by"],
            requested_by=row["requested_by"],
            approved_by=row["approved_by"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            supersedes_id=row["supersedes_id"],
            superseded_by=row["superseded_by"],
            registration_date=row["registration_date"],
            registered_by=row["registered_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_active=bool(row["is_active"]),
        )

    def _row_to_document(self, row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            filename=row["filename"],
            source_type=row["source_type"],
            title=row["title"],
            content=row["content"],
            chunk_index=row["chunk_index"],
            created_at=row["created_at"],
        )
