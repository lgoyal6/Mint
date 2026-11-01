import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from mint.backend.models import (
    ApolloOrgResult,
    ApolloPersonResult,
    DataQuality,
    EnrichmentInput,
    EnrichmentResult,
    ExaEnrichmentResult,
    ResolvedIdentity,
)
from mint.backend.enrichment.usage import begin_usage, end_usage


@dataclass
class _CompanyEnrichment:
    apollo_org: ApolloOrgResult | None = None
    exa: ExaEnrichmentResult | None = None
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Set when company APIs were skipped because stored company data is still fresh.
    data_quality: DataQuality | None = None


class EnrichmentService:
    def __init__(
        self,
        apollo: Any,
        exa: Any,
        resolver: Any,
        storage: Any = None,
        ttl_days_company: int = 30,
        ttl_days_person_apollo: int = 90,
        ttl_days_person_exa: int = 30,
    ) -> None:
        self.apollo = apollo
        self.exa = exa
        self.resolver = resolver
        self.storage = storage
        self.ttl_days_company = ttl_days_company
        self.ttl_days_person_apollo = ttl_days_person_apollo
        self.ttl_days_person_exa = ttl_days_person_exa

    async def enrich_lead(self, input: EnrichmentInput) -> EnrichmentResult:
        return await self._enrich_lead(input)

    async def enrich_resolved_lead(
        self,
        input: EnrichmentInput,
        resolution: ResolvedIdentity,
        person: ApolloPersonResult | None = None,
        *,
        refresh_person: bool = False,
    ) -> EnrichmentResult:
        usage_token = begin_usage()
        try:
            return await self._enrich_resolved_lead(input, resolution, person, refresh_person=refresh_person)
        finally:
            end_usage(usage_token)

    async def _enrich_resolved_lead(
        self,
        input: EnrichmentInput,
        resolution: ResolvedIdentity,
        person: ApolloPersonResult | None = None,
        *,
        refresh_person: bool = False,
    ) -> EnrichmentResult:
        errors: list[str] = []
        sources: list[str] = []
        if refresh_person:
            refreshed_person, apollo_person_ok = await self._match_person(input, errors)
            if apollo_person_ok and refreshed_person.matched:
                person = refreshed_person
                sources.append("apollo_person")
        company = _CompanyEnrichment()
        enriched = self._should_enrich(resolution)
        if enriched:
            company = await self._company_enrichment(
                input=input,
                resolution=resolution,
                person=person,
                company_cache=None,
                company_tasks=None,
                company_lock=None,
            )
            errors.extend(company.errors)
            sources.extend(company.sources)

        result = EnrichmentResult(
            resolution=resolution,
            person=person,
            company_apollo=company.apollo_org,
            company_exa=company.exa,
            people_search=None,
            data_quality=company.data_quality or self._data_quality(person, company.apollo_org, company.exa),
            enriched=enriched,
            enrichment_sources=sources,
            errors=errors,
        )
        await self._save_result(input, result)
        return result

    async def enrich_batch(
        self,
        inputs: list[EnrichmentInput],
        max_concurrent: int = 10,
        on_progress: Any = None,
        force_refresh: bool = False,
    ) -> list[EnrichmentResult]:
        semaphore = asyncio.Semaphore(max_concurrent)
        company_cache: dict[str, _CompanyEnrichment] = {}
        company_tasks: dict[str, asyncio.Task[_CompanyEnrichment]] = {}
        company_lock = asyncio.Lock()
        email_tasks: dict[str, asyncio.Task[EnrichmentResult]] = {}
        done_count = 0
        total_count = 0

        async def run_one(lead: EnrichmentInput) -> EnrichmentResult:
            nonlocal done_count
            async with semaphore:
                result = await self._enrich_lead(
                    lead,
                    company_cache=company_cache,
                    company_tasks=company_tasks,
                    company_lock=company_lock,
                    force_refresh=force_refresh,
                )
            done_count += 1
            if on_progress is not None:
                await on_progress(done_count, total_count)
            return result

        unique_inputs: dict[str, EnrichmentInput] = {}
        for lead in inputs:
            unique_inputs.setdefault(lead.email, lead)
        total_count = len(unique_inputs)
        if on_progress is not None:
            await on_progress(0, total_count)
        for email, lead in unique_inputs.items():
            email_tasks[email] = asyncio.create_task(run_one(lead))

        return [await email_tasks[lead.email] for lead in inputs]

    async def close(self) -> None:
        for component in (self.apollo, self.exa, self.resolver):
            close = getattr(component, "close", None)
            if close is not None:
                await close()

    async def _enrich_lead(
        self,
        input: EnrichmentInput,
        *,
        company_cache: dict[str, _CompanyEnrichment] | None = None,
        company_tasks: dict[str, asyncio.Task[_CompanyEnrichment]] | None = None,
        company_lock: asyncio.Lock | None = None,
        force_refresh: bool = False,
    ) -> EnrichmentResult:
        usage_token = begin_usage()
        try:
            return await self._enrich_lead_with_usage(
                input,
                company_cache=company_cache,
                company_tasks=company_tasks,
                company_lock=company_lock,
                force_refresh=force_refresh,
            )
        finally:
            end_usage(usage_token)

    async def _enrich_lead_with_usage(
        self,
        input: EnrichmentInput,
        *,
        company_cache: dict[str, _CompanyEnrichment] | None = None,
        company_tasks: dict[str, asyncio.Task[_CompanyEnrichment]] | None = None,
        company_lock: asyncio.Lock | None = None,
        force_refresh: bool = False,
    ) -> EnrichmentResult:
        errors: list[str] = []
        sources: list[str] = []

        if self.storage is not None and not force_refresh:
            try:
                freshness = await self.storage.get_freshness(input.email)
            except Exception as exc:
                errors.append(f"storage_freshness: {exc}")
            else:
                if freshness is not None and self._is_fresh(freshness):
                    result = self._skipped_fresh_result(input)
                    await self._save_result(input, result)
                    return result

        person, apollo_person_ok = await self._match_person(input, errors)
        if apollo_person_ok:
            sources.append("apollo_person")

        resolution = await self._resolve_identity(input, person, errors)
        people_search = self._people_search_from_resolve(input)
        if people_search:
            sources.append("exa_people")

        company = _CompanyEnrichment()
        enriched = self._should_enrich(resolution)
        if enriched:
            company = await self._company_enrichment(
                input=input,
                resolution=resolution,
                person=person,
                company_cache=company_cache,
                company_tasks=company_tasks,
                company_lock=company_lock,
                force_refresh=force_refresh,
            )
            sources.extend(source for source in company.sources if source not in sources)
            errors.extend(company.errors)

        data_quality = company.data_quality or self._data_quality(person, company.apollo_org, company.exa)
        result = EnrichmentResult(
            resolution=resolution,
            person=person,
            company_apollo=company.apollo_org,
            company_exa=company.exa,
            people_search=people_search,
            data_quality=data_quality,
            enriched=enriched,
            enrichment_sources=sources,
            errors=errors,
        )
        await self._save_result(input, result)
        return result

    async def _match_person(
        self,
        input: EnrichmentInput,
        errors: list[str],
    ) -> tuple[ApolloPersonResult | None, bool]:
        try:
            match_args = {
                "first_name": input.first_name,
                "last_name": input.last_name,
                "company_name": input.company_name,
            }
            if input.linkedin_url:
                match_args["linkedin_url"] = input.linkedin_url
            person = await self.apollo.match_person(input.email, **match_args)
            return person, True
        except Exception as exc:
            errors.append(f"apollo_person: {exc}")
            return (
                ApolloPersonResult(
                    matched=False,
                    match_status="not_found",
                    email=input.email,
                    first_name=input.first_name,
                    last_name=input.last_name,
                    organization_name=input.company_name,
                ),
                False,
            )

    async def _resolve_identity(
        self,
        input: EnrichmentInput,
        person: ApolloPersonResult | None,
        errors: list[str],
    ) -> ResolvedIdentity:
        try:
            return await self.resolver.resolve(
                input.email,
                input.company_name,
                person,
                linkedin_url=input.linkedin_url,
                title=input.title,
                website_url=input.website_url,
            )
        except Exception as exc:
            errors.append(f"company_resolution: {exc}")
            return ResolvedIdentity(
                person_source="self_reported",
                name=self._person_name(input, person),
                title=input.title,
                seniority=None,
                company_source="none",
                domain=None,
                company_name=None,
                affiliation_state="unverified",
                confidence="low",
                evidence={
                    "person_state": "self_reported" if (input.first_name or input.last_name) else "unknown",
                    "company_state": "unresolved",
                    "chain": [f"Resolver failed: {exc}", "-> confidence: low"],
                },
            )

    async def _company_enrichment(
        self,
        *,
        input: EnrichmentInput,
        resolution: ResolvedIdentity,
        person: ApolloPersonResult | None,
        company_cache: dict[str, _CompanyEnrichment] | None,
        company_tasks: dict[str, asyncio.Task[_CompanyEnrichment]] | None,
        company_lock: asyncio.Lock | None,
        force_refresh: bool = False,
    ) -> _CompanyEnrichment:
        if not resolution.domain:
            return _CompanyEnrichment()

        if company_cache is None or company_tasks is None or company_lock is None:
            return await self._fetch_company_enrichment(
                input, resolution, person, force_refresh=force_refresh
            )

        async with company_lock:
            if resolution.domain in company_cache:
                return company_cache[resolution.domain]
            if resolution.domain not in company_tasks:
                company_tasks[resolution.domain] = asyncio.create_task(
                    self._fetch_company_enrichment(
                        input, resolution, person, force_refresh=force_refresh
                    )
                )
            task = company_tasks[resolution.domain]

        company = await task
        async with company_lock:
            company_cache[resolution.domain] = company
        return company

    async def _fetch_company_enrichment(
        self,
        input: EnrichmentInput,
        resolution: ResolvedIdentity,
        person: ApolloPersonResult | None,
        *,
        force_refresh: bool = False,
    ) -> _CompanyEnrichment:
        assert resolution.domain is not None
        if not force_refresh:
            fresh = await self._fresh_company_enrichment(resolution.domain)
            if fresh is not None:
                return fresh
        apollo_task = asyncio.create_task(self._capture_apollo_org(resolution.domain))
        exa_task = asyncio.create_task(self._capture_exa_deep(input, resolution, person))
        apollo_org, apollo_error = await apollo_task
        exa_result, exa_error = await exa_task

        sources: list[str] = []
        errors: list[str] = []
        if apollo_org:
            sources.append("apollo_org")
        if exa_result:
            sources.append("exa_deep")
        if apollo_error:
            errors.append(apollo_error)
        if exa_error:
            errors.append(exa_error)
        return _CompanyEnrichment(
            apollo_org=apollo_org,
            exa=exa_result,
            sources=sources,
            errors=errors,
        )

    async def _fresh_company_enrichment(self, domain: str) -> _CompanyEnrichment | None:
        lookup = getattr(self.storage, "get_company_freshness", None) if self.storage is not None else None
        if lookup is None:
            return None
        try:
            freshness = await lookup(domain)
        except Exception:
            return None
        if freshness is None or freshness.apollo_enriched_at is None:
            return None
        cutoff = datetime.now(UTC) - timedelta(days=self.ttl_days_company)
        if self._ensure_aware(freshness.apollo_enriched_at) < cutoff:
            return None
        # As with people, a never-run Exa (null) must not force a paid org
        # re-enrichment; only a present-but-stale Exa does.
        if freshness.exa_enriched_at is not None and self._ensure_aware(freshness.exa_enriched_at) < cutoff:
            return None
        stored_quality = freshness.exa_data_quality
        return _CompanyEnrichment(
            sources=["company_fresh"],
            data_quality=stored_quality if stored_quality in {"rich", "moderate", "sparse"} else None,
        )

    async def _capture_apollo_org(self, domain: str) -> tuple[ApolloOrgResult | None, str | None]:
        try:
            return await self.apollo.enrich_org(domain), None
        except Exception as exc:
            return None, f"apollo_org: {exc}"

    async def _capture_exa_deep(
        self,
        input: EnrichmentInput,
        resolution: ResolvedIdentity,
        person: ApolloPersonResult | None,
    ) -> tuple[ExaEnrichmentResult | None, str | None]:
        company_name = resolution.company_name or input.company_name or (person.organization_name if person else None)
        company_name = company_name or resolution.domain or "unknown company"
        lead_context = input.model_dump()
        lead_context.update(
            {
                "first_name": (resolution.name or "").split(" ", 1)[0] or input.first_name,
                "last_name": (resolution.name or "").split(" ", 1)[1] if resolution.name and " " in resolution.name else input.last_name,
                "title": resolution.title or input.title,
                "company_name": resolution.company_name or input.company_name,
                "resolved_name": resolution.name,
                "resolved_title": resolution.title,
                "resolved_company_name": resolution.company_name,
                "resolved_domain": resolution.domain,
                "resolved_confidence": resolution.confidence,
                "resolved_affiliation_state": resolution.affiliation_state,
                "resolution_evidence": resolution.evidence,
            }
        )
        if person:
            lead_context.update(
                {
                    "apollo_match_status": person.match_status,
                    "apollo_person_title": person.title,
                    "apollo_person_seniority": person.seniority,
                    "apollo_organization_name": person.organization_name,
                }
            )
        try:
            return await self.exa.deep_research(company_name, resolution.domain, lead_context), None
        except Exception as exc:
            return None, f"exa_deep: {exc}"

    def _people_search_from_resolve(self, input: EnrichmentInput) -> list[dict[str, Any]] | None:
        lookup = getattr(self.resolver, "people_search_results_for", None)
        if lookup is None:
            return None
        results = lookup(input.email, input.company_name)
        return results or None

    @staticmethod
    def _should_enrich(resolution: ResolvedIdentity) -> bool:
        return bool(resolution.domain and resolution.confidence in {"high", "medium", "low"})

    def _is_fresh(self, freshness: Any) -> bool:
        if freshness.apollo_enriched_at is None:
            return False
        now = datetime.now(UTC)
        apollo_at = self._ensure_aware(freshness.apollo_enriched_at)
        if apollo_at < now - timedelta(days=self.ttl_days_person_apollo):
            return False
        # Exa is a secondary, low-cost signal. A never-run Exa (null timestamp)
        # must NOT force a full Apollo re-enrichment, otherwise people who never
        # get Exa data are re-charged on Apollo every run. Only a present-but-
        # stale Exa triggers refresh.
        if freshness.exa_enriched_at is not None:
            exa_at = self._ensure_aware(freshness.exa_enriched_at)
            exa_ttl_days = min(self.ttl_days_person_exa, self.ttl_days_company)
            if exa_at < now - timedelta(days=exa_ttl_days):
                return False
        return True

    async def _save_result(self, input: EnrichmentInput, result: EnrichmentResult) -> None:
        if self.storage is None:
            return
        try:
            await self.storage.save(input, result)
        except Exception as exc:
            result.errors.append(f"storage: {exc}")

    def _skipped_fresh_result(self, input: EnrichmentInput) -> EnrichmentResult:
        return EnrichmentResult(
            resolution=ResolvedIdentity(
                person_source="self_reported",
                name=self._person_name(input, None),
                title=input.title,
                seniority=None,
                company_source="none",
                domain=None,
                company_name=input.company_name,
                affiliation_state="unverified",
                confidence="low",
                evidence={
                    "person_state": "self_reported" if (input.first_name or input.last_name) else "unknown",
                    "company_state": "unresolved",
                    "chain": ["Existing enrichment is fresh within TTL windows", "-> skipped_fresh"],
                },
            ),
            person=None,
            company_apollo=None,
            company_exa=None,
            people_search=None,
            data_quality="sparse",
            enriched=False,
            enrichment_sources=["skipped_fresh"],
            errors=[],
        )

    @staticmethod
    def _ensure_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _person_name(input: EnrichmentInput, person: ApolloPersonResult | None) -> str | None:
        first = input.first_name or (person.first_name if person else None)
        last = input.last_name or (person.last_name if person else None)
        if first and last:
            return f"{first} {last}".strip()
        return input.first_name or (person.name if person else None)

    @staticmethod
    def _data_quality(
        person: ApolloPersonResult | None,
        org: ApolloOrgResult | None,
        exa: ExaEnrichmentResult | None,
    ) -> DataQuality:
        if exa is not None:
            return exa.data_quality
        if org is not None or (person is not None and person.matched):
            return "moderate"
        return "sparse"
