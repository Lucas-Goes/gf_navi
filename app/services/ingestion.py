from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.models import Memory, Preview
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

    def confirm(self, preview: Preview) -> Memory:
        memory = Memory(
            id=str(uuid.uuid4()),
            fact_type=preview.fact_type,
            closing_period=preview.closing_period,
            title=preview.title,
            description=preview.description,
            decided_by=preview.decided_by,
            requested_by=preview.requested_by,
            approved_by=preview.approved_by,
            metadata=preview.metadata,
            supersedes_id=preview.supersedes_id,
            registration_date=datetime.now(timezone.utc).isoformat(),
            registered_by=os.getlogin(),
            is_active=True,
        )

        self.sqlite.insert_memory(memory)

        if preview.supersedes_id:
            self.sqlite.update_superseded_by(
                preview.supersedes_id, memory.id
            )
        self.vector.add_memory(
            memory_id=memory.id,
            title=memory.title,
            description=memory.description,
            metadata={
                "memory_id": memory.id,
                "fact_type": memory.fact_type.value,
                "closing_period": memory.closing_period,
                "decided_by": memory.decided_by or "",
                "requested_by": memory.requested_by or "",
            },
        )

        self.remove_preview(preview.preview_id)
        return memory
