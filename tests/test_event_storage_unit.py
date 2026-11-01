import asyncio
from uuid import uuid4

from mint.backend.db.event_storage import EventStorage
from mint.backend.db.tables import Registration


def run(coro):
    return asyncio.run(coro)


def test_event_backfill_selects_pending_and_declined_registrations() -> None:
    event_id = uuid4()
    rows = [
        (
            Registration(
                event_id=event_id,
                email=f"{status}@example.com",
                approval_status=status,
                survey_responses={"Company": "Example AI"},
            ),
            None,
        )
        for status in ("approved", "pending", "declined")
    ]

    class Session:
        statement = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement):
            self.statement = statement
            return rows

    session = Session()
    storage = EventStorage(lambda: session)

    inputs = run(storage.enrichment_inputs_for_event(event_id))

    assert [lead.email for lead in inputs] == [
        "approved@example.com",
        "pending@example.com",
        "declined@example.com",
    ]
    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "approval_status IN" not in compiled
