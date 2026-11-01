import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.db.tables import Company, EnrichmentLog, Person
from mint.backend.enrichment.usage import current_usage
from mint.backend.models import (
    ApolloOrgResult,
    ApolloPersonResult,
    EnrichmentInput,
    EnrichmentResult,
    ExaEnrichmentResult,
)


logger = logging.getLogger(__name__)


def _sanitize_for_postgres(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_sanitize_for_postgres(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_for_postgres(item) for item in value)
    if isinstance(value, dict):
        return {
            _sanitize_for_postgres(key) if isinstance(key, str) else key: _sanitize_for_postgres(item)
            for key, item in value.items()
        }
    return value


@dataclass
class FreshnessInfo:
    email: str
    apollo_enriched_at: datetime | None
    exa_enriched_at: datetime | None


@dataclass
class CompanyFreshnessInfo:
    domain: str
    apollo_enriched_at: datetime | None
    exa_enriched_at: datetime | None
    exa_data_quality: str | None


class EnrichmentStorage:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_freshness(self, email: str) -> FreshnessInfo | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Person.email, Person.apollo_enriched_at, Person.exa_enriched_at).where(
                        Person.email == email.strip().lower()
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return FreshnessInfo(
            email=row.email,
            apollo_enriched_at=row.apollo_enriched_at,
            exa_enriched_at=row.exa_enriched_at,
        )

    async def get_company_freshness(self, domain: str) -> CompanyFreshnessInfo | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        Company.domain,
                        Company.apollo_enriched_at,
                        Company.exa_enriched_at,
                        Company.exa_data_quality,
                    ).where(Company.domain == domain.strip().lower())
                )
            ).one_or_none()
        if row is None:
            return None
        return CompanyFreshnessInfo(
            domain=row.domain,
            apollo_enriched_at=row.apollo_enriched_at,
            exa_enriched_at=row.exa_enriched_at,
            exa_data_quality=row.exa_data_quality,
        )

    async def save_region_backfill_person(
        self,
        person_id: UUID,
        event_id: UUID,
        person: ApolloPersonResult,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                if person.matched:
                    values = self._apollo_person_values(person)
                    values.update(apollo_enriched_at=datetime.now(UTC), updated_at=func.now())
                    result = await session.execute(
                        update(Person).where(Person.id == person_id).values(**_sanitize_for_postgres(values))
                    )
                    if result.rowcount == 0:
                        raise ValueError("person not found")
                await session.execute(
                    insert(EnrichmentLog).values(
                        entity_type="person",
                        entity_id=person_id,
                        event_id=event_id,
                        source="apollo",
                        operation="region_backfill",
                        requests=1,
                        credits=1 if person.matched else 0,
                        estimated_cost_usd=0,
                        pricing_basis="Apollo person match for missing attendee location",
                        status="success" if person.matched else "not_found",
                    )
                )

    async def save(self, input: EnrichmentInput, result: EnrichmentResult) -> None:
        if result.enrichment_sources == ["skipped_fresh"]:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await self._log_skipped_fresh(session, input.email)
            except Exception:
                logger.warning("Could not record skipped-fresh enrichment usage", exc_info=True)
            return

        # Core person/company persistence must not be rolled back merely because
        # optional usage logging fails or the log schema is temporarily behind.
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(UTC)
                company_id = await self._upsert_company(session, input, result, now)
                person_id = await self._upsert_person(session, input, result, company_id, now)

        try:
            async with self.session_factory() as session:
                async with session.begin():
                    await self._insert_logs(session, input, result, person_id, company_id)
        except Exception:
            logger.warning("Saved enrichment result but could not record provider usage", exc_info=True)

    async def _upsert_company(
        self,
        session: AsyncSession,
        input: EnrichmentInput,
        result: EnrichmentResult,
        now: datetime,
    ) -> UUID | None:
        domain = result.resolution.domain
        if not result.enriched or not domain:
            return None

        values: dict[str, Any] = {
            "domain": domain,
            "name": self._company_name(result),
        }
        update_keys = {"name"}

        if result.company_apollo is not None:
            values.update(self._apollo_org_values(result.company_apollo, now))
            update_keys.update(values.keys() - {"domain"})

        if result.company_exa is not None:
            values.update(self._exa_company_values(result.company_exa, now))
            update_keys.update(values.keys() - {"domain"})

        values = _sanitize_for_postgres(values)
        stmt = pg_insert(Company).values(**values)
        update_values = {key: getattr(stmt.excluded, key) for key in update_keys if key != "domain"}
        update_values["updated_at"] = func.now()
        upsert = (
            stmt.on_conflict_do_update(
                index_elements=[Company.domain],
                set_=update_values,
            )
            .returning(Company.id)
        )
        return (await session.execute(upsert)).scalar_one()

    async def _upsert_person(
        self,
        session: AsyncSession,
        input: EnrichmentInput,
        result: EnrichmentResult,
        company_id: UUID | None,
        now: datetime,
    ) -> UUID:
        person = result.person
        trustworthy_company_link = result.resolution.affiliation_state in {"verified", "inferred"}
        linked_company_id = company_id if trustworthy_company_link else None
        values = {
            "email": input.email,
            "first_name": input.first_name or (person.first_name if person else None),
            "last_name": input.last_name or (person.last_name if person else None),
            "company_id": linked_company_id,
            "affiliation_state": result.resolution.affiliation_state,
            "resolution_confidence": result.resolution.confidence,
            "resolution_evidence": result.resolution.evidence,
            "candidate_companies": [
                candidate.model_dump(mode="json") for candidate in result.resolution.candidate_companies
            ],
            "enrichment_data_quality": result.data_quality,
            "self_reported_title": input.title,
            "self_reported_company": input.company_name,
            "self_reported_building": input.what_they_are_building,
            "exa_person_role_signals": (
                result.company_exa.person_role_signals if result.company_exa is not None else None
            ),
            "exa_people_search_results": result.people_search,
            "apollo_enriched_at": now if "apollo_person" in result.enrichment_sources else None,
            "exa_enriched_at": now
            if any(source in result.enrichment_sources for source in ("exa_deep", "exa_people"))
            else None,
        }
        values.update(self._apollo_person_values(person) if person else self._empty_apollo_person_values())
        if input.linkedin_url and not values.get("apollo_person_linkedin_url"):
            values["apollo_person_linkedin_url"] = input.linkedin_url

        values = _sanitize_for_postgres(values)
        stmt = pg_insert(Person).values(**values)
        preserved_columns = {
            "email",
            "first_seen_at",
            "current_tier",
            "current_confidence",
            "current_classification_id",
            "events_registered",
            "events_attended",
            "last_event_at",
            "created_at",
        }
        update_values = {
            key: getattr(stmt.excluded, key)
            for key in values
            if key not in preserved_columns
        }
        human_resolution = Person.resolution_evidence.op("?")("human_resolution")
        for key in {
            "company_id",
            "affiliation_state",
            "resolution_confidence",
            "resolution_evidence",
            "candidate_companies",
        }:
            update_values[key] = case(
                (human_resolution, getattr(Person, key)),
                else_=getattr(stmt.excluded, key),
            )
        update_values["apollo_enriched_at"] = func.coalesce(
            stmt.excluded.apollo_enriched_at,
            Person.apollo_enriched_at,
        )
        update_values["exa_enriched_at"] = func.coalesce(
            stmt.excluded.exa_enriched_at,
            Person.exa_enriched_at,
        )
        update_values["updated_at"] = func.now()
        upsert = (
            stmt.on_conflict_do_update(
                index_elements=[Person.email],
                set_=update_values,
            )
            .returning(Person.id)
        )
        return (await session.execute(upsert)).scalar_one()

    async def _insert_logs(
        self,
        session: AsyncSession,
        input: EnrichmentInput,
        result: EnrichmentResult,
        person_id: UUID,
        company_id: UUID | None,
    ) -> None:
        event_id = self._event_uuid(input.event_id)
        usage = current_usage()
        if usage:
            rows = [
                self._log_row(
                    "company" if item.operation in {"organization_enrichment", "deep_search"} else "person",
                    company_id if item.operation in {"organization_enrichment", "deep_search"} and company_id else person_id,
                    item.provider,
                    "success",
                    event_id=event_id,
                    operation=item.operation,
                    requests=item.requests,
                    credits=item.credits,
                    estimated_cost_usd=item.estimated_cost_usd,
                    pricing_basis=item.pricing_basis,
                )
                for item in usage
            ]
            await session.execute(insert(EnrichmentLog), _sanitize_for_postgres(rows))
            return

        rows = []
        for source in result.enrichment_sources:
            if source == "skipped_fresh":
                rows.append(self._log_row("person", person_id, source, "skipped_fresh"))
                continue
            entity_type, entity_id = self._entity_for_source(source, person_id, company_id)
            rows.append(
                self._log_row(
                    entity_type,
                    entity_id,
                    source,
                    "success",
                    event_id=event_id,
                    request_id=self._request_id_for_source(source, result),
                )
            )

        for error in result.errors:
            source = error.split(":", 1)[0].strip() or "unknown"
            entity_type, entity_id = self._entity_for_source(source, person_id, company_id)
            rows.append(self._log_row(entity_type, entity_id, source, "failed", event_id=event_id, error_message=error))

        if rows:
            await session.execute(insert(EnrichmentLog), _sanitize_for_postgres(rows))

    async def _log_skipped_fresh(self, session: AsyncSession, email: str) -> None:
        person_id = await session.scalar(select(Person.id).where(Person.email == email.strip().lower()))
        if person_id is None:
            return
        await session.execute(
            insert(EnrichmentLog),
            [_sanitize_for_postgres(self._log_row("person", person_id, "skipped_fresh", "skipped_fresh"))],
        )

    @staticmethod
    def _apollo_person_values(person: ApolloPersonResult) -> dict[str, Any]:
        return {
            "apollo_person_data": person.raw_data,
            "apollo_match_status": person.match_status,
            "apollo_person_title": person.title,
            "apollo_person_seniority": person.seniority,
            "apollo_person_departments": person.departments,
            "apollo_person_linkedin_url": person.linkedin_url,
            "apollo_person_location": person.location,
        }

    @staticmethod
    def _empty_apollo_person_values() -> dict[str, Any]:
        return {
            "apollo_person_data": None,
            "apollo_match_status": None,
            "apollo_person_title": None,
            "apollo_person_seniority": None,
            "apollo_person_departments": None,
            "apollo_person_linkedin_url": None,
            "apollo_person_location": None,
        }

    @staticmethod
    def _apollo_org_values(org: ApolloOrgResult, now: datetime) -> dict[str, Any]:
        return {
            "apollo_org_data": org.raw_data,
            "apollo_org_industry": org.industry,
            "apollo_org_employee_count": org.employee_count,
            "apollo_org_founded_year": org.founded_year,
            "apollo_org_funding_stage": org.funding_stage,
            "apollo_org_total_funding": org.total_funding,
            "apollo_org_revenue": org.revenue,
            "apollo_org_technologies": org.technologies,
            "apollo_org_headcount_growth_6mo": org.headcount_growth_6mo,
            "apollo_org_headcount_growth_12mo": org.headcount_growth_12mo,
            "apollo_enriched_at": now,
        }

    @staticmethod
    def _exa_company_values(exa: ExaEnrichmentResult, now: datetime) -> dict[str, Any]:
        return {
            "exa_enrichment": exa.model_dump(mode="json"),
            "exa_what_they_do": exa.what_they_do,
            "exa_target_vertical": exa.target_vertical,
            "exa_stage": exa.stage,
            "exa_data_quality": exa.data_quality,
            "exa_deep_raw": exa.raw_data,
            "exa_grounding": exa.exa_grounding,
            "exa_enriched_at": now,
        }

    @staticmethod
    def _company_name(result: EnrichmentResult) -> str | None:
        if result.company_apollo and result.company_apollo.name:
            return result.company_apollo.name
        return result.resolution.company_name

    @staticmethod
    def _entity_for_source(source: str, person_id: UUID, company_id: UUID | None) -> tuple[str, UUID]:
        if source in {"apollo_org", "exa_deep", "company_fresh"} and company_id is not None:
            return "company", company_id
        return "person", person_id

    @staticmethod
    def _request_id_for_source(source: str, result: EnrichmentResult) -> str | None:
        raw_data: dict[str, Any] | None = None
        if source == "apollo_person" and result.person is not None:
            raw_data = result.person.raw_data
        elif source == "apollo_org" and result.company_apollo is not None:
            raw_data = result.company_apollo.raw_data
        elif source == "exa_deep" and result.company_exa is not None:
            raw_data = result.company_exa.raw_data
        if not raw_data:
            return None
        value = raw_data.get("requestId") or raw_data.get("request_id")
        return str(value) if value else None

    @staticmethod
    def _log_row(
        entity_type: str,
        entity_id: UUID,
        source: str,
        status: str,
        *,
        event_id: UUID | None = None,
        operation: str | None = None,
        requests: int = 1,
        credits: Decimal | int = 0,
        estimated_cost_usd: Decimal | int = 0,
        pricing_basis: str | None = None,
        request_id: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "source": source,
            "event_id": event_id,
            "operation": operation,
            "requests": requests,
            "credits": credits,
            "estimated_cost_usd": estimated_cost_usd,
            "pricing_basis": pricing_basis,
            "status": status,
            "request_id": request_id,
            "error_message": error_message,
        }

    @staticmethod
    def _event_uuid(value: str | None) -> UUID | None:
        if not value:
            return None
        try:
            return UUID(value)
        except ValueError:
            return None
