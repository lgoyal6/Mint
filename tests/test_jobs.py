import asyncio

from mint.backend.jobs import JobManager


def run(coro):
    return asyncio.run(coro)


def test_job_runner_progress_is_visible_while_running() -> None:
    async def scenario():
        manager = JobManager()
        gate = asyncio.Event()

        async def runner(progress):
            await progress.update(stage="enriching", done=45, total=200, message="Enriching 45 of 200 leads")
            await gate.wait()
            return {"done": True}

        record = manager.start("csv_upload", runner)
        await asyncio.sleep(0)

        snapshot = manager.get(record.id).model_dump(mode="json")
        assert snapshot["status"] == "running"
        assert snapshot["progress"] == {
            "stage": "enriching",
            "done": 45,
            "total": 200,
            "message": "Enriching 45 of 200 leads",
        }

        gate.set()
        await asyncio.sleep(0)
        final = manager.get(record.id).model_dump(mode="json")
        assert final["status"] == "succeeded"
        assert final["result"] == {"done": True}

    run(scenario())


def test_job_progress_default_message() -> None:
    async def scenario():
        manager = JobManager()

        async def runner(progress):
            await progress.update(stage="classifying", done=3, total=9)
            return None

        record = manager.start("classify", runner)
        while manager.get(record.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)
        assert manager.get(record.id).progress["message"] == "Classifying 3 of 9"

    run(scenario())


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.upserts: list[tuple[str, dict | None]] = []

    async def upsert(self, record) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.upserts.append((record.status, record.progress))


def test_job_manager_writes_through_to_store() -> None:
    async def scenario():
        store = FakeStore()
        manager = JobManager(store=store)

        async def runner(progress):
            await progress.update(stage="enriching", done=1, total=2)
            return {"ok": True}

        record = manager.start("csv_upload", runner)
        while manager.get(record.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)

        statuses = [status for status, _ in store.upserts]
        assert statuses == ["running", "running", "succeeded"]
        assert store.upserts[1][1]["done"] == 1

    run(scenario())


def test_store_failure_does_not_fail_the_job() -> None:
    async def scenario():
        manager = JobManager(store=FakeStore(fail=True))

        async def runner(progress):
            await progress.update(stage="enriching", done=1, total=1)
            return {"ok": True}

        record = manager.start("csv_upload", runner)
        while manager.get(record.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)
        assert manager.get(record.id).status == "succeeded"

    run(scenario())


def test_job_failure_records_error() -> None:
    async def scenario():
        manager = JobManager()

        async def runner(progress):
            raise ValueError("boom")

        record = manager.start("csv_upload", runner)
        while manager.get(record.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)
        assert manager.get(record.id).status == "failed"
        assert manager.get(record.id).error == "boom"

    run(scenario())


def test_job_manager_deduplicates_active_key_and_releases_it_when_done() -> None:
    async def scenario():
        manager = JobManager()
        gate = asyncio.Event()

        async def runner(progress):
            await gate.wait()
            return {"ok": True}

        first = manager.start("event_backfill", runner, key="event:123")
        duplicate = manager.start("event_backfill", runner, key="event:123")
        assert duplicate.id == first.id

        gate.set()
        while manager.get(first.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)

        replacement = manager.start("event_backfill", runner, key="event:123")
        assert replacement.id != first.id
        while manager.get(replacement.id).status not in {"succeeded", "failed"}:
            await asyncio.sleep(0)
        assert manager._tasks == {}

    run(scenario())
