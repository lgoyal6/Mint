import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


logger = logging.getLogger(__name__)

JobStatus = str


@dataclass
class JobRecord:
    id: UUID
    name: str
    status: JobStatus = "queued"
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {
            "id": str(self.id) if mode == "json" else self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if mode == "json" else self.created_at,
            "updated_at": self.updated_at.isoformat() if mode == "json" else self.updated_at,
        }


class JobProgress:
    def __init__(self, record: JobRecord, on_update: Callable[[JobRecord], Awaitable[None]] | None = None) -> None:
        self._record = record
        self._on_update = on_update

    async def update(
        self,
        *,
        stage: str,
        done: int,
        total: int,
        message: str | None = None,
        eta_seconds: int | None = None,
        elapsed_seconds: int | None = None,
        rate_per_minute: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "stage": stage,
            "done": done,
            "total": total,
            "message": message or f"{stage.capitalize()} {done} of {total}",
        }
        if eta_seconds is not None:
            payload["eta_seconds"] = eta_seconds
        if elapsed_seconds is not None:
            payload["elapsed_seconds"] = elapsed_seconds
        if rate_per_minute is not None:
            payload["rate_per_minute"] = rate_per_minute
        self._record.progress = payload
        self._record.updated_at = datetime.now(UTC)
        if self._on_update is not None:
            await self._on_update(self._record)


class JobManager:
    def __init__(self, store: Any = None) -> None:
        self._jobs: dict[UUID, JobRecord] = {}
        self._tasks: dict[UUID, asyncio.Task] = {}
        self._active_keys: dict[str, UUID] = {}
        self._store = store

    def start(
        self,
        name: str,
        runner: Callable[[JobProgress], Awaitable[Any]],
        *,
        key: str | None = None,
    ) -> JobRecord:
        if key is not None:
            existing_id = self._active_keys.get(key)
            existing = self._jobs.get(existing_id) if existing_id is not None else None
            if existing is not None and existing.status in {"queued", "running"}:
                return existing

        record = JobRecord(id=uuid4(), name=name)
        self._jobs[record.id] = record
        if key is not None:
            self._active_keys[key] = record.id
        self._tasks[record.id] = asyncio.create_task(self._run(record, runner, key=key))
        return record

    def get(self, job_id: UUID) -> JobRecord | None:
        return self._jobs.get(job_id)

    async def _run(
        self,
        record: JobRecord,
        runner: Callable[[JobProgress], Awaitable[Any]],
        *,
        key: str | None,
    ) -> None:
        record.status = "running"
        record.updated_at = datetime.now(UTC)
        await self._persist(record)
        try:
            result = await runner(JobProgress(record, on_update=self._persist))
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            record.updated_at = datetime.now(UTC)
            await self._persist(record)
        else:
            record.status = "succeeded"
            record.result = _jsonable_result(result)
            record.updated_at = datetime.now(UTC)
            await self._persist(record)
        finally:
            self._tasks.pop(record.id, None)
            if key is not None and self._active_keys.get(key) == record.id:
                self._active_keys.pop(key, None)

    async def _persist(self, record: JobRecord) -> None:
        if self._store is None:
            return
        try:
            await self._store.upsert(record)
        except Exception:
            logger.warning("Could not persist job %s to the database", record.id, exc_info=True)


def _jsonable_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    return {"value": result}
