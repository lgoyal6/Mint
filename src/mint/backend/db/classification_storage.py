from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.classification.classifier import FAILURE_NP_TRIGGERS
from mint.backend.db.tables import Classification, Company, Person, Registration
from mint.backend.models import ClassificationResult

if TYPE_CHECKING:
    from mint.backend.classification.service import ClassificationScope


@dataclass(frozen=True)
class ClassifiableRecord:
    person: Person
    company: Company


@dataclass(frozen=True)
class AutoReviewRecord:
    person: Person
    company: Company | None
    classification: Classification


class ClassificationStorage:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_classifiable_people(self, scope: "ClassificationScope") -> list[ClassifiableRecord]:
        async with self.session_factory() as session:
            stmt = (
                select(Person, Company)
                .outerjoin(Company, Person.company_id == Company.id)
                .outerjoin(Classification, Person.current_classification_id == Classification.id)
            )
            if scope.event_id:
                # An event classification request is exhaustive: every linked
                # registration receives a tier, including unresolved records.
                stmt = stmt.join(Registration, Registration.person_id == Person.id).where(
                    Registration.event_id == scope.event_id
                )
            else:
                stmt = stmt.where(
                    or_(
                        Person.resolution_confidence.in_(["high", "medium"]),
                        (
                            (Person.resolution_confidence == "low")
                            & (
                                Person.self_reported_company.is_not(None)
                                | Person.self_reported_building.is_not(None)
                                | Person.self_reported_title.is_not(None)
                            )
                        ),
                    )
                )
            if scope.force_reclassify:
                # A manual reclassification (reviewed_at set) must not be clobbered by the LLM.
                stmt = stmt.where(
                    or_(
                        Person.current_classification_id.is_(None),
                        Classification.reviewed_at.is_(None),
                    )
                )
            else:
                # Failure placeholders (LLM down, parse error) stay classifiable so a later run retries them.
                stmt = stmt.where(
                    or_(
                        Person.current_classification_id.is_(None),
                        Classification.np_trigger.in_(FAILURE_NP_TRIGGERS),
                    )
                )
            if scope.person_ids:
                stmt = stmt.where(Person.id.in_(scope.person_ids))
            if scope.limit:
                stmt = stmt.limit(scope.limit)
            rows = await session.execute(stmt)
            return [ClassifiableRecord(person=row[0], company=row[1]) for row in rows]

    async def pending_auto_reviews(self, *, limit: int = 200) -> list[AutoReviewRecord]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Person, Company, Classification)
                .join(Classification, Person.current_classification_id == Classification.id)
                .outerjoin(Company, Person.company_id == Company.id)
                .where(
                    Classification.review_status == "NEEDS_REVIEW",
                    Classification.auto_review_status.is_(None),
                )
                .order_by(Classification.classified_at.desc().nulls_last())
                .limit(limit)
            )
            return [
                AutoReviewRecord(person=row[0], company=row[1], classification=row[2])
                for row in rows
            ]

    async def mark_auto_review(self, classification_id: UUID, *, status: str, note: str | None = None) -> None:
        values: dict = {
            "auto_review_status": status,
            "auto_reviewed_at": func.now(),
            "auto_review_note": note,
        }
        if status == "confirmed":
            values["review_status"] = "CLASSIFIED"
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Classification).where(Classification.id == classification_id).values(**values)
                )

    async def pending_flagged_resolutions(self, *, limit: int = 200) -> list[Person]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Person)
                .where(
                    Person.resolution_confidence == "flagged",
                    Person.review_dismissed_at.is_(None),
                    Person.auto_resolve_attempted_at.is_(None),
                )
                .order_by(Person.updated_at.desc())
                .limit(limit)
            )
            return list(rows.scalars())

    async def mark_auto_resolve_attempt(self, person_id: UUID, *, note: str | None = None) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Person)
                    .where(Person.id == person_id)
                    .values(auto_resolve_attempted_at=func.now(), auto_resolve_note=note)
                )

    async def reset_all_classifications(self, *, dismiss_unresolved: bool) -> dict[str, int]:
        async with self.session_factory() as session:
            async with session.begin():
                people_reset = (
                    await session.execute(
                        update(Person)
                        .where(Person.current_classification_id.is_not(None))
                        .values(current_tier=None, current_confidence=None, current_classification_id=None)
                    )
                ).rowcount
                classifications_deleted = (await session.execute(delete(Classification))).rowcount
                people_dismissed = 0
                if dismiss_unresolved:
                    people_dismissed = (
                        await session.execute(
                            update(Person)
                            .where(
                                Person.resolution_confidence.in_(["flagged", "low"]),
                                Person.review_dismissed_at.is_(None),
                            )
                            .values(review_dismissed_at=func.now())
                        )
                    ).rowcount
        return {
            "classifications_deleted": int(classifications_deleted or 0),
            "people_reset": int(people_reset or 0),
            "people_dismissed": int(people_dismissed or 0),
        }

    async def save_classification(
        self,
        person_id: UUID,
        company_id: UUID | None,
        result: ClassificationResult,
    ) -> UUID:
        async with self.session_factory() as session:
            async with session.begin():
                classification_id = (
                    await session.execute(
                        insert(Classification)
                        .values(
                            person_id=person_id,
                            company_id=company_id,
                            tier=result.tier,
                            np_trigger=result.np_trigger,
                            product_fit=result.product_fit,
                            primary_product=result.primary_product,
                            confidence=result.confidence,
                            reasoning=result.reasoning,
                            key_signal=result.key_signal,
                            key_risk=result.key_risk,
                            flags=result.flags,
                            review_status=result.review_status,
                            pipeline_version=result.pipeline_version,
                        )
                        .returning(Classification.id)
                    )
                ).scalar_one()
                await self.update_person_current_classification(
                    person_id,
                    classification_id,
                    result.tier,
                    result.confidence,
                    session=session,
                )
                return classification_id

    async def update_person_current_classification(
        self,
        person_id: UUID,
        classification_id: UUID,
        tier: str,
        confidence: int,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        if session is not None:
            await session.execute(
                update(Person)
                .where(Person.id == person_id)
                .values(
                    current_tier=tier,
                    current_confidence=confidence,
                    current_classification_id=classification_id,
                )
            )
            return
        async with self.session_factory() as owned_session:
            async with owned_session.begin():
                await self.update_person_current_classification(
                    person_id,
                    classification_id,
                    tier,
                    confidence,
                    session=owned_session,
                )
