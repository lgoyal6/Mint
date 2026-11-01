from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, distinct, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.classification.classifier import funding_stage_details
from mint.backend.db.tables import Classification, Company, EnrichmentLog, Event, EventRating, Person, Registration
from mint.backend.exports.leads import ExportProfile, LeadExportData, LeadExportRow, LeadExportSummaryRow
from mint.backend.models import ApolloPersonResult, CandidateCompany, EnrichmentInput, ResolvedIdentity


TARGET_OR_ADJACENT_VERTICALS = {
    "research_labs",
    "coding_platforms",
    "agentic",
    "data_labeling",
    "robotics",
    "video_ai_multimodal",
    "adjacent",
    "autonomous_vehicles",
    "cv_vision_platforms",
}
SQL_READY_TIERS = ("P0", "P1")
MQL_TIERS = ("P0", "P1", "P2")
ALL_TIERS = ("P0", "P1", "P2", "NR", "NP")
APOLLO_USAGE_SOURCES = ("apollo", "apollo_person", "apollo_org")
EXA_USAGE_SOURCES = ("exa", "exa_deep", "exa_people", "exa_company_search", "exa_profile_affiliation")
USAGE_SOURCES = (*APOLLO_USAGE_SOURCES, *EXA_USAGE_SOURCES)

# Event scorecard radar axes. Auto axes are computed from event data; manual axes
# are 1-5 ratings entered by the team and rescaled to 0-100 (×20) for the radar.
AUTO_AXES: list[dict[str, str]] = [
    {"key": "turnout", "label": "Turnout"},
    {"key": "audience_fit", "label": "Audience Fit"},
]
MANUAL_AXES: list[dict[str, str]] = [
    {"key": "engagement", "label": "Engagement"},
    {"key": "content", "label": "Content"},
    {"key": "follow_up", "label": "Follow-up"},
]
MANUAL_RATING_KEYS = frozenset(axis["key"] for axis in MANUAL_AXES)


class LeadFilters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: str | None = None
    tiers: list[str] = Field(default_factory=list)
    primary_product: str | None = None
    event_id: UUID | None = None
    vertical: str | None = None
    min_confidence: int | None = None
    review_status: str | None = None
    resolution_confidence: str | None = None
    search: str | None = None


class LeadSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    name: str | None = None
    title: str | None = None
    resolved_company: str | None = None
    resolved_domain: str | None = None
    tier: str | None = None
    primary_product: str | None = None
    product_fit: list[str] = Field(default_factory=list)
    confidence: int | None = None
    target_vertical: str | None = None
    location: str | None = None
    region: str = "Needs location review"
    review_status: str | None = None
    key_signal: str | None = None
    current_classification_id: UUID | None = None
    person_id: UUID


class LeadPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int
    items: list[LeadSummary]


class PersonProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = None
    title: str | None = None
    seniority: str | None = None
    linkedin_url: str | None = None
    self_reported_title: str | None = None
    self_reported_company: str | None = None
    self_reported_building: str | None = None
    affiliation_state: str | None = None
    resolution_confidence: str | None = None
    resolution_evidence: dict[str, Any] = Field(default_factory=dict)
    candidate_companies: list[dict[str, Any]] = Field(default_factory=list)
    apollo_match_status: str | None = None
    apollo_person_data: dict[str, Any] | None = None
    exa_person_role_signals: str | None = None
    exa_people_search_results: list[dict[str, Any]] | None = None
    clay_person_data: dict[str, Any] | None = None
    events_registered: int = 0
    events_attended: int = 0
    last_event_at: datetime | None = None
    enrichment_data_quality: str | None = None


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    name: str | None = None
    domain: str
    apollo_org_industry: str | None = None
    apollo_org_employee_count: int | None = None
    apollo_org_founded_year: int | None = None
    apollo_org_funding_stage: str | None = None
    apollo_org_total_funding: str | None = None
    apollo_org_revenue: str | None = None
    apollo_org_technologies: list[str] = Field(default_factory=list)
    apollo_org_data: dict[str, Any] | None = None
    exa_what_they_do: str | None = None
    exa_target_vertical: str | None = None
    exa_stage: str | None = None
    exa_data_quality: str | None = None
    exa_enrichment: dict[str, Any] | None = None
    exa_grounding: list[dict[str, Any]] | None = None


class ClassificationProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    tier: str
    np_trigger: str | None = None
    product_fit: list[str] = Field(default_factory=list)
    primary_product: str | None = None
    confidence: int | None = None
    reasoning: str | None = None
    key_signal: str | None = None
    key_risk: str | None = None
    flags: list[str] = Field(default_factory=list)
    review_status: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    reclassified_to: str | None = None
    pipeline_version: str | None = None
    classified_at: datetime | None = None


class RegistrationProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    event_id: UUID
    event_name: str | None = None
    luma_event_id: str | None = None
    approval_status: str | None = None
    checked_in_at: datetime | None = None
    survey_responses: dict[str, Any] | None = None


class LeadDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person: PersonProfile
    company: CompanyProfile | None = None
    current_classification: ClassificationProfile | None = None
    classification_history: list[ClassificationProfile] = Field(default_factory=list)
    registrations: list[RegistrationProfile] = Field(default_factory=list)


class FlaggedLead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    email: str
    name: str | None = None
    title: str | None = None
    self_reported_company: str | None = None
    candidate_companies: list[dict[str, Any]] = Field(default_factory=list)
    resolution_evidence: dict[str, Any] = Field(default_factory=dict)
    event_names: list[str] = Field(default_factory=list)


class NeedsReviewLead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    email: str
    name: str | None = None
    title: str | None = None
    resolved_company: str | None = None
    resolved_domain: str | None = None
    tier: str | None = None
    confidence: int | None = None
    primary_product: str | None = None
    review_status: str | None = None
    resolution_confidence: str | None = None
    reason: str
    source: Literal["classification", "resolution"]
    event_names: list[str] = Field(default_factory=list)


class ReviewPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int
    items: list[FlaggedLead | NeedsReviewLead]


class ResolutionActionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    company_id: UUID
    domain: str
    company_name: str | None = None
    affiliation_state: str
    resolution_confidence: str


@dataclass(frozen=True)
class EnrichmentContext:
    input: EnrichmentInput
    person: ApolloPersonResult
    resolution: ResolvedIdentity


class ReadStorage:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], alias_store: Any = None) -> None:
        self.session_factory = session_factory
        self.alias_store = alias_store

    async def list_leads(
        self,
        filters: LeadFilters,
        *,
        sort: str = "tier",
        limit: int = 50,
        offset: int = 0,
    ) -> LeadPage:
        limit = _bounded_limit(limit)
        offset = max(offset, 0)
        async with self.session_factory() as session:
            stmt = (
                select(Person, Company, Classification)
                .join(Classification, Person.current_classification_id == Classification.id)
                .outerjoin(Company, Person.company_id == Company.id)
            )
            stmt = self._apply_lead_filters(stmt, filters)
            total = await _count_rows(session, stmt)
            rows = await session.execute(
                stmt.order_by(*_lead_order(sort)).limit(limit).offset(offset)
            )
            items = [_lead_summary(person, company, classification) for person, company, classification in rows]
        return LeadPage(total=total, items=items)

    async def export_leads(
        self,
        filters: LeadFilters,
        *,
        sort: str = "tier",
        profile: ExportProfile = "standard",
    ) -> LeadExportData:
        async with self.session_factory() as session:
            stmt = (
                select(Person, Company, Classification)
                .join(Classification, Person.current_classification_id == Classification.id)
                .outerjoin(Company, Person.company_id == Company.id)
            )
            stmt = self._apply_lead_filters(stmt, filters)
            result = await session.execute(stmt.order_by(*_lead_order(sort)))
            rows = list(result)
            people = [person for person, _, _ in rows]
            event_names = await _event_names_by_person(session, [person.id for person in people], filters)
            checked_in = await _checked_in_by_person(session, [person.id for person in people], filters)
            approval_statuses = await _approval_status_by_person(session, [person.id for person in people], filters)

        export_rows = [
            _lead_export_row(
                person,
                company,
                classification,
                event_names.get(person.id, []),
                approval_status=approval_statuses.get(person.id),
                checked_in=checked_in.get(person.id),
            )
            for person, company, classification in rows
        ]
        return LeadExportData(
            rows=export_rows,
            summary=_lead_export_summary(export_rows),
            profile=profile,
        )

    async def get_lead_detail(self, person_id: UUID) -> LeadDetail | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Person, Company, Classification)
                    .outerjoin(Company, Person.company_id == Company.id)
                    .outerjoin(Classification, Person.current_classification_id == Classification.id)
                    .where(Person.id == person_id)
                )
            ).one_or_none()
            if row is None:
                return None
            person, company, current_classification = row
            history_rows = await session.execute(
                select(Classification)
                .where(Classification.person_id == person_id)
                .order_by(Classification.classified_at.desc().nulls_last(), Classification.created_at.desc())
            )
            registration_rows = await session.execute(
                select(Registration, Event)
                .join(Event, Registration.event_id == Event.id)
                .where(Registration.person_id == person_id)
                .order_by(Event.event_date.desc().nulls_last(), Registration.created_at.desc())
            )
            history = [_classification_profile(classification) for classification in history_rows.scalars()]
            registrations = [
                _registration_profile(registration, event)
                for registration, event in registration_rows
            ]
        return LeadDetail(
            person=_person_profile(person),
            company=_company_profile(company) if company else None,
            current_classification=(
                _classification_profile(current_classification) if current_classification else None
            ),
            classification_history=history,
            registrations=registrations,
        )

    async def get_lead_detail_by_email(self, email: str) -> LeadDetail | None:
        normalized = email.strip().lower()
        if not normalized:
            return None
        async with self.session_factory() as session:
            person_id = await session.scalar(select(Person.id).where(func.lower(Person.email) == normalized))
        return await self.get_lead_detail(person_id) if person_id is not None else None

    async def save_clay_identity_data(self, person_id: UUID, data: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    update(Person)
                    .where(Person.id == person_id)
                    .values(clay_person_data=data, clay_enriched_at=func.now(), updated_at=func.now())
                )
                if result.rowcount == 0:
                    raise ValueError("person not found")

    async def list_flagged(
        self, *, event_id: UUID | None = None, limit: int = 50, offset: int = 0
    ) -> ReviewPage:
        limit = _bounded_limit(limit)
        offset = max(offset, 0)
        async with self.session_factory() as session:
            stmt = select(Person).where(
                Person.resolution_confidence == "flagged",
                Person.review_dismissed_at.is_(None),
            )
            if event_id:
                stmt = stmt.where(
                    Person.id.in_(
                        select(Registration.person_id).where(
                            Registration.event_id == event_id,
                            Registration.person_id.is_not(None),
                        )
                    )
                )
            total = await _count_rows(session, stmt)
            rows = await session.execute(stmt.order_by(Person.updated_at.desc()).limit(limit).offset(offset))
            people = list(rows.scalars())
            event_names = await _event_names_by_person(
                session, [person.id for person in people], LeadFilters(event_id=event_id)
            )
            items = [_flagged_lead(person, event_names.get(person.id, [])) for person in people]
        return ReviewPage(total=total, items=items)

    async def list_needs_review(
        self,
        *,
        queue_type: Literal["classification", "resolution", "all"] = "all",
        event_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ReviewPage:
        limit = _bounded_limit(limit)
        offset = max(offset, 0)
        items: list[NeedsReviewLead] = []
        async with self.session_factory() as session:
            classification_stmt = (
                select(Person, Company, Classification)
                .join(Classification, Person.current_classification_id == Classification.id)
                .outerjoin(Company, Person.company_id == Company.id)
                .where(Classification.review_status == "NEEDS_REVIEW")
                .order_by(Classification.classified_at.desc().nulls_last())
            )
            resolution_stmt = (
                select(Person, Company)
                .outerjoin(Company, Person.company_id == Company.id)
                .where(
                    Person.resolution_confidence == "low",
                    Person.review_dismissed_at.is_(None),
                    or_(
                        Person.company_id.is_not(None),
                        Company.exa_target_vertical.in_(TARGET_OR_ADJACENT_VERTICALS),
                        Person.self_reported_company.is_not(None),
                    ),
                )
                .order_by(Person.updated_at.desc())
            )
            if event_id:
                event_people = select(Registration.person_id).where(
                    Registration.event_id == event_id,
                    Registration.person_id.is_not(None),
                )
                classification_stmt = classification_stmt.where(Person.id.in_(event_people))
                resolution_stmt = resolution_stmt.where(Person.id.in_(event_people))

            if queue_type == "classification":
                total = await _count_rows(session, classification_stmt)
                rows = await session.execute(classification_stmt.limit(limit).offset(offset))
                row_items = list(rows)
                event_names = await _event_names_by_person(
                    session, [person.id for person, _, _ in row_items], LeadFilters(event_id=event_id)
                )
                items.extend(_needs_review_from_classification(person, company, classification, event_names.get(person.id, [])) for person, company, classification in row_items)
                return ReviewPage(total=total, items=items)

            if queue_type == "resolution":
                total = await _count_rows(session, resolution_stmt)
                rows = await session.execute(resolution_stmt.limit(limit).offset(offset))
                row_items = list(rows)
                event_names = await _event_names_by_person(
                    session, [person.id for person, _ in row_items], LeadFilters(event_id=event_id)
                )
                items.extend(_needs_review_from_resolution(person, company, event_names.get(person.id, [])) for person, company in row_items)
                return ReviewPage(total=total, items=items)

            total = await _count_rows(session, classification_stmt) + await _count_rows(session, resolution_stmt)
            page_fetch_limit = offset + limit
            classification_rows = await session.execute(classification_stmt.limit(page_fetch_limit))
            classification_items = list(classification_rows)
            resolution_rows = await session.execute(resolution_stmt.limit(page_fetch_limit))
            resolution_items = list(resolution_rows)
            person_ids = [person.id for person, _, _ in classification_items] + [person.id for person, _ in resolution_items]
            event_names = await _event_names_by_person(session, person_ids, LeadFilters(event_id=event_id))
            items.extend(
                _needs_review_from_classification(person, company, classification, event_names.get(person.id, []))
                for person, company, classification in classification_items
            )
            items.extend(_needs_review_from_resolution(person, company, event_names.get(person.id, [])) for person, company in resolution_items)
        deduped = _dedupe_review_items(items)
        return ReviewPage(total=total, items=deduped[offset : offset + limit])

    async def clear_review_queues(self, *, event_id: UUID | None = None) -> dict[str, int]:
        person_filter = None
        if event_id:
            person_filter = select(Registration.person_id).where(
                Registration.event_id == event_id,
                Registration.person_id.is_not(None),
            )
        async with self.session_factory() as session:
            async with session.begin():
                people_stmt = (
                    update(Person)
                    .where(
                        Person.review_dismissed_at.is_(None),
                        Person.resolution_confidence.in_(["flagged", "low"]),
                    )
                    .values(review_dismissed_at=func.now(), updated_at=func.now())
                )
                classifications_stmt = (
                    update(Classification)
                    .where(Classification.review_status == "NEEDS_REVIEW")
                    .values(review_status="REVIEWED", reviewed_by="bulk-clear", reviewed_at=func.now())
                )
                if person_filter is not None:
                    people_stmt = people_stmt.where(Person.id.in_(person_filter))
                    classifications_stmt = classifications_stmt.where(Classification.person_id.in_(person_filter))
                people = (await session.execute(people_stmt)).rowcount or 0
                classifications = (await session.execute(classifications_stmt)).rowcount or 0
        return {"people_dismissed": int(people), "classifications_reviewed": int(classifications)}

    async def resolve_flagged_person(
        self,
        person_id: UUID,
        *,
        chosen_domain: str | None = None,
        company_domain: str | None = None,
        company_name: str | None = None,
        chosen_company_id: UUID | None = None,
        remember_aliases: bool = False,
    ) -> ResolutionActionResult:
        async with self.session_factory() as session:
            async with session.begin():
                person = (
                    await session.execute(select(Person).where(Person.id == person_id).with_for_update())
                ).scalar_one_or_none()
                if person is None:
                    raise ValueError("person not found")
                candidate_domains = [
                    normalized
                    for candidate in person.candidate_companies or []
                    if (normalized := _normalize_domain(candidate.get("domain")))
                ]
                company_id, domain, name = await self._resolve_company_choice(
                    session,
                    person,
                    chosen_domain=chosen_domain,
                    company_domain=company_domain,
                    company_name=company_name,
                    chosen_company_id=chosen_company_id,
                )
                evidence = dict(person.resolution_evidence or {})
                chain = list(evidence.get("chain") or [])
                chain.append(f"Human selected {domain} as the correct company")
                chain.append("-> affiliation: verified")
                chain.append("-> confidence: high")
                evidence.update(
                    {
                        "person_state": evidence.get("person_state", "self_reported"),
                        "company_state": "verified",
                        "chain": chain,
                        "human_resolution": {
                            "domain": domain,
                            "company_name": name,
                            "resolved_at": datetime.now(UTC).isoformat(),
                        },
                    }
                )
                await session.execute(
                    update(Person)
                    .where(Person.id == person_id)
                    .values(
                        company_id=company_id,
                        affiliation_state="verified",
                        resolution_confidence="high",
                        resolution_evidence=evidence,
                        candidate_companies=[],
                        review_dismissed_at=None,
                        updated_at=func.now(),
                    )
                )
        if remember_aliases and self.alias_store is not None:
            await self.alias_store.remember(domain, candidate_domains)
        return ResolutionActionResult(
            person_id=person_id,
            company_id=company_id,
            domain=domain,
            company_name=name,
            affiliation_state="verified",
            resolution_confidence="high",
        )

    async def reclassify_person(
        self,
        person_id: UUID,
        *,
        new_tier: str,
        reason: str,
        reviewed_by: str | None = None,
        mark_human_review: bool = True,
    ) -> LeadDetail:
        async with self.session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(Person, Company, Classification)
                        .outerjoin(Company, Person.company_id == Company.id)
                        .outerjoin(Classification, Person.current_classification_id == Classification.id)
                        .where(Person.id == person_id)
                        .with_for_update(of=Person)
                    )
                ).one_or_none()
                if row is None:
                    raise ValueError("person not found")
                person, company, current = row
                confidence = 5
                classification_id = (
                    await session.execute(
                        insert(Classification)
                        .values(
                            person_id=person.id,
                            company_id=company.id if company else None,
                            tier=new_tier,
                            np_trigger="does_not_meet" if new_tier == "NP" else None,
                            product_fit=current.product_fit if current else [],
                            primary_product=current.primary_product if current else None,
                            confidence=confidence,
                            reasoning=reason,
                            key_signal="Manual review",
                            key_risk="Manual override",
                            flags=current.flags if current else [],
                            review_status="CLASSIFIED",
                            reviewed_by=reviewed_by,
                            # reviewed_at marks a HUMAN decision; the reclassify guard keys on it.
                            reviewed_at=datetime.now(UTC) if mark_human_review else None,
                            reclassified_to=new_tier,
                            pipeline_version="manual-review-v1" if mark_human_review else "auto-review-v1",
                        )
                        .returning(Classification.id)
                    )
                ).scalar_one()
                await session.execute(
                    update(Person)
                    .where(Person.id == person.id)
                    .values(
                        current_tier=new_tier,
                        current_confidence=confidence,
                        current_classification_id=classification_id,
                        updated_at=func.now(),
                    )
                )
        detail = await self.get_lead_detail(person_id)
        if detail is None:
            raise ValueError("person not found after reclassification")
        return detail

    async def enrichment_context_for_person(self, person_id: UUID) -> EnrichmentContext | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Person, Company)
                    .join(Company, Person.company_id == Company.id)
                    .where(Person.id == person_id)
                )
            ).one_or_none()
            latest_event_id = await session.scalar(
                select(Registration.event_id)
                .where(Registration.person_id == person_id)
                .order_by(Registration.created_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        person, company = row
        input = EnrichmentInput(
            email=person.email,
            first_name=person.first_name or _clay_person_value(person, "first_name"),
            last_name=person.last_name or _clay_person_value(person, "last_name"),
            company_name=company.name or person.self_reported_company,
            title=person.self_reported_title or _clay_person_value(person, "title"),
            linkedin_url=person.apollo_person_linkedin_url or _clay_person_value(person, "linkedin_url"),
            what_they_are_building=person.self_reported_building,
            source="review",
            event_id=str(latest_event_id) if latest_event_id else None,
        )
        apollo_person = ApolloPersonResult(
            matched=person.apollo_match_status == "matched",
            match_status=person.apollo_match_status or "not_found",
            email=person.email,
            first_name=person.first_name or _clay_person_value(person, "first_name"),
            last_name=person.last_name or _clay_person_value(person, "last_name"),
            name=_person_name(person),
            title=person.apollo_person_title or _clay_person_value(person, "title"),
            seniority=person.apollo_person_seniority,
            departments=list(person.apollo_person_departments or []),
            linkedin_url=person.apollo_person_linkedin_url or _clay_person_value(person, "linkedin_url"),
            location=person.apollo_person_location,
            organization_name=company.name,
            organization_domain=company.domain,
            raw_data=person.apollo_person_data or {},
        )
        resolution = ResolvedIdentity(
            person_source="apollo" if apollo_person.matched else "self_reported",
            name=_person_name(person),
            title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
            seniority=person.apollo_person_seniority,
            company_source="none",
            domain=company.domain,
            company_name=company.name,
            affiliation_state=person.affiliation_state or "verified",
            confidence=person.resolution_confidence or "high",
            evidence=person.resolution_evidence or {},
            candidate_companies=[
                CandidateCompany.model_validate(candidate)
                for candidate in person.candidate_companies or []
            ],
        )
        return EnrichmentContext(input=input, person=apollo_person, resolution=resolution)

    async def region_backfill_candidates(
        self, event_id: UUID, *, include_attempted: bool = False
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            attempted: set[UUID] = set()
            if not include_attempted:
                attempted = set(
                    (
                        await session.execute(
                            select(EnrichmentLog.entity_id).where(
                                EnrichmentLog.event_id == event_id,
                                EnrichmentLog.operation == "region_backfill",
                            )
                        )
                    ).scalars()
                )
            rows = await session.execute(
                select(Person, Company)
                .join(Registration, Registration.person_id == Person.id)
                .outerjoin(Company, Person.company_id == Company.id)
                .where(Registration.event_id == event_id)
                .order_by(Person.created_at)
            )
            candidates: list[dict[str, Any]] = []
            seen: set[UUID] = set()
            for person, company in rows:
                if person.id in seen or person.id in attempted or _best_location(person, company):
                    continue
                seen.add(person.id)
                candidates.append(
                    {
                        "person_id": person.id,
                        "email": person.email,
                        "first_name": person.first_name or _clay_person_value(person, "first_name"),
                        "last_name": person.last_name or _clay_person_value(person, "last_name"),
                        "company_name": company.name if company else person.self_reported_company,
                        "linkedin_url": person.apollo_person_linkedin_url,
                    }
                )
        return candidates

    async def api_usage_by_event(self) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            events = (
                await session.execute(select(Event).order_by(Event.event_date.desc().nulls_last(), Event.created_at.desc()))
            ).scalars().all()
            provider = case(
                (EnrichmentLog.source.in_(APOLLO_USAGE_SOURCES), "apollo"),
                (EnrichmentLog.source.in_(EXA_USAGE_SOURCES), "exa"),
                else_=EnrichmentLog.source,
            ).label("provider")
            usage_rows = (
                await session.execute(
                    select(
                        EnrichmentLog.event_id,
                        provider,
                        func.coalesce(func.sum(EnrichmentLog.requests), 0),
                        func.coalesce(func.sum(EnrichmentLog.credits), 0),
                        func.coalesce(func.sum(EnrichmentLog.estimated_cost_usd), 0),
                    )
                    .where(EnrichmentLog.event_id.is_not(None), EnrichmentLog.source.in_(USAGE_SOURCES))
                    .group_by(EnrichmentLog.event_id, provider)
                )
            ).all()

        totals: dict[UUID, dict[str, dict[str, float | int]]] = {}
        for event_id, provider, requests, credits, cost in usage_rows:
            totals.setdefault(event_id, {})[provider] = {
                "requests": int(requests or 0),
                "credits": float(credits or 0),
                "estimated_cost_usd": float(cost or 0),
            }
        empty = {"requests": 0, "credits": 0.0, "estimated_cost_usd": 0.0}
        return [
            {
                "event": {
                    "id": event.id,
                    "name": event.name,
                    "event_date": event.event_date,
                    "status": event.status,
                },
                "apollo": totals.get(event.id, {}).get("apollo", dict(empty)),
                "exa": totals.get(event.id, {}).get("exa", dict(empty)),
                "total_estimated_cost_usd": sum(
                    float(totals.get(event.id, {}).get(provider, {}).get("estimated_cost_usd", 0))
                    for provider in ("apollo", "exa")
                ),
            }
            for event in events
        ]

    async def event_summary(self, event_id: UUID) -> dict[str, Any]:
        async with self.session_factory() as session:
            event = (await session.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
            if event is None:
                raise ValueError("event not found")
            guest_counts = (
                await session.execute(
                    select(
                        func.count(Registration.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (Registration.approval_status.in_(["going", "approved"]), 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    )
                    .select_from(Registration)
                    .where(Registration.event_id == event_id)
                )
            ).one()
            resolution_counts = (
                await session.execute(
                    select(
                        func.count(
                            distinct(
                                case(
                                    (Person.resolution_confidence.in_(["high", "medium"]), Person.id),
                                    else_=None,
                                )
                            )
                        ),
                        func.count(
                            distinct(
                                case(
                                    (
                                        (Person.resolution_confidence == "low")
                                        & Person.review_dismissed_at.is_(None),
                                        Person.id,
                                    ),
                                    else_=None,
                                )
                            )
                        ),
                        func.count(
                            distinct(
                                case(
                                    (
                                        (Person.resolution_confidence == "flagged")
                                        & Person.review_dismissed_at.is_(None),
                                        Person.id,
                                    ),
                                    else_=None,
                                )
                            )
                        ),
                        func.count(
                            distinct(
                                case(
                                    (Person.current_classification_id.is_not(None), Person.id),
                                    else_=None,
                                )
                            )
                        ),
                    )
                    .select_from(Registration)
                    .join(Person, Registration.person_id == Person.id)
                    .where(Registration.event_id == event_id)
                )
            ).one()
            classification_rows = await session.execute(
                select(Classification)
                .join(Person, Person.current_classification_id == Classification.id)
                .join(Registration, Registration.person_id == Person.id)
                .where(Registration.event_id == event_id)
            )
            classifications = list(classification_rows.scalars())
        p0_p1 = await self.list_leads(
            LeadFilters(event_id=event_id),
            sort="tier",
            limit=200,
            offset=0,
        )
        trigger_values = [item.np_trigger for item in classifications if item.np_trigger]
        technical_triggers = {"classification_failed", "llm_unavailable", "parse_error"}
        evidence_triggers = {"sparse_data", "unresolved_event_guest"}
        return {
            "event": {
                "id": str(event.id),
                "luma_event_id": event.luma_event_id,
                "name": event.name,
                "status": event.status,
            },
            "counts": {
                "total_guests": int(guest_counts[0] or 0),
                "going_count": int(guest_counts[1] or 0),
                "resolved": int(resolution_counts[0] or 0),
                "parked_low": int(resolution_counts[1] or 0),
                "flagged": int(resolution_counts[2] or 0),
                "classified": int(resolution_counts[3] or 0),
            },
            "tier_breakdown": _count_by([item.tier for item in classifications if item.tier]),
            "product_fit_breakdown": _count_product_fit(classifications),
            "np_trigger_breakdown": _count_by(trigger_values),
            "processing_failure_breakdown": _count_by(
                [value for value in trigger_values if value in technical_triggers]
            ),
            "evidence_gap_breakdown": _count_by(
                [value for value in trigger_values if value in evidence_triggers]
            ),
            "qualification_outcome_breakdown": _count_by(
                [
                    value
                    for value in trigger_values
                    if value not in technical_triggers and value not in evidence_triggers
                ]
            ),
            "p0_p1_leads": [
                item.model_dump(mode="json") for item in p0_p1.items if item.tier in set(SQL_READY_TIERS)
            ],
            "resolution_funnel": {
                "high_medium": int(resolution_counts[0] or 0),
                "low": int(resolution_counts[1] or 0),
                "flagged": int(resolution_counts[2] or 0),
            },
        }

    async def event_analytics(self, event_id: UUID) -> dict[str, Any]:
        async with self.session_factory() as session:
            event = (await session.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
            if event is None:
                raise ValueError("event not found")
            going_count = await session.scalar(
                select(func.count())
                .select_from(Registration)
                .where(
                    Registration.event_id == event_id,
                    Registration.approval_status.in_(["going", "approved"]),
                )
            )
            checked_in = await session.scalar(
                select(func.count())
                .select_from(Registration)
                .where(
                    Registration.event_id == event_id,
                    Registration.approval_status.in_(["going", "approved"]),
                    Registration.checked_in_at.is_not(None),
                )
            )
            p0_p1 = await session.scalar(
                select(func.count(distinct(Person.id)))
                .select_from(Registration)
                .join(Person, Registration.person_id == Person.id)
                .where(
                    Registration.event_id == event_id,
                    Registration.approval_status.in_(["going", "approved"]),
                    Person.current_tier.in_(SQL_READY_TIERS),
                )
            )
            rating = (
                await session.execute(select(EventRating).where(EventRating.event_id == event_id))
            ).scalar_one_or_none()
        return _event_analytics_payload(
            event={
                "id": str(event.id),
                "name": event.name,
                "event_date": event.event_date.isoformat() if event.event_date else None,
                "status": event.status,
            },
            going=int(going_count or 0),
            checked_in=int(checked_in or 0),
            p0_p1=int(p0_p1 or 0),
            scores=dict(rating.scores or {}) if rating is not None else None,
            notes=rating.notes if rating is not None else None,
        )

    async def event_scorecards(self) -> list[dict[str, Any]]:
        """Compact auto-axis scorecards for every event, for cross-event comparison."""
        async with self.session_factory() as session:
            events = list(
                (
                    await session.execute(
                        select(Event).order_by(Event.event_date.desc().nulls_last(), Event.created_at.desc())
                    )
                ).scalars()
            )
            attendance_rows = await session.execute(
                select(
                    Registration.event_id,
                    func.count(Registration.id),
                    func.count(Registration.checked_in_at),
                )
                .where(Registration.approval_status.in_(["going", "approved"]))
                .group_by(Registration.event_id)
            )
            attendance = {row[0]: (int(row[1]), int(row[2])) for row in attendance_rows}
            fit_rows = await session.execute(
                select(Registration.event_id, func.count(distinct(Person.id)))
                .join(Person, Registration.person_id == Person.id)
                .where(
                    Registration.approval_status.in_(["going", "approved"]),
                    Person.current_tier.in_(SQL_READY_TIERS),
                )
                .group_by(Registration.event_id)
            )
            fit = {row[0]: int(row[1]) for row in fit_rows}
            ratings = {
                rating.event_id: dict(rating.scores or {})
                for rating in (await session.execute(select(EventRating))).scalars()
            }
        cards: list[dict[str, Any]] = []
        for event in events:
            going, checked_in = attendance.get(event.id, (0, 0))
            scores = ratings.get(event.id)
            radar = _radar_points(
                turnout=_ratio_pct(checked_in, going),
                audience_fit=_ratio_pct(fit.get(event.id, 0), going),
                scores=scores,
            )
            cards.append(
                {
                    "event_id": str(event.id),
                    "name": event.name,
                    "event_date": event.event_date.isoformat() if event.event_date else None,
                    "going": going,
                    "overall_score": _overall_score(radar),
                    "axes": {point["axis"]: point["score"] for point in radar},
                }
            )
        return cards

    async def pipeline_summary(self) -> dict[str, Any]:
        async with self.session_factory() as session:
            primary_rows = await session.execute(
                select(Classification.primary_product, func.count())
                .where(
                    Classification.tier.in_(SQL_READY_TIERS),
                    Classification.primary_product.is_not(None),
                )
                .group_by(Classification.primary_product)
            )
            repeat_rows = await session.execute(
                select(Person.id, Person.email, Person.events_registered, Person.events_attended)
                .where(or_(Person.events_registered >= 2, Person.events_attended >= 2))
                .order_by(Person.events_attended.desc(), Person.events_registered.desc())
                .limit(50)
            )
            company_rows = await session.execute(
                select(Company.id, Company.name, Company.domain, func.count(Person.id).label("person_count"))
                .join(Person, Person.company_id == Company.id)
                .group_by(Company.id)
                .having(func.count(Person.id) > 1)
                .order_by(func.count(Person.id).desc())
                .limit(50)
            )
            nurture_rows = await session.execute(
                select(Person, Company, Classification)
                .join(Classification, Person.current_classification_id == Classification.id)
                .join(Company, Person.company_id == Company.id)
                .where(
                    Classification.tier.in_(["NR", "NP"]),
                    Classification.np_trigger.in_(
                        [
                            "pre_product",
                            "pre_revenue",
                            "sparse_data",
                            "low_confidence",
                            "insufficient_evidence",
                        ]
                    ),
                    Company.exa_target_vertical.in_(TARGET_OR_ADJACENT_VERTICALS),
                )
                .order_by(Classification.classified_at.desc().nulls_last())
                .limit(50)
            )
        return {
            "p0_p1_by_primary_product": {row[0]: int(row[1]) for row in primary_rows if row[0]},
            "repeat_attendees": [
                {
                    "person_id": str(row.id),
                    "email": row.email,
                    "events_registered": row.events_registered,
                    "events_attended": row.events_attended,
                }
                for row in repeat_rows
            ],
            "companies_with_multiple_people": [
                {
                    "company_id": str(row.id),
                    "name": row.name,
                    "domain": row.domain,
                    "person_count": int(row.person_count),
                }
                for row in company_rows
            ],
            "nurture_reengage": [
                _lead_summary(person, company, classification).model_dump(mode="json")
                for person, company, classification in nurture_rows
            ],
        }

    async def data_quality_summary(self) -> dict[str, Any]:
        async with self.session_factory() as session:
            total_people = await session.scalar(select(func.count()).select_from(Person))
            high_medium = await session.scalar(
                select(func.count()).select_from(Person).where(Person.resolution_confidence.in_(["high", "medium"]))
            )
            flagged = await session.scalar(
                select(func.count())
                .select_from(Person)
                .where(Person.resolution_confidence == "flagged", Person.review_dismissed_at.is_(None))
            )
            low = await session.scalar(
                select(func.count())
                .select_from(Person)
                .where(Person.resolution_confidence == "low", Person.review_dismissed_at.is_(None))
            )
            apollo_matches = await session.scalar(
                select(func.count()).select_from(Person).where(Person.apollo_match_status == "matched")
            )
            quality_rows = await session.execute(
                select(Person.enrichment_data_quality, func.count())
                .group_by(Person.enrichment_data_quality)
            )
            np_rows = await session.execute(
                select(Classification.np_trigger, func.count())
                .where(Classification.np_trigger.is_not(None))
                .group_by(Classification.np_trigger)
            )
            promising_low = await session.scalar(
                select(func.count())
                .select_from(Person)
                .outerjoin(Company, Person.company_id == Company.id)
                .where(
                    Person.resolution_confidence == "low",
                    Person.review_dismissed_at.is_(None),
                    or_(
                        Person.company_id.is_not(None),
                        Company.exa_target_vertical.in_(TARGET_OR_ADJACENT_VERTICALS),
                        Person.self_reported_company.is_not(None),
                    ),
                )
            )
            last_luma_sync_at = await session.scalar(
                select(func.max(Event.synced_at)).where(Event.luma_event_id.is_not(None))
            )
        return {
            "resolution": {
                "total_people": int(total_people or 0),
                "high_medium": int(high_medium or 0),
                "low": int(low or 0),
                "flagged": int(flagged or 0),
                "resolution_rate": _ratio(high_medium, total_people),
            },
            "apollo_match_rate": _ratio(apollo_matches, total_people),
            "data_quality_distribution": {row[0] or "unknown": int(row[1]) for row in quality_rows},
            "np_trigger_breakdown": {row[0]: int(row[1]) for row in np_rows if row[0]},
            "promising_low_confidence": int(promising_low or 0),
            "last_luma_sync_at": last_luma_sync_at.isoformat() if last_luma_sync_at else None,
        }

    def _apply_lead_filters(self, stmt, filters: LeadFilters):
        tiers = [tier for tier in filters.tiers if tier]
        if tiers:
            stmt = stmt.where(Classification.tier.in_(tiers))
        elif filters.tier:
            stmt = stmt.where(Classification.tier == filters.tier)
        if filters.primary_product:
            stmt = stmt.where(Classification.primary_product == filters.primary_product)
        if filters.event_id:
            stmt = stmt.join(Registration, Registration.person_id == Person.id).where(
                Registration.event_id == filters.event_id
            )
        if filters.vertical:
            stmt = stmt.where(Company.exa_target_vertical == filters.vertical)
        if filters.min_confidence is not None:
            stmt = stmt.where(Classification.confidence >= filters.min_confidence)
        if filters.review_status:
            stmt = stmt.where(Classification.review_status == filters.review_status)
        if filters.resolution_confidence:
            stmt = stmt.where(Person.resolution_confidence == filters.resolution_confidence)
        if filters.search:
            pattern = f"%{filters.search.strip()}%"
            stmt = stmt.where(
                or_(
                    Person.email.ilike(pattern),
                    Person.first_name.ilike(pattern),
                    Person.last_name.ilike(pattern),
                    Company.name.ilike(pattern),
                    Company.domain.ilike(pattern),
                )
            )
        return stmt

    async def _resolve_company_choice(
        self,
        session: AsyncSession,
        person: Person,
        *,
        chosen_domain: str | None,
        company_domain: str | None,
        company_name: str | None,
        chosen_company_id: UUID | None,
    ) -> tuple[UUID, str, str | None]:
        if chosen_company_id:
            company = (
                await session.execute(select(Company).where(Company.id == chosen_company_id))
            ).scalar_one_or_none()
            if company is None:
                raise ValueError("chosen company not found")
            return company.id, company.domain, company.name

        domain = _normalize_domain(company_domain or chosen_domain)
        name = company_name
        if chosen_domain and not company_domain:
            for candidate in person.candidate_companies or []:
                if _normalize_domain(candidate.get("domain")) == domain:
                    name = name or candidate.get("name")
                    break
        if not domain:
            raise ValueError("company domain is required")
        stmt = pg_insert(Company).values(domain=domain, name=name)
        return (
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[Company.domain],
                    set_={
                        "name": func.coalesce(stmt.excluded.name, Company.name),
                        "updated_at": func.now(),
                    },
                )
                .returning(Company.id, Company.domain, Company.name)
            )
        ).one()


def _ratio_pct(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round(numerator / denominator * 100)


def _radar_points(*, turnout: int, audience_fit: int, scores: dict[str, Any] | None) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = [
        {"axis": "Turnout", "score": turnout, "kind": "auto"},
        {"axis": "Audience Fit", "score": audience_fit, "kind": "auto"},
    ]
    for axis in MANUAL_AXES:
        raw = (scores or {}).get(axis["key"])
        if isinstance(raw, (int, float)):
            points.append({"axis": axis["label"], "score": int(raw) * 20, "kind": "manual"})
    return points


def _overall_score(radar: list[dict[str, Any]]) -> int | None:
    values = [point["score"] for point in radar]
    return round(sum(values) / len(values)) if values else None


def _event_analytics_payload(
    *,
    event: dict[str, Any],
    going: int,
    checked_in: int,
    p0_p1: int,
    scores: dict[str, Any] | None,
    notes: str | None,
) -> dict[str, Any]:
    turnout = _ratio_pct(checked_in, going)
    audience_fit = _ratio_pct(p0_p1, going)
    auto = [
        {
            "key": "turnout",
            "label": "Turnout",
            "value": turnout,
            "detail": f"{checked_in}/{going} checked in",
        },
        {
            "key": "audience_fit",
            "label": "Audience Fit",
            "value": audience_fit,
            "detail": f"{p0_p1}/{going} P0/P1 of attendees",
        },
    ]
    radar = _radar_points(turnout=turnout, audience_fit=audience_fit, scores=scores)
    manual_scores = (
        {axis["key"]: scores.get(axis["key"]) for axis in MANUAL_AXES} if scores is not None else None
    )
    return {
        "event": event,
        "auto": auto,
        "manual": {"scores": manual_scores, "notes": notes, "dimensions": MANUAL_AXES},
        "radar": radar,
        "overall_score": _overall_score(radar),
    }


async def _count_rows(session: AsyncSession, stmt) -> int:
    count_stmt = select(func.count()).select_from(stmt.order_by(None).limit(None).offset(None).subquery())
    return int((await session.execute(count_stmt)).scalar_one() or 0)


def _bounded_limit(limit: int) -> int:
    return max(1, min(limit, 200))


def _lead_order(sort: str):
    tier_order = case(
        (Classification.tier == "P0", 0),
        (Classification.tier == "P1", 1),
        (Classification.tier == "P2", 2),
        (Classification.tier == "NR", 3),
        (Classification.tier == "NP", 4),
        else_=5,
    )
    if sort == "confidence":
        return [Classification.confidence.desc().nulls_last(), tier_order, Person.created_at.desc()]
    if sort == "created_at":
        return [Person.created_at.desc()]
    if sort == "company":
        return [Company.name.asc().nulls_last(), tier_order, Classification.confidence.desc().nulls_last()]
    if sort == "name":
        return [Person.first_name.asc().nulls_last(), Person.last_name.asc().nulls_last()]
    return [tier_order, Classification.confidence.desc().nulls_last(), Person.created_at.desc()]


def _lead_summary(person: Person, company: Company | None, classification: Classification) -> LeadSummary:
    location = _best_location(person, company)
    return LeadSummary(
        email=person.email,
        name=_person_name(person),
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        resolved_company=company.name if company else person.self_reported_company,
        resolved_domain=company.domain if company else None,
        tier=classification.tier,
        primary_product=classification.primary_product,
        product_fit=list(classification.product_fit or []),
        confidence=classification.confidence,
        target_vertical=company.exa_target_vertical if company else None,
        location=location,
        region=_region_for_location(location),
        review_status=classification.review_status,
        key_signal=classification.key_signal,
        current_classification_id=person.current_classification_id,
        person_id=person.id,
    )


async def _event_names_by_person(
    session: AsyncSession,
    person_ids: list[UUID],
    filters: LeadFilters,
) -> dict[UUID, list[str]]:
    if not person_ids:
        return {}
    stmt = (
        select(Registration.person_id, Event.name)
        .join(Event, Registration.event_id == Event.id)
        .where(Registration.person_id.in_(person_ids))
        .order_by(Event.name.asc())
    )
    if filters.event_id:
        stmt = stmt.where(Registration.event_id == filters.event_id)
    rows = await session.execute(stmt)
    event_names: dict[UUID, list[str]] = {}
    for person_id, event_name in rows:
        if person_id is None:
            continue
        names = event_names.setdefault(person_id, [])
        if event_name not in names:
            names.append(event_name)
    return event_names


async def _checked_in_by_person(
    session: AsyncSession,
    person_ids: list[UUID],
    filters: LeadFilters,
) -> dict[UUID, bool]:
    if not person_ids:
        return {}
    stmt = (
        select(
            Registration.person_id,
            func.max(
                case(
                    (Registration.checked_in_at.is_not(None), 1),
                    else_=0,
                )
            ),
        )
        .where(Registration.person_id.in_(person_ids))
        .group_by(Registration.person_id)
    )
    if filters.event_id:
        stmt = stmt.where(Registration.event_id == filters.event_id)
    rows = await session.execute(stmt)
    return {person_id: bool(is_checked_in) for person_id, is_checked_in in rows if person_id is not None}


async def _approval_status_by_person(
    session: AsyncSession,
    person_ids: list[UUID],
    filters: LeadFilters,
) -> dict[UUID, str]:
    if not person_ids:
        return {}
    stmt = (
        select(Registration.person_id, Registration.approval_status)
        .where(Registration.person_id.in_(person_ids))
        .order_by(Registration.created_at.desc())
    )
    if filters.event_id:
        stmt = stmt.where(Registration.event_id == filters.event_id)
    rows = await session.execute(stmt)
    output: dict[UUID, str] = {}
    for person_id, approval_status in rows:
        if person_id is not None and person_id not in output and approval_status:
            output[person_id] = approval_status
    return output


def _lead_export_row(
    person: Person,
    company: Company | None,
    classification: Classification,
    event_names: list[str],
    *,
    approval_status: str | None = None,
    checked_in: bool | None = None,
) -> LeadExportRow:
    company_stage, stage_source, stage_confidence = funding_stage_details(
        company,
        person.self_reported_building,
    )
    return LeadExportRow(
        tier=classification.tier,
        primary_product=classification.primary_product,
        confidence=classification.confidence,
        review_status=classification.review_status,
        first_name=person.first_name or _clay_person_value(person, "first_name"),
        last_name=person.last_name or _clay_person_value(person, "last_name"),
        name=_person_name(person),
        email=person.email,
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        linkedin_url=person.apollo_person_linkedin_url or _clay_person_value(person, "linkedin_url"),
        company=company.name if company and company.name else person.self_reported_company,
        domain=company.domain if company else None,
        company_stage=company_stage,
        stage_source=stage_source,
        stage_confidence=stage_confidence,
        target_vertical=company.exa_target_vertical if company else None,
        what_they_are_building=person.self_reported_building,
        key_signal=classification.key_signal,
        key_risk=classification.key_risk,
        reasoning=classification.reasoning,
        product_fit=list(classification.product_fit or []),
        np_trigger=classification.np_trigger,
        event_names=event_names,
        approval_status=approval_status,
        checked_in=checked_in,
        apollo_match=person.apollo_match_status,
        resolution_confidence=person.resolution_confidence,
        data_quality=person.enrichment_data_quality,
        location=_best_location(person, company),
        region=_region_for_location(_best_location(person, company)),
    )


def _best_location(person: Person, company: Company | None) -> str | None:
    """Prefer the normalized Apollo location, then preserve useful raw Apollo fields."""
    if person.apollo_person_location:
        return person.apollo_person_location
    for payload in (person.apollo_person_data, company.apollo_org_data if company else None):
        location = _location_from_payload(payload)
        if location:
            return location
    return None


def _location_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("location") or payload.get("location_name")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for nested_key in ("organization", "organization_location"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            found = _location_from_payload(nested)
            if found:
                return found
    parts = [payload.get(key) for key in ("city", "state", "region", "country", "country_name")]
    text = ", ".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())
    return text or None


def _region_for_location(location: str | None) -> str:
    text = (location or "").lower()
    if not text:
        return "Needs location review"
    if any(country in text for country in ("united states", "usa", "u.s.", "canada")):
        return "North America"
    if any(country in text for country in ("mexico", "brazil", "argentina", "chile", "colombia", "peru")):
        return "Latin America"
    if any(country in text for country in ("united kingdom", "uk", "england", "france", "germany", "spain", "italy", "netherlands", "sweden", "switzerland", "ireland", "poland", "portugal", "denmark", "norway", "finland")):
        return "Europe"
    if any(country in text for country in ("india", "china", "japan", "south korea", "korea", "singapore", "taiwan", "hong kong", "australia", "new zealand", "indonesia", "philippines", "vietnam", "thailand")):
        return "APAC"
    if any(country in text for country in ("israel", "united arab emirates", "uae", "saudi", "qatar", "turkey", "egypt", "south africa", "nigeria", "kenya", "morocco")):
        return "Middle East & Africa"
    return "Other"


def _lead_export_summary(rows: list[LeadExportRow]) -> list[LeadExportSummaryRow]:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        event_names = row.event_names or ["Unattributed"]
        for event_name in event_names:
            event_counts = counts.setdefault(
                event_name,
                {"classified_rows": 0, "P0": 0, "P1": 0, "P2": 0, "NR": 0, "NP": 0},
            )
            event_counts["classified_rows"] += 1
            if row.tier in set(ALL_TIERS):
                event_counts[row.tier] += 1

    summary = [
        LeadExportSummaryRow(
            event=event,
            classified_rows=values["classified_rows"],
            p0=values["P0"],
            p1=values["P1"],
            p2=values["P2"],
            nr=values["NR"],
            np=values["NP"],
        )
        for event, values in counts.items()
    ]
    if len(summary) > 1:
        summary.append(
            LeadExportSummaryRow(
                event="Total",
                classified_rows=len(rows),
                p0=sum(1 for row in rows if row.tier == "P0"),
                p1=sum(1 for row in rows if row.tier == "P1"),
                p2=sum(1 for row in rows if row.tier == "P2"),
                nr=sum(1 for row in rows if row.tier == "NR"),
                np=sum(1 for row in rows if row.tier == "NP"),
            )
        )
    return summary


def _person_profile(person: Person) -> PersonProfile:
    return PersonProfile(
        id=person.id,
        email=person.email,
        first_name=person.first_name or _clay_person_value(person, "first_name"),
        last_name=person.last_name or _clay_person_value(person, "last_name"),
        name=_person_name(person),
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        seniority=person.apollo_person_seniority,
        linkedin_url=person.apollo_person_linkedin_url or _clay_person_value(person, "linkedin_url"),
        self_reported_title=person.self_reported_title,
        self_reported_company=person.self_reported_company,
        self_reported_building=person.self_reported_building,
        affiliation_state=person.affiliation_state,
        resolution_confidence=person.resolution_confidence,
        resolution_evidence=person.resolution_evidence or {},
        candidate_companies=list(person.candidate_companies or []),
        apollo_match_status=person.apollo_match_status,
        apollo_person_data=person.apollo_person_data,
        exa_person_role_signals=person.exa_person_role_signals,
        exa_people_search_results=person.exa_people_search_results,
        clay_person_data=person.clay_person_data,
        events_registered=person.events_registered or 0,
        events_attended=person.events_attended or 0,
        last_event_at=person.last_event_at,
        enrichment_data_quality=person.enrichment_data_quality,
    )


def _company_profile(company: Company) -> CompanyProfile:
    return CompanyProfile(
        id=company.id,
        name=company.name,
        domain=company.domain,
        apollo_org_industry=company.apollo_org_industry,
        apollo_org_employee_count=company.apollo_org_employee_count,
        apollo_org_founded_year=company.apollo_org_founded_year,
        apollo_org_funding_stage=company.apollo_org_funding_stage,
        apollo_org_total_funding=company.apollo_org_total_funding,
        apollo_org_revenue=company.apollo_org_revenue,
        apollo_org_technologies=list(company.apollo_org_technologies or []),
        apollo_org_data=company.apollo_org_data,
        exa_what_they_do=company.exa_what_they_do,
        exa_target_vertical=company.exa_target_vertical,
        exa_stage=company.exa_stage,
        exa_data_quality=company.exa_data_quality,
        exa_enrichment=company.exa_enrichment,
        exa_grounding=company.exa_grounding,
    )


def _classification_profile(classification: Classification) -> ClassificationProfile:
    return ClassificationProfile(
        id=classification.id,
        tier=classification.tier,
        np_trigger=classification.np_trigger,
        product_fit=list(classification.product_fit or []),
        primary_product=classification.primary_product,
        confidence=classification.confidence,
        reasoning=classification.reasoning,
        key_signal=classification.key_signal,
        key_risk=classification.key_risk,
        flags=list(classification.flags or []),
        review_status=classification.review_status,
        reviewed_by=classification.reviewed_by,
        reviewed_at=classification.reviewed_at,
        reclassified_to=classification.reclassified_to,
        pipeline_version=classification.pipeline_version,
        classified_at=classification.classified_at,
    )


def _registration_profile(registration: Registration, event: Event) -> RegistrationProfile:
    return RegistrationProfile(
        id=registration.id,
        event_id=registration.event_id,
        event_name=event.name,
        luma_event_id=event.luma_event_id,
        approval_status=registration.approval_status,
        checked_in_at=registration.checked_in_at,
        survey_responses=registration.survey_responses,
    )


def _flagged_lead(person: Person, event_names: list[str]) -> FlaggedLead:
    return FlaggedLead(
        person_id=person.id,
        email=person.email,
        name=_person_name(person),
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        self_reported_company=person.self_reported_company,
        candidate_companies=list(person.candidate_companies or []),
        resolution_evidence=person.resolution_evidence or {},
        event_names=event_names,
    )


def _needs_review_from_classification(
    person: Person,
    company: Company | None,
    classification: Classification,
    event_names: list[str],
) -> NeedsReviewLead:
    return NeedsReviewLead(
        person_id=person.id,
        email=person.email,
        name=_person_name(person),
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        resolved_company=company.name if company else person.self_reported_company,
        resolved_domain=company.domain if company else None,
        tier=classification.tier,
        confidence=classification.confidence,
        primary_product=classification.primary_product,
        review_status=classification.review_status,
        resolution_confidence=person.resolution_confidence,
        reason=_with_auto_review_note(
            classification.key_risk or classification.reasoning or "classification needs review",
            classification,
        ),
        source="classification",
        event_names=event_names,
    )


def _with_auto_review_note(reason: str, classification: Classification) -> str:
    if classification.auto_review_status == "uncertain" and classification.auto_review_note:
        return f"{reason} AI reviewer: {classification.auto_review_note}"
    return reason


def _needs_review_from_resolution(
    person: Person, company: Company | None, event_names: list[str]
) -> NeedsReviewLead:
    return NeedsReviewLead(
        person_id=person.id,
        email=person.email,
        name=_person_name(person),
        title=person.apollo_person_title or _clay_person_value(person, "title") or person.self_reported_title,
        resolved_company=company.name if company else person.self_reported_company,
        resolved_domain=company.domain if company else None,
        resolution_confidence=person.resolution_confidence,
        reason="low-confidence resolution with a company signal",
        source="resolution",
        event_names=event_names,
    )


def _dedupe_review_items(items: list[NeedsReviewLead]) -> list[NeedsReviewLead]:
    seen: set[tuple[UUID, str]] = set()
    deduped = []
    for item in items:
        key = (item.person_id, item.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _clay_person_value(person: Person, key: str) -> str | None:
    data = person.clay_person_data or {}
    value = data.get(key) if isinstance(data, dict) else None
    text = str(value or "").strip()
    return text or None


def _person_name(person: Person) -> str | None:
    parts = [
        person.first_name or _clay_person_value(person, "first_name"),
        person.last_name or _clay_person_value(person, "last_name"),
    ]
    name = " ".join(part for part in parts if part)
    return name or None


def _normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    domain = value.strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    domain = domain.split("/", 1)[0]
    return domain or None


def _count_by(values: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _count_product_fit(classifications: list[Classification]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for classification in classifications:
        for product in classification.product_fit or []:
            counts[product] = counts.get(product, 0) + 1
    return counts


def _ratio(numerator: int | None, denominator: int | None) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator or 0) / float(denominator), 4)
