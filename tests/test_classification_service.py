import asyncio
from types import SimpleNamespace
from uuid import uuid4

from mint.backend.classification.service import ClassificationScope, ClassificationService


def run(coro):
    return asyncio.run(coro)


def record(email: str) -> SimpleNamespace:
    return SimpleNamespace(
        person=SimpleNamespace(id=uuid4(), email=email),
        company=SimpleNamespace(id=uuid4()),
    )


class FakeStorage:
    def __init__(self, records: list[SimpleNamespace]) -> None:
        self.records = records
        self.saved: list[tuple] = []

    async def get_classifiable_people(self, scope):
        return self.records

    async def save_classification(self, person_id, company_id, result):
        self.saved.append((person_id, company_id, result))
        return uuid4()


class TrackingClassifier:
    def __init__(self, fail_emails: set[str] | None = None, skip_emails: set[str] | None = None) -> None:
        self.fail_emails = fail_emails or set()
        self.skip_emails = skip_emails or set()
        self.in_flight = 0
        self.peak_in_flight = 0

    async def classify_person(self, person, company):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            if person.email in self.fail_emails:
                raise RuntimeError("llm exploded")
            if person.email in self.skip_emails:
                return None
            return SimpleNamespace(tier="P1", confidence=3)
        finally:
            self.in_flight -= 1


def test_classify_batch_runs_classifications_concurrently() -> None:
    records = [record(f"lead{i}@quadex.ai") for i in range(4)]
    classifier = TrackingClassifier()
    service = ClassificationService(FakeStorage(records), classifier)

    summary = run(service.classify_batch(ClassificationScope()))

    assert summary.classified_count == 4
    assert classifier.peak_in_flight > 1


def test_classify_batch_respects_max_concurrent() -> None:
    records = [record(f"lead{i}@quadex.ai") for i in range(6)]
    classifier = TrackingClassifier()
    service = ClassificationService(FakeStorage(records), classifier)

    run(service.classify_batch(ClassificationScope(), max_concurrent=2))

    assert classifier.peak_in_flight <= 2


def test_classify_batch_reports_progress_and_collects_errors() -> None:
    records = [record(f"lead{i}@quadex.ai") for i in range(3)]
    classifier = TrackingClassifier(fail_emails={"lead1@quadex.ai"}, skip_emails={"lead2@quadex.ai"})
    storage = FakeStorage(records)
    service = ClassificationService(storage, classifier)
    progress: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        progress.append((done, total))

    summary = run(service.classify_batch(ClassificationScope(), on_progress=on_progress))

    assert summary.total_count == 3
    assert summary.classified_count == 1
    assert summary.skipped_count == 1
    assert len(summary.errors) == 1
    assert "lead1@quadex.ai" in summary.errors[0]
    assert sorted(done for done, _ in progress) == [1, 2, 3]
    assert all(total == 3 for _, total in progress)
    assert len(storage.saved) == 1


def test_classify_batch_automatically_retries_transient_placeholder_with_low_concurrency() -> None:
    records = [record("retry@quadex.ai")]
    storage = FakeStorage(records)

    class RecoveringClassifier:
        llm_client = object()

        def __init__(self) -> None:
            self.calls = 0

        async def classify_person(self, person, company, *, evidence_mode="hybrid"):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    tier="NR",
                    confidence=1,
                    np_trigger="classification_failed",
                )
            return SimpleNamespace(tier="P1", confidence=3, np_trigger=None)

    classifier = RecoveringClassifier()
    service = ClassificationService(storage, classifier)

    summary = run(service.classify_batch(ClassificationScope()))

    assert summary.classified_count == 1
    assert classifier.calls == 2
    assert [saved[2].tier for saved in storage.saved] == ["NR", "P1"]
