from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter()


class ThreadUpsertRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    event_id: UUID | None = None


class ChatMessageInput(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str
    tool_trace: list[dict[str, Any]] | None = None


class AppendMessagesRequest(BaseModel):
    messages: list[ChatMessageInput]


def _storage(request: Request):
    storage = getattr(request.app.state.ctx, "chat_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="chat storage is not configured")
    return storage


@router.get("/chat/threads")
async def list_threads(request: Request) -> list[dict]:
    return await _storage(request).list_threads()


@router.get("/chat/threads/{thread_id}")
async def get_thread(thread_id: UUID, request: Request) -> dict:
    thread = await _storage(request).get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread


@router.put("/chat/threads/{thread_id}")
async def upsert_thread(thread_id: UUID, body: ThreadUpsertRequest, request: Request) -> dict:
    storage = _storage(request)
    if body.event_id is None:
        return await storage.upsert_thread(thread_id, body.title)
    return await storage.upsert_thread(thread_id, body.title, event_id=body.event_id)


@router.post("/chat/threads/{thread_id}/messages")
async def append_messages(thread_id: UUID, body: AppendMessagesRequest, request: Request) -> dict:
    appended = await _storage(request).append_messages(
        thread_id,
        [message.model_dump(mode="python") for message in body.messages],
    )
    if not appended:
        raise HTTPException(status_code=404, detail="thread not found")
    return {"appended": len(body.messages)}


@router.delete("/chat/threads/{thread_id}")
async def delete_thread(thread_id: UUID, request: Request) -> dict:
    deleted = await _storage(request).delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    return {"deleted": True}
