from __future__ import annotations

import json
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.ask_agent import AskAgent
from app.services.search import SearchService
from app.storage.sqlite_store import SQLiteStore
from app.storage.vector_store import VectorStore
from app.config import settings

router = APIRouter()

_agent: AskAgent | None = None


def _get_agent() -> AskAgent:
    global _agent
    if _agent is None:
        sqlite = SQLiteStore(settings.sqlite_path)
        sqlite.run_migrations()
        vector = VectorStore(settings.chroma_path, settings.embedding_model)
        search = SearchService(sqlite, vector)
        _agent = AskAgent(search, vector)
    return _agent


@router.post("/chat")
async def chat(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return {"error": "Pergunta vazia"}

    agent = _get_agent()

    async def event_stream():
        agent = _get_agent()
        for chunk in agent.ask(question):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
