import asyncio
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from mint.backend.classification.classifier import FAILURE_NP_TRIGGERS
from mint.backend.models import ClassifyResult


class ClassificationScope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID | None = None
    person_ids: list[UUID] | None = None
    force_reclassify: bool = False
    limit: int | None = None
    evidence_mode: Literal["hybrid", "self_reported"] = "hybrid"


class ClassificationService:
    def __init__(self, storage, classifier) -> None:
        self.storage = storage
        self.classifier = classifier

    async def classify_batch(
        self,
        scope: ClassificationScope,
        on_progress=None,
        max_concurrent: int = 10,
    ) -> ClassifyResult:
        records = await self.storage.get_classifiable_people(scope)
        semaphore = asyncio.Semaphore(max_concurrent)
        classified_count = 0
        skipped_count = 0
        done_count = 0
        errors: list[str] = []
        technical_failures = []

        async def run_one(record) -> None:
            nonlocal classified_count, skipped_count, done_count
            try:
                async with semaphore:
                    if scope.evidence_mode == "hybrid":
                        result = await self.classifier.classify_person(record.person, record.company)
                    else:
                        result = await self.classifier.classify_person(
                            record.person,
                            record.company,
                            evidence_mode=scope.evidence_mode,
                        )
                    if result is None:
                        skipped_count += 1
                        return
                    company_id = record.company.id if record.company is not None else None
                    await self.storage.save_classification(record.person.id, company_id, result)
                    classified_count += 1
                    if getattr(result, "np_trigger", None) in FAILURE_NP_TRIGGERS:
                        technical_failures.append(record)
            except Exception as exc:
                errors.append(f"{record.person.email}: {exc}")
            finally:
                done_count += 1
                if on_progress is not None:
                    await on_progress(done_count, len(records))

        await asyncio.gather(*(run_one(record) for record in records))
        if technical_failures and getattr(self.classifier, "llm_client", None) is not None:
            retry_semaphore = asyncio.Semaphore(2)

            async def retry_one(record) -> None:
                try:
                    async with retry_semaphore:
                        await asyncio.sleep(1)
                        result = await self.classifier.classify_person(
                            record.person,
                            record.company,
                            evidence_mode=scope.evidence_mode,
                        )
                    if result is not None:
                        company_id = record.company.id if record.company is not None else None
                        await self.storage.save_classification(record.person.id, company_id, result)
                except Exception as exc:
                    errors.append(f"{record.person.email}: automatic technical retry failed: {exc}")

            await asyncio.gather(*(retry_one(record) for record in technical_failures))

        return ClassifyResult(
            total_count=len(records),
            classified_count=classified_count,
            skipped_count=skipped_count,
            errors=errors,
        )
