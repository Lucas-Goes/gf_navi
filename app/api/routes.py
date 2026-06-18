from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.services.ask_agent import AskAgent

_SENTINEL = object()
from app.services.logger import logger
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
    session_id = body.get("session_id") or None
    if not question:
        return {"error": "Pergunta vazia"}

    agent = _get_agent()

    async def event_stream():
        loop = asyncio.get_event_loop()
        agent = _get_agent()
        gen = agent.ask(question, session_id=session_id)
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(gen, _SENTINEL))
            if chunk is _SENTINEL:
                break
            if isinstance(chunk, dict):
                if "debug" in chunk:
                    yield f"data: {json.dumps({'debug': chunk['debug']})}\n\n"
                else:
                    yield f"data: {json.dumps(chunk)}\n\n"
            else:
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


@router.get("/health")
async def health():
    return {"status": "ok", "service": "navi"}
