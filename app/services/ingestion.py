from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.models import Memory, Preview
from app.services.logger import logger
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore


class IngestionService:
    def __init__(self, sqlite: SQLiteStore, vector: VectorStore):
        self.sqlite = sqlite
        self.vector = vector
        self._previews: dict[str, Preview] = {}

    def store_preview(self, preview: Preview) -> Preview:
        self._previews[preview.preview_id] = preview
        return preview

    def get_preview(self, preview_id: str) -> Preview | None:
        return self._previews.get(preview_id)

    def remove_preview(self, preview_id: str):
        self._previews.pop(preview_id, None)

    def _get_user(self) -> str:
        try:
            return os.getlogin()
        except OSError:
            return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"

    def confirm(self, preview: Preview) -> Memory:
        memory = Memory(
            id=str(uuid.uuid4()),
            fact_type=preview.fact_type,
            closing_period=preview.closing_period,
            title=preview.title,
            description=preview.description,
            tags=preview.tags,
            decided_by=preview.decided_by,
            requested_by=preview.requested_by,
            approved_by=preview.approved_by,
            metadata=preview.metadata,
            supersedes_id=preview.supersedes_id,
            registration_date=datetime.now(timezone.utc).isoformat(),
            registered_by=self._get_user(),
            is_active=True,
        )

        conn = self.sqlite.connect()
        try:
            conn.execute("BEGIN")
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

            if preview.supersedes_id:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE memories SET superseded_by = ?, is_active = 0, updated_at = ? WHERE id = ?",
                    (memory.id, now, preview.supersedes_id),
                )

            self.vector.add_memory(
                memory_id=memory.id,
                title=memory.title,
                description=memory.description,
                metadata={
                    "memory_id": memory.id,
                    "fact_type": memory.fact_type.value,
                    "closing_period": memory.closing_period,
                    "tags": ",".join(memory.tags),
                    "decided_by": memory.decided_by or "",
                    "requested_by": memory.requested_by or "",
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.error("Falha ao confirmar preview %s: rollback executado", preview.preview_id)
            raise
        finally:
            self.remove_preview(preview.preview_id)

        return memory
