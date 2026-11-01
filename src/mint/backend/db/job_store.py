from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.db.tables import Job
from mint.backend.jobs import JobRecord


class JobStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def upsert(self, record: JobRecord) -> None:
        values = {
            "id": record.id,
            "name": record.name,
            "status": record.status,
            "progress": record.progress,
            "result": record.result,
            "error": record.error,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        stmt = pg_insert(Job).values(**values).on_conflict_do_update(
            index_elements=[Job.id],
            set_={key: value for key, value in values.items() if key != "id"},
        )
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(stmt)

    async def get(self, job_id: UUID) -> JobRecord | None:
        async with self.session_factory() as session:
            row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
        if row is None:
            return None
        return JobRecord(
            id=row.id,
            name=row.name,
            status=row.status,
            progress=row.progress,
            result=row.result,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def fail_running(self, error_message: str) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Job)
                    .where(Job.status.in_(["queued", "running"]))
                    .values(status="failed", error=error_message)
                )
