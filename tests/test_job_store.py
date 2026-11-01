import asyncio
import importlib.util
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.job_store import JobStore
from mint.backend.jobs import JobRecord
from mint.backend.settings import Settings


def run(coro):
    return asyncio.run(coro)


def job_store_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def store():
    url = job_store_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return engine, JobStore(session_factory)

    engine, adapter = run(setup())
    try:
        yield adapter
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


def test_job_store_round_trip(store: JobStore) -> None:
    record = JobRecord(id=uuid4(), name="csv_upload", status="running")
    record.progress = {"stage": "enriching", "done": 5, "total": 10, "message": "Enriching 5 of 10"}

    run(store.upsert(record))
    loaded = run(store.get(record.id))

    assert loaded is not None
    assert loaded.name == "csv_upload"
    assert loaded.status == "running"
    assert loaded.progress["done"] == 5

    record.status = "succeeded"
    record.result = {"enriched_count": 10}
    run(store.upsert(record))
    reloaded = run(store.get(record.id))
    assert reloaded.status == "succeeded"
    assert reloaded.result == {"enriched_count": 10}


def test_job_store_fail_running_marks_active_jobs(store: JobStore) -> None:
    active = JobRecord(id=uuid4(), name="csv_upload", status="running")
    finished = JobRecord(id=uuid4(), name="csv_upload", status="succeeded")
    run(store.upsert(active))
    run(store.upsert(finished))

    run(store.fail_running("The backend restarted while this job was running. Start it again."))

    assert run(store.get(active.id)).status == "failed"
    assert run(store.get(active.id)).error.startswith("The backend restarted")
    assert run(store.get(finished.id)).status == "succeeded"
