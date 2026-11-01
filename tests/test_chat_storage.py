import asyncio
import importlib.util
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.chat_storage import ChatStorage
from mint.backend.settings import Settings


def run(coro):
    return asyncio.run(coro)


def chat_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def storage():
    url = chat_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return engine, ChatStorage(session_factory)

    engine, adapter = run(setup())
    try:
        yield adapter
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


def test_chat_thread_round_trip(storage: ChatStorage) -> None:
    thread_id = uuid4()
    run(storage.upsert_thread(thread_id, "Show me the P0s"))

    user_id, assistant_id = uuid4(), uuid4()
    appended = run(
        storage.append_messages(
            thread_id,
            [
                {"id": user_id, "role": "user", "content": "Show me the P0s", "tool_trace": None},
                {
                    "id": assistant_id,
                    "role": "assistant",
                    "content": "Two P0s.",
                    "tool_trace": [{"name": "list_leads", "input": {"tier": "P0"}}],
                },
            ],
        )
    )
    assert appended is True

    thread = run(storage.get_thread(thread_id))
    assert thread is not None
    assert thread["title"] == "Show me the P0s"
    assert [message["role"] for message in thread["messages"]] == ["user", "assistant"]
    assert thread["messages"][1]["tool_trace"][0]["name"] == "list_leads"

    threads = run(storage.list_threads())
    assert [item["id"] for item in threads] == [str(thread_id)]


def test_append_messages_is_idempotent_per_message_id(storage: ChatStorage) -> None:
    thread_id = uuid4()
    run(storage.upsert_thread(thread_id, "Retry test"))
    message = {"id": uuid4(), "role": "user", "content": "hello", "tool_trace": None}

    run(storage.append_messages(thread_id, [message]))
    run(storage.append_messages(thread_id, [message]))

    thread = run(storage.get_thread(thread_id))
    assert len(thread["messages"]) == 1


def test_append_messages_returns_false_for_unknown_thread(storage: ChatStorage) -> None:
    appended = run(
        storage.append_messages(uuid4(), [{"id": uuid4(), "role": "user", "content": "x", "tool_trace": None}])
    )
    assert appended is False


def test_delete_thread_cascades_messages(storage: ChatStorage) -> None:
    thread_id = uuid4()
    run(storage.upsert_thread(thread_id, "Delete me"))
    run(storage.append_messages(thread_id, [{"id": uuid4(), "role": "user", "content": "x", "tool_trace": None}]))

    assert run(storage.delete_thread(thread_id)) is True
    assert run(storage.get_thread(thread_id)) is None
    assert run(storage.delete_thread(thread_id)) is False
