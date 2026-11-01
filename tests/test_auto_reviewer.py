import asyncio
from types import SimpleNamespace
from uuid import uuid4

from mint.backend.classification.classifier import load_icp_config
from mint.backend.db.classification_storage import AutoReviewRecord
from mint.backend.db.tables import Classification, Company, Person
from mint.backend.review.auto_reviewer import AutoReviewService


def run(coro):
    return asyncio.run(coro)


def person(email: str = "alex@quadex.ai", **overrides) -> Person:
    values = dict(
        id=uuid4(),
        email=email,
        first_name="Alex",
        last_name="Rivera",
        resolution_confidence="high",
        affiliation_state="verified",
        apollo_person_title="Founder",
        apollo_person_seniority="founder",
        exa_person_role_signals="Founder likely buyer.",
        self_reported_building="AI agents",
    )
    values.update(overrides)
    return Person(**values)


def company() -> Company:
    return Company(
        id=uuid4(),
        name="Quadex AI",
        domain="quadex.ai",
        exa_target_vertical="agentic",
        exa_stage="growth",
        exa_what_they_do="Quadex builds AI agents.",
        exa_data_quality="moderate",
        apollo_org_employee_count=40,
        apollo_org_industry="software",
        exa_enrichment={"what_they_do": "Quadex builds AI agents."},
    )


def classification(tier: str = "P1", np_trigger: str | None = None, flags=None) -> Classification:
    return Classification(
        id=uuid4(),
        tier=tier,
        np_trigger=np_trigger,
        product_fit=["maas"],
        primary_product="maas",
        confidence=2,
        reasoning="Original reasoning.",
        key_signal="Signal.",
        key_risk="Risk.",
        flags=flags or [],
        review_status="NEEDS_REVIEW",
        pipeline_version="test",
    )


def record(tier: str = "P1", np_trigger: str | None = None, email: str = "alex@quadex.ai") -> AutoReviewRecord:
    return AutoReviewRecord(person=person(email=email), company=company(), classification=classification(tier, np_trigger))


class FakeStorage:
    def __init__(self, reviews=None, flagged=None) -> None:
        self.reviews = list(reviews or [])
        self.flagged = list(flagged or [])
        self.review_marks: list[tuple] = []
        self.resolve_marks: list[tuple] = []

    async def pending_auto_reviews(self, *, limit: int = 200):
        return self.reviews[:limit]

    async def mark_auto_review(self, classification_id, *, status, note=None):
        self.review_marks.append((classification_id, status, note))

    async def pending_flagged_resolutions(self, *, limit: int = 200):
        return self.flagged[:limit]

    async def mark_auto_resolve_attempt(self, person_id, *, note=None):
        self.resolve_marks.append((person_id, note))


class FakeReadStorage:
    def __init__(self) -> None:
        self.reclassified: list[dict] = []
        self.resolved: list[tuple] = []

    async def reclassify_person(self, person_id, *, new_tier, reason, reviewed_by=None, mark_human_review=True):
        self.reclassified.append(
            {
                "person_id": person_id,
                "new_tier": new_tier,
                "reason": reason,
                "reviewed_by": reviewed_by,
                "mark_human_review": mark_human_review,
            }
        )
        return SimpleNamespace(current_classification=SimpleNamespace(tier=new_tier))

    async def resolve_flagged_person(self, person_id, *, chosen_domain=None, **kwargs):
        self.resolved.append((person_id, chosen_domain))
        return SimpleNamespace(person_id=person_id, domain=chosen_domain)

    async def enrichment_context_for_person(self, person_id):
        return None


class FakeClassificationService:
    def __init__(self) -> None:
        self.scopes: list = []

    async def classify_batch(self, scope, on_progress=None, max_concurrent: int = 10):
        self.scopes.append(scope)
        return SimpleNamespace(model_dump=lambda mode="json": {})


class FakeReviewLlm:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list = []

    async def classify_tier(self, context, *, system_prompt, model=None):
        self.calls.append((context, system_prompt))
        return self.responses.pop(0)


def service(storage, llm=None, read_storage=None, classification_service=None, max_llm_reviews=200):
    return AutoReviewService(
        storage,
        read_storage or FakeReadStorage(),
        llm or FakeReviewLlm([]),
        load_icp_config(),
        classification_service=classification_service,
        max_llm_reviews=max_llm_reviews,
    )


def test_structural_np_is_confirmed_without_llm() -> None:
    storage = FakeStorage(reviews=[record(tier="NP", np_trigger="bigtech_or_large_corp")])
    llm = FakeReviewLlm([])

    summary = run(service(storage, llm).sweep())

    assert summary.reviews_confirmed == 1
    assert llm.calls == []
    assert storage.review_marks[0][1] == "confirmed"


def test_llm_confirm_clears_review_flag() -> None:
    item = record()
    storage = FakeStorage(reviews=[item])
    llm = FakeReviewLlm([{"verdict": "confirm", "tier": "P1", "reasoning": "Tier is right."}])

    summary = run(service(storage, llm).sweep())

    assert summary.reviews_confirmed == 1
    assert storage.review_marks == [(item.classification.id, "confirmed", "Tier is right.")]
    context, prompt = llm.calls[0]
    assert context["existing_classification"]["tier"] == "P1"
    assert "confirm" in prompt


def test_llm_adjust_records_note_without_reclassifying() -> None:
    item = record(tier="P1")
    storage = FakeStorage(reviews=[item])
    read_storage = FakeReadStorage()
    llm = FakeReviewLlm([{"verdict": "adjust", "tier": "P0", "reasoning": "Founder with grounded need."}])

    summary = run(service(storage, llm, read_storage=read_storage).sweep())

    assert summary.reviews_adjusted == 1
    assert read_storage.reclassified == []
    assert storage.review_marks == [(item.classification.id, "adjusted", "Founder with grounded need.")]


def test_llm_uncertain_stays_in_queue_with_note() -> None:
    item = record()
    storage = FakeStorage(reviews=[item])
    llm = FakeReviewLlm([{"verdict": "uncertain", "tier": None, "reasoning": "Cannot tell from evidence."}])

    summary = run(service(storage, llm).sweep())

    assert summary.reviews_uncertain == 1
    assert storage.review_marks == [(item.classification.id, "uncertain", "Cannot tell from evidence.")]


def test_llm_budget_leaves_excess_items_pending() -> None:
    storage = FakeStorage(reviews=[record(email="a@x.ai"), record(email="b@x.ai")])
    llm = FakeReviewLlm([{"verdict": "confirm", "tier": "P1", "reasoning": "ok"}])

    summary = run(service(storage, llm, max_llm_reviews=1).sweep())

    assert summary.reviews_confirmed == 1
    assert len(llm.calls) == 1
    assert len(storage.review_marks) == 1


def test_flagged_conflict_resolves_to_candidate_domain_and_reclassifies() -> None:
    flagged = person(
        email="conflict@gmail.com",
        resolution_confidence="flagged",
        candidate_companies=[{"domain": "oldco.com", "name": "OldCo"}, {"domain": "quadex.ai", "name": "Quadex"}],
    )
    storage = FakeStorage(flagged=[flagged])
    read_storage = FakeReadStorage()
    classification_service = FakeClassificationService()
    llm = FakeReviewLlm([{"verdict": "resolve", "chosen_domain": "quadex.ai", "reasoning": "Self-report matches."}])

    summary = run(
        service(storage, llm, read_storage=read_storage, classification_service=classification_service).sweep()
    )

    assert summary.conflicts_resolved == 1
    assert read_storage.resolved == [(flagged.id, "quadex.ai")]
    assert classification_service.scopes[0].person_ids == [flagged.id]
    assert classification_service.scopes[0].force_reclassify is True
    assert storage.resolve_marks[0][0] == flagged.id


def test_flagged_conflict_with_unknown_domain_is_marked_uncertain() -> None:
    flagged = person(
        email="conflict@gmail.com",
        resolution_confidence="flagged",
        candidate_companies=[{"domain": "oldco.com"}],
    )
    storage = FakeStorage(flagged=[flagged])
    read_storage = FakeReadStorage()
    llm = FakeReviewLlm([{"verdict": "resolve", "chosen_domain": "made-up.ai", "reasoning": "Guess."}])

    summary = run(service(storage, llm, read_storage=read_storage).sweep())

    assert summary.conflicts_resolved == 0
    assert summary.conflicts_uncertain == 1
    assert read_storage.resolved == []
    assert storage.resolve_marks[0][0] == flagged.id


def test_flagged_conflict_uncertain_verdict_is_marked() -> None:
    flagged = person(
        email="conflict@gmail.com",
        resolution_confidence="flagged",
        candidate_companies=[{"domain": "a.ai"}, {"domain": "b.ai"}],
    )
    storage = FakeStorage(flagged=[flagged])
    read_storage = FakeReadStorage()
    llm = FakeReviewLlm([{"verdict": "uncertain", "chosen_domain": None, "reasoning": "Evidence is tied."}])

    summary = run(service(storage, llm, read_storage=read_storage).sweep())

    assert summary.conflicts_uncertain == 1
    assert read_storage.resolved == []
    assert storage.resolve_marks == [(flagged.id, "Evidence is tied.")]
