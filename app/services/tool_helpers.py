from __future__ import annotations

import uuid
from typing import Any

from app.config import settings
from app.models import Preview
from app.services.ingestion import IngestionService
from app.services.llm import create_provider
from app.services.search import SearchService
from app.doc_sync import _link_documents
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore

_SQLITE: SQLiteStore | None = None
_VECTOR: VectorStore | None = None
_LLM = None
_INGESTION: IngestionService | None = None
_SEARCH: SearchService | None = None

_PREVIEWS: dict[str, dict] = {}


def _get_sqlite() -> SQLiteStore:
    global _SQLITE
    if _SQLITE is None:
        _SQLITE = SQLiteStore(settings.sqlite_path)
        _SQLITE.run_migrations()
    return _SQLITE


def _get_vector() -> VectorStore:
    global _VECTOR
    if _VECTOR is None:
        _VECTOR = VectorStore(settings.chroma_path, settings.embedding_model)
    return _VECTOR


def _get_llm():
    global _LLM
    if _LLM is None:
        _LLM = create_provider(settings)
    return _LLM


def _get_ingestion() -> IngestionService:
    global _INGESTION
    if _INGESTION is None:
        _INGESTION = IngestionService(_get_sqlite(), _get_vector())
    return _INGESTION


def _get_search() -> SearchService:
    global _SEARCH
    if _SEARCH is None:
        _SEARCH = SearchService(_get_sqlite(), _get_vector())
    return _SEARCH


def _store_preview(kind: str, text: str, preview: Preview) -> str:
    preview_id = str(uuid.uuid4())[:8]
    _PREVIEWS[preview_id] = {"kind": kind, "text": text, "preview": preview}
    return preview_id


def _confirm_preview(preview_id: str) -> dict:
    entry = _PREVIEWS.pop(preview_id, None)
    if not entry:
        return {"error": f"Preview '{preview_id}' não encontrado ou já expirou."}
    ingestion = _get_ingestion()
    memory = ingestion.confirm(entry["preview"])
    _link_documents(_get_sqlite(), entry["text"], memory.id, entry["preview"].supersedes_id)
    return {
        "id": memory.id,
        "title": memory.title,
        "fact_type": memory.fact_type.value,
        "closing_period": memory.closing_period,
        "tags": memory.tags,
        "description": memory.description[:200],
        "is_active": memory.is_active,
    }


def _cancel_preview(preview_id: str) -> dict:
    entry = _PREVIEWS.pop(preview_id, None)
    if not entry:
        return {"error": f"Preview '{preview_id}' não encontrado."}
    return {"title": entry["preview"].title}
