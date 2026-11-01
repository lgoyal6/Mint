import asyncio
import json
import re
from typing import Any

import httpx

from mint.backend.enrichment.usage import record_usage
from mint.backend.models import EnrichmentInput, ExaEnrichmentResult
from mint.backend.utils.domain import clean_domain


ENRICHMENT_SCHEMA = {
    "type": "object",
    "required": [
        "what_they_do",
        "target_vertical",
        "stage",
        "funding_and_revenue",
        "compute_profile",
        "team_and_activity",
        "person_role_signals",
        "data_quality",
    ],
    "properties": {
        "what_they_do": {
            "type": "string",
            "description": "2-3 sentence description of core product/service and who they sell to.",
        },
        "target_vertical": {
            "type": "string",
            "description": (
                "GMI Cloud ICP category for the target company as a likely buyer/user of GPU compute, "
                "not as a seller of GPU compute: research_labs | coding_platforms | agentic | "
                "data_labeling | robotics | video_ai_multimodal | adjacent | not_target. The 6 target "
                "verticals are research labs, coding platforms, agentic/generative AI, data labeling, "
                "robotics, and video AI/multimodal because they tend to have high-volume inference, "
                "token-hungry workloads, or bare-metal GPU needs for open-source models. Autonomous "
                "vehicles and CV/vision platforms are adjacent, not core target verticals. Use not_target "
                "when the company does not fit these target or adjacent categories. A parent university, "
                "college, or higher-education institution is not_target even if it hosts robotics, AI, "
                "or research labs; classify only independent compute-heavy research organizations as research_labs."
            ),
        },
        "stage": {
            "type": "string",
            "description": "pre-revenue | early-revenue | growth | mature | public | unknown. Include business model (SaaS, platform, services, hardware, research, marketplace, other) in your reasoning.",
        },
        "funding_and_revenue": {
            "type": "string",
            "description": (
                "Funding stage, total raised, investors, round dates, and whether the company appears "
                "Series A or above. Revenue evidence: pricing pages, paid customers, case studies, "
                "customer logos, scale claims like '10K+ teams' or '1M+ API calls/day', profitability, "
                "or lack thereof. Be specific about what was and wasn't found."
            ),
        },
        "compute_profile": {
            "type": "string",
            "description": (
                "ML inference workloads, model hosting, GPU usage, open-source vs proprietary models, "
                "scale indicators (API volume, throughput, user count), current infrastructure providers "
                "(AWS, GCP, CoreWeave, Lambda, RunPod, etc), migration signals, and estimated monthly "
                "GPU/inference spend. Include enough evidence so a classifier can apply thresholds later: "
                "whether spend appears below $5K/mo, $5-10K/mo, or >= $10K/mo; whether "
                "the company is pre-revenue, Series A or above, growth/mature, public, or a large corp. "
                "Do not assign P0/P1/P2/NR or MQL/SQL labels."
            ),
        },
        "team_and_activity": {
            "type": "string",
            "description": "Tech stack and frameworks. Open roles especially ML infra, platform eng, GPU, CUDA, model serving. Recent activity: funding rounds, product launches, partnerships, acquisitions, key hires, layoffs. Include approximate dates when available.",
        },
        "person_role_signals": {
            "type": "string",
            "description": "Lead's current role, seniority, and relevance to GPU/infrastructure purchasing decisions. Is this person a buyer, champion, technical evaluator, or end user?",
        },
        "data_quality": {
            "type": "string",
            "description": "rich (multiple corroborating sources, clear signals) | moderate (some gaps but workable) | sparse (minimal information found)",
        },
    },
}


class TokenBucketRateLimiter:
    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        time_func=None,
        sleep=asyncio.sleep,
    ) -> None:
        self.rate = max(float(rate), 0.000001)
        self.burst = max(int(burst), 1)
        self.tokens = float(self.burst)
        self.last_refill: float | None = None
        self.time_func = time_func
        self.sleep = sleep
        self.lock = asyncio.Lock()

    def _now(self) -> float:
        if self.time_func is not None:
            return float(self.time_func())
        return asyncio.get_running_loop().time()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = self._now()
                if self.last_refill is None:
                    self.last_refill = now
                elapsed = max(0.0, now - self.last_refill)
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait_seconds = (1 - self.tokens) / self.rate
            await self.sleep(wait_seconds)


class ExaClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.exa.ai",
        max_retries: int = 3,
        backoff_seconds: float = 0.1,
        rate_limit_per_second: float = 1.5,
        rate_limit_burst: int = 10,
        rate_limiter: TokenBucketRateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = not bool(api_key)
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            rate=rate_limit_per_second,
            burst=rate_limit_burst,
        )
        self.client = httpx.AsyncClient(
            timeout=90,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Content-Type": "application/json", **({"x-api-key": api_key} if api_key else {})},
            transport=transport,
        )

    async def deep_research(
        self,
        company_name: str,
        domain: str | None,
        lead_context: dict[str, Any],
    ) -> ExaEnrichmentResult:
        response = await self._search(
            {
                "query": _lead_research_query(company_name, domain, lead_context),
                "type": "deep",
                "numResults": 10,
                "contents": {"highlights": True},
                "systemPrompt": (
                    "You are a research agent gathering enrichment evidence for GMI Cloud, which sells GPU cloud, "
                    "bare metal GPU capacity, and inference compute. Evaluate whether the target "
                    "company is a buyer/user of GPU compute, not whether they sell GPU compute. "
                    "Do not classify the lead as P0, P1, MQL, SQL, or NP; lay out the facts so a "
                    "separate classifier can make that decision. "
                    "Prefer official company pages, funding/news "
                    "sources, careers pages, technical docs, reputable public profiles, and "
                    "current role evidence. Use the provided lead context to disambiguate the "
                    "company and person. Keep every field grounded in sources and provided data. "
                    "Do not invent."
                ),
                "additionalQueries": _lead_additional_queries(company_name, lead_context),
                "outputSchema": ENRICHMENT_SCHEMA,
            }
        )
        return _enrichment_from_deep_response(response, company_name=company_name, domain=domain)

    async def company_search(self, query: str) -> list[dict[str, Any]]:
        response = await self._search(
            {
                "query": query,
                "type": "auto",
                "category": "company",
                "numResults": 5,
                "contents": {"highlights": True},
            }
        )
        return _compact_company_results(response.get("results", []))

    async def people_search(self, name: str, company: str) -> list[dict[str, Any]]:
        query = " ".join(
            part
            for part in [
                name,
                company,
                "AI machine learning infrastructure founder CTO engineering leader LinkedIn",
            ]
            if part
        )
        response = await self._search(
            {
                "query": query,
                "type": "auto",
                "category": "people",
                "numResults": 5,
                "contents": {"highlights": True},
            }
        )
        return _compact_people_results(response.get("results", []))

    async def profile_affiliation_search(
        self,
        name: str,
        linkedin_url: str,
        company: str,
    ) -> list[dict[str, Any]]:
        query = " ".join(
            part
            for part in [
                f'"{name}"',
                f'"{company}"',
                f'"{linkedin_url}"',
                "LinkedIn current role company",
            ]
            if part
        )
        response = await self._search(
            {
                "query": query,
                "type": "auto",
                "category": "people",
                "numResults": 3,
                "contents": {"highlights": True},
            }
        )
        return _compact_people_results(response.get("results", []))

    async def runtime_fit_text(self, company_name: str, domain: str | None) -> tuple[str | None, str]:
        """Fetch concise company text for the AgentBox runtime-fit gate."""
        if self.dry_run:
            return None, "dry-run"
        payload: dict[str, Any] = {
            "query": f"{company_name} agent runtime sandbox code execution inference",
            "type": "auto",
            "numResults": 3,
            "contents": {"text": True},
        }
        if domain:
            payload["includeDomains"] = [domain]
        response = await self._search(payload)
        texts: list[str] = []
        for result in response.get("results", []):
            if isinstance(result, dict) and isinstance(result.get("text"), str):
                texts.append(result["text"])
        return "\n".join(texts) or None, "exa"

    def health_check(self) -> dict[str, Any]:
        """Report key configuration without issuing a potentially billable Exa request.

        Exa does not document a no-cost API-key validation endpoint. Keeping this
        check local means opening Mint's Health page cannot consume search credits.
        """
        if self.dry_run:
            return {
                "configured": False,
                "verified": False,
                "status": "disabled",
                "credit_cost": "none",
            }
        return {
            "configured": True,
            "verified": False,
            "status": "configured_unverified",
            "credit_cost": "not_checked",
            "detail": "No documented no-cost Exa key check; Mint did not make a billable request.",
        }

    async def live_check(self) -> dict[str, Any]:
        """Issue one deliberately small search to verify the key and balance."""
        if self.dry_run:
            return {
                "configured": False,
                "verified": False,
                "status": "disabled",
                "estimated_cost_usd": 0,
            }
        await self._search(
            {
                "query": "Mint enrichment integration verification",
                "type": "auto",
                "numResults": 1,
            }
        )
        return {
            "configured": True,
            "verified": True,
            "status": "ok",
            "estimated_cost_usd": 0.007,
            "detail": "One capped Exa search succeeded.",
        }

    async def close(self) -> None:
        await self.client.aclose()

    async def _search(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                await self.rate_limiter.acquire()
                response = await self.client.post(f"{self.base_url}/search", json=payload)
                response.raise_for_status()
                data = response.json()
                search_type = str(payload.get("type") or "auto")
                results = data.get("results") if isinstance(data, dict) else None
                result_count = len(results) if isinstance(results, list) else 0
                if not result_count and isinstance(data, dict):
                    output = data.get("output")
                    grounding = output.get("grounding") if isinstance(output, dict) else None
                    result_count = len(grounding) if isinstance(grounding, list) else 0
                base_cost = 0.012 if search_type in {"deep", "deep-lite"} else 0.015 if search_type == "deep-reasoning" else 0.007
                content_cost = 0.001 * result_count if payload.get("contents") else 0
                record_usage(
                    "exa",
                    f"{search_type}_search",
                    estimated_cost_usd=base_cost + content_cost,
                    pricing_basis="estimate from Exa search base price plus requested page contents",
                )
                return data
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if not _is_retryable_status(exc.response.status_code) or attempt >= self.max_retries:
                    raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
            await asyncio.sleep(self.backoff_seconds * (2**attempt))
        raise RuntimeError("Exa request retries exhausted") from last_error


def _lead_research_query(company_name: str, domain: str | None, lead: dict[str, Any]) -> str:
    domain_clause = f" ({domain})" if domain else ""
    return (
        f"Research {company_name}{domain_clause} for GMI Cloud enrichment. GMI Cloud sells GPU cloud, "
        "bare metal GPU capacity, and inference compute; evaluate whether the target company is a "
        "likely buyer/user of GPU compute, not whether they sell GPU compute. Determine what the "
        "company does, whether it matches GMI Cloud's 6 target verticals (research labs, coding "
        "platforms, agentic/generative AI, data labeling, robotics, video AI/multimodal) or an "
        "adjacent AI/ML vertical such as autonomous vehicles or CV/vision platforms; whether it "
        "is a parent university/college/higher-education institution, which should be not_target "
        "rather than robotics/research_labs based only on hosted labs or departments; whether it "
        "has revenue; funding stage; scale; likely GPU/inference spend; and whether this specific "
        "lead is relevant to infra buying. Provide evidence for target vertical, estimated spend "
        "band (<$5K/mo, $5-10K/mo, or >= $10K/mo), stage/revenue, public or large-corp status, "
        "and buyer-role relevance, but do not classify the lead as P0, P1, MQL, SQL, or NP; the "
        f"classifier can make the tiering decision later.\n\nLead context:\n{_lead_context(lead)}"
    )


def _lead_additional_queries(company_name: str, lead: dict[str, Any]) -> list[str]:
    title = _text(lead.get("title"))
    build = _text(lead.get("what_they_are_building"))
    return [
        f'"{company_name}" funding revenue customers',
        f'"{company_name}" AI GPU inference infrastructure',
        f'"{company_name}" hiring ML infrastructure GPU',
        " ".join(part for part in [f'"{company_name}"', title, build] if part).strip(),
    ]


def _lead_context(lead: dict[str, Any]) -> str:
    lines: list[str] = []
    for key in (
        "first_name",
        "last_name",
        "email",
        "title",
        "company_name",
        "linkedin_url",
        "what_they_are_building",
        "source",
        "event_id",
    ):
        value = _text(lead.get(key))
        if value:
            lines.append(f"- {key}: {value}")
    survey_responses = lead.get("survey_responses")
    if isinstance(survey_responses, dict):
        for question, answer in survey_responses.items():
            if answer not in (None, "", [], {}):
                lines.append(f'- Survey Q: "{question}" -> "{_text(answer)}"')
    return "\n".join(lines) or "- no lead-specific context provided"


def _enrichment_from_deep_response(
    response: dict[str, Any],
    *,
    company_name: str | None = None,
    domain: str | None = None,
) -> ExaEnrichmentResult:
    output = response.get("output")
    content: Any = {}
    grounding: Any = []
    if isinstance(output, dict):
        content = output.get("content") or {}
        grounding = output.get("grounding") or []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            content = {"what_they_do": content}
    if not isinstance(content, dict):
        content = {}
    if _is_academic_institution(company_name, domain, content):
        content = {**content, "target_vertical": "not_target"}
    content = _expanded_enrichment_content(content)
    return ExaEnrichmentResult(
        **content,
        exa_grounding=grounding if isinstance(grounding, list) else [],
        raw_data=response,
    )


def _expanded_enrichment_content(content: dict[str, Any]) -> dict[str, Any]:
    if "funding_and_revenue" not in content and "compute_profile" not in content:
        return content
    funding_and_revenue = _text(content.get("funding_and_revenue"), max_chars=5000)
    compute_profile = _text(content.get("compute_profile"), max_chars=5000)
    team_and_activity = _text(content.get("team_and_activity"), max_chars=5000)
    expanded = dict(content)
    expanded.setdefault("revenue_signals", funding_and_revenue)
    expanded.setdefault("customer_signals", funding_and_revenue)
    expanded.setdefault("funding_signals", funding_and_revenue)
    expanded.setdefault("inference_signals", compute_profile)
    expanded.setdefault("spend_signals", compute_profile)
    expanded.setdefault("competitors_or_current_infra", compute_profile)
    expanded.setdefault("tech_stack", [])
    expanded.setdefault("hiring_signals", [team_and_activity] if team_and_activity else [])
    expanded.setdefault("recent_activity", [team_and_activity] if team_and_activity else [])
    expanded["data_quality"] = _normalize_data_quality(expanded.get("data_quality"))
    return expanded


def _is_academic_institution(
    company_name: str | None,
    domain: str | None,
    content: dict[str, Any],
) -> bool:
    domain_value = str(domain or "").strip().lower()
    if re.search(r"(\.edu|\.edu\.[a-z]{2,3}|\.ac\.[a-z]{2,3})$", domain_value, re.IGNORECASE):
        return True
    text = _text(
        " ".join(
            [
                str(company_name or ""),
                str(content.get("what_they_do") or ""),
                str(content.get("funding_and_revenue") or ""),
                str(content.get("team_and_activity") or ""),
            ]
        ),
        max_chars=12000,
    )
    return bool(
        re.search(
            r"\b(university|college|higher education|school of|institute of technology)\b",
            text,
            re.IGNORECASE,
        )
    )


def _normalize_data_quality(value: Any) -> str:
    text = _text(value).lower()
    if text.startswith("rich"):
        return "rich"
    if text.startswith("sparse"):
        return "sparse"
    return "moderate"


def _compact_company_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    compacted: list[dict[str, Any]] = []
    for result in results[:5]:
        if not isinstance(result, dict):
            continue
        company = result.get("company") if isinstance(result.get("company"), dict) else {}
        url = _text(result.get("url"))
        domain = (
            clean_domain(company.get("domain"))
            or clean_domain(result.get("domain"))
            or clean_domain(result.get("company_domain"))
            or clean_domain(url)
        )
        name = _text(company.get("name") or result.get("name") or result.get("title"))
        item = {"name": name, "domain": domain, "url": url}
        compacted.append({key: value for key, value in item.items() if value})
    return compacted


def _compact_people_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    compacted: list[dict[str, Any]] = []
    for result in results[:5]:
        if not isinstance(result, dict):
            continue
        item = {
            key: result[key]
            for key in ("title", "url", "highlights", "domain", "company_domain", "company")
            if key in result and result[key] not in (None, "", [], {})
        }
        if item:
            compacted.append(item)
    return compacted


def _text(value: Any, max_chars: int = 500) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text[:max_chars]


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500
