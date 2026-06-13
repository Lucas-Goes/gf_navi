from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FactType(str, Enum):
    rule_change = "rule_change"
    decision = "decision"
    implementation = "implementation"
    incident = "incident"
    other = "other"


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fact_type: FactType
    closing_period: str
    title: str
    description: str
    decided_by: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    metadata: Optional[dict] = None
    supersedes_id: Optional[str] = None
    superseded_by: Optional[str] = None
    registration_date: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    registered_by: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_active: bool = True


class Document(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    source_type: str
    title: str
    content: str
    chunk_index: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MemoryDocument(BaseModel):
    memory_id: str
    document_id: str


class Preview(BaseModel):
    preview_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    fact_type: FactType
    closing_period: str
    description: str
    decided_by: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    metadata: Optional[dict] = None
    supersedes_id: Optional[str] = None
    is_correction: bool = False
    confidence_score: float = 1.0
    superseded_memory_title: Optional[str] = None


class SearchResult(BaseModel):
    memory: Memory
    score: float
    warnings: list[str] = []
    related_documents: list[Document] = []
