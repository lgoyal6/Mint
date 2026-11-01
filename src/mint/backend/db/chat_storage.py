from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.db.tables import ChatMessage, ChatThread


class ChatStorage:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def list_threads(self) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            rows = (
                (await session.execute(select(ChatThread).order_by(ChatThread.updated_at.desc()))).scalars().all()
            )
        return [_thread_dict(row) for row in rows]

    async def get_thread(self, thread_id: UUID) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            thread = (
                await session.execute(select(ChatThread).where(ChatThread.id == thread_id))
            ).scalar_one_or_none()
            if thread is None:
                return None
            messages = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(ChatMessage.thread_id == thread_id)
                        .order_by(ChatMessage.position)
                    )
                )
                .scalars()
                .all()
            )
        output = _thread_dict(thread)
        output["messages"] = [
            {
                "id": str(message.id),
                "role": message.role,
                "content": message.content,
                "tool_trace": message.tool_trace,
            }
            for message in messages
        ]
        return output

    async def upsert_thread(self, thread_id: UUID, title: str, *, event_id: UUID | None = None) -> dict[str, Any]:
        stmt = (
            pg_insert(ChatThread)
            .values(id=thread_id, title=title, event_id=event_id)
            .on_conflict_do_update(index_elements=[ChatThread.id], set_={"title": title, "event_id": event_id, "updated_at": func.now()})
            .returning(ChatThread)
        )
        async with self.session_factory() as session:
            async with session.begin():
                thread = (await session.execute(stmt)).scalar_one()
                return _thread_dict(thread)

    async def append_messages(self, thread_id: UUID, messages: list[dict[str, Any]]) -> bool:
        async with self.session_factory() as session:
            async with session.begin():
                exists = (
                    await session.execute(select(ChatThread.id).where(ChatThread.id == thread_id))
                ).scalar_one_or_none()
                if exists is None:
                    return False
                if not messages:
                    return True
                max_position = (
                    await session.execute(
                        select(func.max(ChatMessage.position)).where(ChatMessage.thread_id == thread_id)
                    )
                ).scalar_one()
                next_position = (max_position if max_position is not None else -1) + 1
                values = [
                    {
                        "id": message["id"],
                        "thread_id": thread_id,
                        "position": next_position + offset,
                        "role": message["role"],
                        "content": message["content"],
                        "tool_trace": message.get("tool_trace"),
                    }
                    for offset, message in enumerate(messages)
                ]
                await session.execute(
                    pg_insert(ChatMessage).values(values).on_conflict_do_nothing(index_elements=[ChatMessage.id])
                )
                await session.execute(
                    update(ChatThread).where(ChatThread.id == thread_id).values(updated_at=func.now())
                )
        return True

    async def delete_thread(self, thread_id: UUID) -> bool:
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ChatThread).where(ChatThread.id == thread_id).returning(ChatThread.id)
                )
                return result.scalar_one_or_none() is not None

    async def search_threads(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        async with self.session_factory() as session:
            rows = await session.execute(
                select(ChatThread, ChatMessage)
                .join(ChatMessage, ChatMessage.thread_id == ChatThread.id)
                .where(
                    ChatThread.title.ilike(pattern) | ChatMessage.content.ilike(pattern)
                )
                .order_by(ChatThread.updated_at.desc(), ChatMessage.position.desc())
                .limit(max(1, min(limit, 20)))
            )
        return [
            {
                "thread_id": str(thread.id),
                "title": thread.title,
                "event_id": str(thread.event_id) if thread.event_id else None,
                "role": message.role,
                "snippet": message.content[:700],
            }
            for thread, message in rows
        ]


def _thread_dict(thread: ChatThread) -> dict[str, Any]:
    return {
        "id": str(thread.id),
        "title": thread.title,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "event_id": str(thread.event_id) if thread.event_id else None,
    }
