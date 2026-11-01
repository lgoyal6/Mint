import asyncio
from typing import Any

import httpx

from mint.backend.enrichment.usage import record_usage
from mint.backend.models import ApolloOrgResult, ApolloPersonResult
from mint.backend.utils.domain import clean_domain, is_personal_email_domain


class ApolloClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.apollo.io/api/v1",
        credit_cost_usd: float | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 0.1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = not bool(api_key)
        self.credit_cost_usd = credit_cost_usd
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.client = httpx.AsyncClient(
            timeout=45,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Cache-Control": "no-cache", **({"X-Api-Key": api_key} if api_key else {})},
            transport=transport,
        )

    async def match_person(
        self,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        company_name: str | None = None,
        linkedin_url: str | None = None,
    ) -> ApolloPersonResult:
        details = self._person_match_details(email, first_name, last_name, company_name, linkedin_url)
        try:
            response = await self._request("POST", "/people/bulk_match", json={"details": [details]})
        except ApolloRequestError as exc:
            if exc.status_code != 422:
                raise
            response = await self._request("POST", "/people/match", json=details)
        match = self._first_match(response)
        record_usage(
            "apollo",
            "person_enrichment",
            credits=1 if match else 0,
            estimated_cost_usd=self.credit_cost_usd if match and self.credit_cost_usd is not None else 0,
            pricing_basis="estimated: one enrichment credit when a qualifying person is returned",
        )
        if not match:
            return ApolloPersonResult(
                matched=False,
                match_status="not_found",
                email=email.strip().lower(),
                first_name=first_name,
                last_name=last_name,
                organization_name=company_name,
            )
        return self._person_from_match(match)

    async def enrich_org(self, domain: str) -> ApolloOrgResult:
        cleaned_domain = clean_domain(domain) or domain
        response = await self._request(
            "POST",
            "/organizations/enrich",
            json={"domain": cleaned_domain},
        )
        organization = response.get("organization")
        record_usage(
            "apollo",
            "organization_enrichment",
            credits=1 if isinstance(organization, dict) else 0,
            estimated_cost_usd=(
                self.credit_cost_usd if isinstance(organization, dict) and self.credit_cost_usd is not None else 0
            ),
            pricing_basis="estimated: one enrichment credit when a qualifying organization is returned",
        )
        if not isinstance(organization, dict):
            return ApolloOrgResult(domain=cleaned_domain, raw_data=response)
        return self._org_from_response(organization, fallback_domain=cleaned_domain)

    async def search_people(self, campaign: dict[str, Any]) -> list[dict[str, Any]]:
        """Search Apollo's people endpoint without revealing masked emails."""
        if self.dry_run:
            return _dry_run_people()
        people_by_id: dict[str, dict[str, Any]] = {}
        keywords = [str(value) for value in campaign.get("org_keywords") or []] or [""]
        pages_per_keyword = int(campaign.get("pages_per_keyword") or campaign.get("max_pages") or 1)
        max_results = int(campaign.get("max_results") or pages_per_keyword * int(campaign.get("per_page") or 100))
        for keyword in keywords:
            for page in range(1, pages_per_keyword + 1):
                batch = await self._search_people_page(campaign, keyword=keyword, page=page)
                for item in batch:
                    person_id = self._text(item.get("id") or item.get("person_id"))
                    if not person_id:
                        continue
                    existing = people_by_id.get(person_id)
                    if existing is None:
                        people_by_id[person_id] = dict(item)
                        if keyword:
                            people_by_id[person_id]["_mint_search_keywords"] = [keyword]
                    elif keyword:
                        matched = existing.setdefault("_mint_search_keywords", [])
                        if isinstance(matched, list) and keyword not in matched:
                            matched.append(keyword)
                if len(people_by_id) >= max_results or len(batch) < int(campaign.get("per_page") or 100):
                    break
            if len(people_by_id) >= max_results:
                break
        return list(people_by_id.values())[:max_results]

    async def _search_people_page(
        self,
        campaign: dict[str, Any],
        *,
        keyword: str,
        page: int,
    ) -> list[dict[str, Any]]:
        # The current Apollo API Search endpoint is POST but models filters as
        # query parameters (including the literal [] suffix for arrays).
        params: list[tuple[str, str | int]] = [
            *(("person_titles[]", str(value)) for value in campaign.get("person_titles") or []),
            *(("person_seniorities[]", str(value)) for value in campaign.get("person_seniorities") or []),
            *(("organization_num_employees_ranges[]", str(value)) for value in campaign.get("org_headcount_ranges") or []),
            *(("person_locations[]", str(value)) for value in campaign.get("person_locations") or []),
            *([("q_organization_keyword_tags[]", keyword)] if keyword else []),
            ("page", page),
            ("per_page", int(campaign.get("per_page") or 100)),
        ]
        response = await self._request(
            "POST",
            # Apollo retired /mixed_people/search for API callers. Keep this
            # separate from the email-reveal endpoint: this search only returns
            # the fields Apollo exposes in search results.
            "/mixed_people/api_search",
            params=params,
        )
        batch = response.get("people") if isinstance(response.get("people"), list) else []
        return [item for item in batch if isinstance(item, dict)]

    async def enrich_people_for_linkedin(self, person_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return complete Apollo profiles for selected people, in batches of ten.

        People Search intentionally returns partial records. Apollo documents Bulk
        People Enrichment as the supported way to turn those IDs into profiles that
        include ``linkedin_url``. Email and phone waterfall options stay disabled.
        """
        if self.dry_run or not person_ids:
            return {}
        enriched: dict[str, dict[str, Any]] = {}
        unique_ids = list(dict.fromkeys(person_id for person_id in person_ids if person_id))
        for start in range(0, len(unique_ids), 10):
            batch_ids = unique_ids[start : start + 10]
            response = await self._request(
                "POST",
                "/people/bulk_match",
                json={"details": [{"id": person_id} for person_id in batch_ids]},
            )
            matches = response.get("matches") if isinstance(response.get("matches"), list) else []
            matched_count = 0
            for match in matches:
                if not isinstance(match, dict):
                    continue
                person_id = self._text(match.get("id") or match.get("person_id"))
                if person_id:
                    enriched[person_id] = match
                    matched_count += 1
            record_usage(
                "apollo",
                "bulk_people_enrichment",
                requests=1,
                credits=response.get("credits_consumed", matched_count),
                estimated_cost_usd=(
                    matched_count * self.credit_cost_usd if self.credit_cost_usd is not None else 0
                ),
                pricing_basis="Apollo-reported credits when available; otherwise matched enriched records",
            )
        return enriched

    async def reveal_email(self, person_id: str) -> str | None:
        """Opt-in, single-person email reveal. Never call this from bulk search."""
        if self.dry_run:
            return None
        response = await self._request("POST", "/people/match", json={"id": person_id})
        person = self._first_match(response)
        return self._text(person.get("email")) if person else None

    async def health_check(self) -> dict[str, Any]:
        """Validate an Apollo key through its documented, no-credit auth endpoint."""
        if self.dry_run:
            return {
                "configured": False,
                "verified": False,
                "status": "disabled",
                "credit_cost": "none",
            }
        try:
            response = await self.client.get(self._auth_health_url())
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return {
                "configured": True,
                "verified": False,
                "status": "invalid_key" if status_code in {401, 403} else "error",
                "http_status": status_code,
                "credit_cost": "none",
            }
        except httpx.HTTPError:
            return {
                "configured": True,
                "verified": False,
                "status": "unreachable",
                "credit_cost": "none",
            }

        healthy = bool(payload.get("healthy")) if isinstance(payload, dict) else False
        logged_in = bool(payload.get("is_logged_in")) if isinstance(payload, dict) else False
        return {
            "configured": True,
            "verified": healthy and logged_in,
            "status": "ok" if healthy and logged_in else "unhealthy",
            "credit_cost": "none",
        }

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.request(method, self._url(path), **kwargs)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if not self._is_retryable_status(exc.response.status_code) or attempt >= self.max_retries:
                    raise ApolloRequestError.from_response(exc.response) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
            await asyncio.sleep(self.backoff_seconds * (2**attempt))
        raise RuntimeError("Apollo request retries exhausted") from last_error

    def _url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{normalized_path}"

    def _auth_health_url(self) -> str:
        """Apollo exposes auth health outside the /api/v1 application API prefix."""
        root = self.base_url.removesuffix("/api/v1").removesuffix("/v1")
        return f"{root}/v1/auth/health"

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    @staticmethod
    def _person_match_details(
        email: str,
        first_name: str | None,
        last_name: str | None,
        company_name: str | None,
        linkedin_url: str | None = None,
    ) -> dict[str, str]:
        details: dict[str, str] = {"email": email.strip().lower()}
        if first_name:
            details["first_name"] = first_name
        if last_name:
            details["last_name"] = last_name
        if company_name:
            details["organization_name"] = company_name
        if linkedin_url:
            details["linkedin_url"] = linkedin_url
        return details

    @staticmethod
    def _first_match(response: dict[str, Any]) -> dict[str, Any] | None:
        matches = response.get("matches")
        if isinstance(matches, list) and matches:
            first = matches[0]
            return first if isinstance(first, dict) else None
        person = response.get("person")
        return person if isinstance(person, dict) else None

    def _person_from_match(self, person: dict[str, Any]) -> ApolloPersonResult:
        organization = person.get("organization") if isinstance(person.get("organization"), dict) else {}
        org_domain = clean_domain(
            organization.get("website_url")
            or organization.get("domain")
            or person.get("organization_website_url")
        )
        location = self._location(person)
        return ApolloPersonResult(
            matched=True,
            match_status="matched",
            email=self._text(person.get("email")),
            first_name=self._text(person.get("first_name")),
            last_name=self._text(person.get("last_name")),
            name=self._text(person.get("name")),
            title=self._text(person.get("title") or person.get("headline")),
            seniority=self._text(person.get("seniority")),
            departments=self._string_list(person.get("departments")),
            linkedin_url=self._text(person.get("linkedin_url")),
            location=location,
            organization_name=self._text(organization.get("name") or person.get("organization_name")),
            organization_domain=org_domain if not is_personal_email_domain(org_domain) else None,
            raw_data=person,
        )

    def _org_from_response(self, organization: dict[str, Any], fallback_domain: str) -> ApolloOrgResult:
        growth = organization.get("employee_growth_percentages")
        growth = growth if isinstance(growth, dict) else {}
        domain = clean_domain(
            organization.get("domain") or organization.get("website_url") or organization.get("primary_domain")
        )
        return ApolloOrgResult(
            domain=domain or fallback_domain,
            name=self._text(organization.get("name")),
            industry=self._text(
                organization.get("industry")
                or organization.get("primary_industry")
                or organization.get("industry_tag_id")
            ),
            employee_count=self._int(
                organization.get("estimated_num_employees") or organization.get("employee_count")
            ),
            founded_year=self._int(organization.get("founded_year")),
            funding_stage=self._text(
                organization.get("latest_funding_stage") or organization.get("funding_stage")
            ),
            latest_funding_round_date=self._text(
                organization.get("latest_funding_round_date") or organization.get("funding_round_date")
            ),
            total_funding=self._text(
                organization.get("total_funding_printed")
                or organization.get("total_funding")
                or organization.get("total_funding_amount")
            ),
            revenue=self._text(
                organization.get("organization_revenue_printed")
                or organization.get("revenue")
                or organization.get("annual_revenue_printed")
            ),
            technologies=self._string_list(
                organization.get("technology_names") or organization.get("technologies")
            ),
            headcount_growth_6mo=self._float(
                growth.get("six_month") or growth.get("6_month") or growth.get("6mo")
            ),
            headcount_growth_12mo=self._float(
                growth.get("twelve_month") or growth.get("12_month") or growth.get("12mo")
            ),
            raw_data=organization,
        )

    @staticmethod
    def _location(person: dict[str, Any]) -> str | None:
        if isinstance(person.get("location"), str) and person["location"].strip():
            return person["location"].strip()
        parts = [person.get("city"), person.get("state"), person.get("country")]
        text_parts = [str(part).strip() for part in parts if part]
        return ", ".join(text_parts) or None

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            if value in (None, ""):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None


class ApolloRequestError(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str) -> None:
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"Apollo {status_code} for {url}: {body}")

    @classmethod
    def from_response(cls, response: httpx.Response) -> "ApolloRequestError":
        text = response.text.strip()
        if len(text) > 1000:
            text = f"{text[:1000]}..."
        return cls(response.status_code, str(response.url), text or "<empty response body>")


def _dry_run_people() -> list[dict[str, Any]]:
    """Small deterministic fixture so sourcing can run without Apollo credits."""
    return [
        {
            "id": "dry-run-runtime",
            "name": "Avery Chen",
            "title": "CTO",
            "linkedin_url": "https://www.linkedin.com/in/avery-chen-example",
            "organization": {
                "name": "Workspace Agents",
                "primary_domain": "workspaceagents.example",
                "estimated_num_employees": 72,
                "latest_funding_stage": "Series A",
                "latest_funding_round_date": "2026-06-01",
                "total_funding_printed": "$12M",
                "short_description": "AI agents use persistent sandboxed code execution workspaces for every user.",
                "keywords": ["AI agent", "code execution", "sandbox"],
            },
        },
        {
            "id": "dry-run-ephemeral",
            "name": "Jordan Patel",
            "title": "Head of Engineering",
            "linkedin_url": None,
            "organization": {
                "name": "Instant Functions",
                "primary_domain": "instantfunctions.example",
                "estimated_num_employees": 24,
                "latest_funding_stage": "Seed",
                "latest_funding_round_date": "2025-08-01",
                "total_funding_printed": "$3M",
                "short_description": "Stateless function platform with ephemeral workloads and instant cold starts.",
                "keywords": ["serverless", "ephemeral"],
            },
        },
    ]
