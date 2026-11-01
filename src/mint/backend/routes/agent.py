import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


router = APIRouter()
STREAM_HEARTBEAT_SECONDS = 15.0


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[dict[str, Any]] = Field(min_length=1)


@router.post("/agent/chat")
async def agent_chat(body: AgentChatRequest, request: Request) -> dict:
    agent_loop = _agent_loop(request)
    result = await agent_loop.run(body.messages)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


@router.post("/agent/chat/stream")
async def agent_chat_stream(body: AgentChatRequest, request: Request) -> StreamingResponse:
    agent_loop = _agent_loop(request)

    async def event_stream():
        try:
            async for event in _events_with_heartbeats(agent_loop.run_stream(body.messages)):
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            # Stop proxies (nginx-style) from buffering the stream.
            "x-accel-buffering": "no",
        },
    )


def _agent_loop(request: Request):
    agent_loop = getattr(request.app.state.ctx, "agent_loop", None)
    if agent_loop is None:
        raise HTTPException(status_code=503, detail="agent is not configured")
    return agent_loop


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _events_with_heartbeats(events, interval: float | None = None):
    interval = STREAM_HEARTBEAT_SECONDS if interval is None else interval
    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(events))
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=interval)
            except TimeoutError:
                yield {"type": "heartbeat"}
                continue
            except StopAsyncIteration:
                break
            pending = None
            yield event
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()
