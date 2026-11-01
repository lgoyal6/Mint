from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any

from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.exa import ExaClient
from mint.backend.exports.leads import AgentBoxExportData, AgentBoxExportRow, write_agentbox_export


@dataclass(slots=True)
class ScoreDimension:
    score: int
    source: str
    confidence: float
    note: str


@dataclass(slots=True)
class AgentBoxLead:
    name: str | None
    title: str | None
    linkedin_url: str | None
    email: str | None
    apollo_person_id: str | None
    company: str | None
    domain: str | None
    headcount: int | None
    funding_stage: str | None
    total_funding: float | None
    days_since_funding: int | None
    org_short_desc: str | None
    org_keywords: list[str]
    dimensions: dict[str, ScoreDimension]
    band: str
    total: int
    needs_review: bool
    suggested_sender: str
    ephemeral_only: bool
    vendor_origin_policy: bool
    campaign: str
    candidate_segment: str
    workload_review: str


@dataclass(slots=True)
class AgentBoxRun:
    leads: list[AgentBoxLead]
    bands: dict[str, int]
    needs_review_count: int
    mode: str
    xlsx: bytes
    campaign: str


class AgentBoxSourceService:
    def __init__(self, config: dict[str, Any], apollo: ApolloClient, exa: ExaClient) -> None:
        self.config = config.get("agentbox_campaign") or {}
        self.apollo = apollo
        self.exa = exa

    async def run(
        self,
        manual_flags: dict[str, dict[str, Any]] | None = None,
        campaign_name: str | None = None,
        *,
        include_workbook: bool = True,
        max_results: int | None = None,
    ) -> AgentBoxRun:
        manual_flags = manual_flags or {}
        campaign_key, campaign = self._campaign(campaign_name)
        if max_results is not None:
            campaign = {**campaign, "max_results": max_results}
        people = await self.apollo.search_people(campaign)
        leads: list[AgentBoxLead] = []
        runtime_cache: dict[str, tuple[str | None, str]] = {}
        for person in people:
            person_id = _text(person.get("id") or person.get("apollo_person_id"))
            flags = manual_flags.get(person_id or "", {})
            leads.append(await self._score_person(person, flags, campaign_key, campaign, runtime_cache))
        await self._attach_apollo_profiles_and_rescore(leads, manual_flags)
        leads.sort(key=lambda lead: (_band_sort(lead.band), -lead.total, (lead.name or "").lower()))
        bands = dict(Counter(lead.band for lead in leads))
        mode = "dry-run" if self.apollo.dry_run or self.exa.dry_run else "live"
        xlsx = write_agentbox_export(AgentBoxExportData(rows=[export_row(lead) for lead in leads])) if include_workbook else b""
        return AgentBoxRun(
            leads=leads,
            bands=bands,
            needs_review_count=sum(lead.needs_review for lead in leads),
            mode=mode,
            xlsx=xlsx,
            campaign=campaign_key,
        )

    async def _attach_apollo_profiles_and_rescore(
        self,
        leads: list[AgentBoxLead],
        manual_flags: dict[str, dict[str, Any]],
    ) -> None:
        """Reuse Apollo's paid person enrichment for company data and final scoring.

        People Search intentionally returns partial records. The existing LinkedIn
        enrichment call returns a fuller person profile, including organization
        data when Apollo has it. Merge that response before assigning the final
        band so missing search fields cannot make ``pursue`` mathematically
        impossible.
        """
        qualified = [
            lead
            for lead in leads
            if lead.band in {"pursue", "nurture"} and lead.apollo_person_id and not lead.linkedin_url
        ]
        profiles = await self.apollo.enrich_people_for_linkedin(
            [lead.apollo_person_id for lead in qualified if lead.apollo_person_id]
        )
        for lead in qualified:
            profile = profiles.get(lead.apollo_person_id or "")
            if not profile:
                continue
            lead.linkedin_url = lead.linkedin_url or _text(profile.get("linkedin_url"))
            lead.email = lead.email or _text(profile.get("email"))
            lead.name = lead.name or _text(profile.get("name")) or _name(profile)
            lead.title = lead.title or _text(profile.get("title"))
            _merge_apollo_profile(lead, profile)
            _apply_profile_exclusion(lead, self.config)
            _rescore_after_apollo_profile(
                lead,
                manual_flags.get(lead.apollo_person_id or "", {}),
            )

    def _campaign(self, requested: str | None) -> tuple[str, dict[str, Any]]:
        campaigns = self.config.get("campaigns")
        if not isinstance(campaigns, dict):
            return "default", self.config
        name = requested or _text(self.config.get("default_campaign")) or "product_runtime_us"
        campaign = campaigns.get(name)
        if not isinstance(campaign, dict):
            raise ValueError(f"Unknown AgentBox campaign: {name}")
        return name, campaign

    async def reveal_email(self, lead: AgentBoxLead) -> str | None:
        if lead.band not in {"pursue", "nurture"} or not lead.apollo_person_id:
            return lead.email
        return await self.apollo.reveal_email(lead.apollo_person_id)

    async def _score_person(
        self,
        person: dict[str, Any],
        flags: dict[str, Any],
        campaign_name: str,
        campaign: dict[str, Any],
        runtime_cache: dict[str, tuple[str | None, str]],
    ) -> AgentBoxLead:
        organization = person.get("organization") if isinstance(person.get("organization"), dict) else {}
        company = _text(organization.get("name") or person.get("organization_name"))
        domain = _domain(organization.get("primary_domain") or organization.get("domain") or organization.get("website_url"))
        description = _text(organization.get("short_description") or organization.get("description") or person.get("organization_short_description"))
        keywords = _strings(organization.get("keywords") or organization.get("keyword_tags") or person.get("organization_keywords"))
        runtime_cache_key = domain or (company or "").strip().lower() or _text(person.get("id")) or "unknown"
        runtime_result = runtime_cache.get(runtime_cache_key)
        if runtime_result is None:
            runtime_result = await self.exa.runtime_fit_text(company or "", domain)
            runtime_cache[runtime_cache_key] = runtime_result
        runtime_text, source = runtime_result
        evidence = runtime_text or description or ""
        runtime_source = "exa" if runtime_text else "apollo"
        profile_text = " ".join(part for part in [company, domain, description, " ".join(keywords)] if part)
        exclusions = _matched_terms(
            profile_text,
            [
                *(self.config.get("exclusion_terms") or []),
                *(self.config.get("hard_exclusion_terms") or []),
                *(self.config.get("competitor_terms") or []),
            ],
        )
        runtime = (
            ScoreDimension(0, "icp_gate", 0.95, f"ICP exclusion matched: {', '.join(exclusions[:3])}.")
            if exclusions
            else _runtime_dimension(
                evidence,
                self.config.get("runtime_terms") or {},
                runtime_source,
                cap=not bool(runtime_text),
            )
        )
        inference = _inference_dimension(
            evidence,
            self.config.get("runtime_terms") or {},
            runtime_source,
            flags,
            cap=not bool(runtime_text),
        )
        ephemeral_only = _has_term(evidence, self.config.get("runtime_terms", {}).get("ephemeral", [])) and not _has_term(
            evidence, _stateful_runtime_terms(self.config.get("runtime_terms") or {})
        )
        headcount = _int(organization.get("estimated_num_employees") or organization.get("employee_count"))
        funding_stage = _text(organization.get("latest_funding_stage") or organization.get("funding_stage"))
        total_funding = _money(organization.get("total_funding") or organization.get("total_funding_printed") or organization.get("total_funding_amount"))
        days_since_funding = _days_since(organization.get("latest_funding_round_date") or organization.get("funding_round_date"))
        budget = _budget_dimension(funding_stage, total_funding, flags)
        forcing = _forcing_dimension(days_since_funding, flags)
        strategic = _strategic_dimension(flags)
        dimensions = {
            "runtime_fit": runtime,
            "forcing_event": forcing,
            "inference_attach": inference,
            "budget_path": budget,
            "strategic_value": strategic,
        }
        total = sum(dimension.score for dimension in dimensions.values())
        vendor_origin_policy = bool(flags.get("vendor_origin_policy", False))
        band = _band(runtime.score, total, ephemeral_only, vendor_origin_policy)
        title = _text(person.get("title"))
        candidate_segment = _text(flags.get("workload_segment")) or _text(campaign.get("candidate_segment")) or "unreviewed"
        workload_review = _workload_review_note(candidate_segment, flags)
        needs_review = (
            runtime.confidence < 0.55
            # A campaign tier is a sourcing filter, not evidence that every
            # returned person is uncertain. Only an unclassified/manual
            # candidate is automatically flagged for review.
            or candidate_segment == "unreviewed"
            or (band in {"pursue", "nurture"} and inference.score < 2)
        )
        return AgentBoxLead(
            name=_text(person.get("name")) or _name(person),
            title=title,
            linkedin_url=_text(person.get("linkedin_url")),
            email=_text(person.get("email")),
            apollo_person_id=_text(person.get("id") or person.get("apollo_person_id")),
            company=company,
            domain=domain,
            headcount=headcount,
            funding_stage=funding_stage,
            total_funding=total_funding,
            days_since_funding=days_since_funding,
            org_short_desc=description,
            org_keywords=keywords,
            dimensions=dimensions,
            band=band,
            total=total,
            needs_review=needs_review,
            suggested_sender=_suggested_sender(band, title),
            ephemeral_only=ephemeral_only,
            vendor_origin_policy=vendor_origin_policy,
            campaign=campaign_name,
            candidate_segment=candidate_segment,
            workload_review=workload_review,
        )


def _runtime_dimension(
    text: str,
    terms: dict[str, Any],
    source: str,
    *,
    cap: bool,
    search_keywords: list[str] | None = None,
) -> ScoreDimension:
    strong = _matched_terms(text, terms.get("strong", []))
    medium = _matched_terms(text, terms.get("medium", []))
    matches = [*strong, *medium]
    if strong:
        score, confidence = 2, 0.75 + min(0.15, 0.03 * (len(strong) - 1))
        note = f"Native session/workspace evidence: {', '.join(strong[:4])}."
    elif medium:
        score, confidence = 1, 0.55 + min(0.15, 0.03 * (len(medium) - 1))
        note = f"Partial runtime signal: {', '.join(medium[:4])}; confirm workload session lifetime and ownership."
    else:
        score, confidence, note = 0, 0.3, "No qualifying ICP fit evidence found; discovery keyword matches do not qualify a company."
    if cap:
        confidence = min(confidence, 0.5)
        note = f"{note} Apollo blurb only; verify before outreach."
    return ScoreDimension(score, source if matches else "default", round(confidence, 2), note)


def _apply_profile_exclusion(lead: AgentBoxLead, config: dict[str, Any]) -> None:
    profile_text = " ".join(
        part for part in [lead.company, lead.domain, lead.org_short_desc, " ".join(lead.org_keywords)] if part
    )
    terms = [
        *(config.get("exclusion_terms") or []),
        *(config.get("hard_exclusion_terms") or []),
        *(config.get("competitor_terms") or []),
    ]
    matches = _matched_terms(profile_text, terms)
    if matches:
        lead.dimensions["runtime_fit"] = ScoreDimension(
            0, "icp_gate", 0.95, f"ICP exclusion matched: {', '.join(matches[:3])}."
        )


def _merge_apollo_profile(lead: AgentBoxLead, profile: dict[str, Any]) -> None:
    organization = profile.get("organization") if isinstance(profile.get("organization"), dict) else {}
    lead.company = _text(organization.get("name") or profile.get("organization_name")) or lead.company
    lead.domain = _domain(
        organization.get("primary_domain")
        or organization.get("domain")
        or organization.get("website_url")
        or profile.get("organization_primary_domain")
        or profile.get("organization_domain")
        or profile.get("organization_website_url")
    ) or lead.domain
    lead.headcount = _int(
        organization.get("estimated_num_employees")
        or organization.get("employee_count")
        or profile.get("organization_estimated_num_employees")
        or profile.get("organization_employee_count")
    ) or lead.headcount
    lead.funding_stage = _text(
        organization.get("latest_funding_stage")
        or organization.get("funding_stage")
        or profile.get("organization_latest_funding_stage")
        or profile.get("organization_funding_stage")
    ) or lead.funding_stage
    lead.total_funding = _money(
        organization.get("total_funding")
        or organization.get("total_funding_printed")
        or organization.get("total_funding_amount")
        or profile.get("organization_total_funding")
        or profile.get("organization_total_funding_printed")
        or profile.get("organization_total_funding_amount")
    ) or lead.total_funding
    enriched_funding_age = _days_since(
        organization.get("latest_funding_round_date")
        or organization.get("funding_round_date")
        or profile.get("organization_latest_funding_round_date")
        or profile.get("organization_funding_round_date")
    )
    lead.days_since_funding = (
        enriched_funding_age if enriched_funding_age is not None else lead.days_since_funding
    )
    lead.org_short_desc = _text(
        organization.get("short_description")
        or organization.get("description")
        or profile.get("organization_short_description")
        or profile.get("organization_description")
    ) or lead.org_short_desc
    enriched_keywords = _strings(
        organization.get("keywords")
        or organization.get("keyword_tags")
        or profile.get("organization_keywords")
    )
    if enriched_keywords:
        lead.org_keywords = enriched_keywords


def _rescore_after_apollo_profile(lead: AgentBoxLead, flags: dict[str, Any]) -> None:
    lead.dimensions["budget_path"] = _budget_dimension(
        lead.funding_stage,
        lead.total_funding,
        flags,
    )
    lead.dimensions["forcing_event"] = _forcing_dimension(lead.days_since_funding, flags)
    lead.dimensions["strategic_value"] = _strategic_dimension(flags)
    lead.total = sum(dimension.score for dimension in lead.dimensions.values())
    lead.band = _band(
        lead.dimensions["runtime_fit"].score,
        lead.total,
        lead.ephemeral_only,
        lead.vendor_origin_policy,
    )
    lead.needs_review = (
        lead.dimensions["runtime_fit"].confidence < 0.55
        or lead.candidate_segment == "unreviewed"
        or (
            lead.band in {"pursue", "nurture"}
            and lead.dimensions["inference_attach"].score < 2
        )
    )
    lead.suggested_sender = _suggested_sender(lead.band, lead.title)


def _inference_dimension(
    text: str,
    terms: dict[str, Any],
    source: str,
    flags: dict[str, Any],
    *,
    cap: bool,
) -> ScoreDimension:
    if bool(flags.get("gmi_inference_committed")):
        return ScoreDimension(2, "manual", 1.0, "Confirmed GMI MaaS or GPU commitment.")
    matches = _matched_terms(text, terms.get("inference", []))
    if not matches:
        return ScoreDimension(0, "default", 0.35, "No inference attachment evidence found.")
    confidence = 0.65 + min(0.15, 0.03 * (len(matches) - 1))
    if cap:
        confidence = min(confidence, 0.5)
    return ScoreDimension(1, source, round(confidence, 2), f"Inference/GPU attachment possible: {', '.join(matches[:4])}; confirm GMI commitment.")


def _budget_dimension(stage: str | None, funding: float | None, flags: dict[str, Any]) -> ScoreDimension:
    monthly_spend = _money(flags.get("monthly_runtime_spend_usd"))
    if bool(flags.get("funded_mandate")) or (monthly_spend or 0) >= 30_000:
        return ScoreDimension(2, "manual", 1.0, "Confirmed funded mandate or at least $30K/month runtime path.")
    stage_text = (stage or "").lower().replace(" ", "")
    if any(token in stage_text for token in ("seriesa", "seriesb", "seriesc")) or (funding or 0) >= 8_000_000:
        return ScoreDimension(1, "apollo", 0.7, "Funding is a land-size budget proxy; confirm runtime spend or mandate.")
    if "seed" in stage_text or funding:
        return ScoreDimension(1, "apollo", 0.6, "Early funding suggests a possible land-size budget path.")
    return ScoreDimension(0, "default", 0.45, "No clear funding or budget-path signal.")


def _forcing_dimension(days_since_funding: int | None, flags: dict[str, Any]) -> ScoreDimension:
    event = _text(flags.get("forcing_event"))
    if event:
        return ScoreDimension(2, "manual", 1.0, f"Confirmed forcing event: {event}.")
    if days_since_funding is not None and days_since_funding <= 120:
        return ScoreDimension(2, "apollo", 0.8, f"Funding event {days_since_funding} days ago.")
    if days_since_funding is not None and days_since_funding <= 400:
        return ScoreDimension(1, "apollo", 0.7, f"Funding event {days_since_funding} days ago.")
    return ScoreDimension(0, "default", 0.45, "No recent funding or fetched infra-hiring signal.")


def _strategic_dimension(flags: dict[str, Any]) -> ScoreDimension:
    value = _text(flags.get("strategic_value"))
    if value in {"distribution", "co_launch", "market_entry"}:
        return ScoreDimension(2, "manual", 1.0, f"Confirmed strategic value: {value.replace('_', ' ')}.")
    if value in {"logo", "co_marketing"}:
        return ScoreDimension(1, "manual", 1.0, f"Confirmed strategic value: {value.replace('_', ' ')}.")
    return ScoreDimension(0, "default", 0.45, "No strategic-account flag or scale signal.")


def _band(runtime_score: int, total: int, ephemeral_only: bool, vendor_origin_policy: bool) -> str:
    if vendor_origin_policy:
        return "disqualify"
    if runtime_score == 0:
        return "pass"
    # Apollo has already constrained these records to buyer titles, target
    # geographies, company size, and agent-runtime keywords. Strong runtime
    # evidence is therefore a plausible ICP lead even before a human supplies
    # commercial flags. Those flags rank leads; they must not be prerequisites.
    # A strong product-runtime match plus an inference attachment scores three
    # and is sufficient for a sales-ready lead. Requiring four made Apollo's
    # often-missing funding fields a hidden prerequisite, which incorrectly
    # pushed founder/CTO buyers at valid runtime companies into nurture.
    if runtime_score == 2 and total >= 3:
        return "pursue"
    if runtime_score == 2:
        return "nurture"
    if runtime_score == 1:
        return "nurture"
    return "pass"


def export_row(lead: AgentBoxLead) -> AgentBoxExportRow:
    return AgentBoxExportRow(
        name=lead.name, title=lead.title, company=lead.company, linkedin_url=lead.linkedin_url, email=lead.email,
        band=lead.band, total=lead.total, needs_review=lead.needs_review, suggested_sender=lead.suggested_sender,
        runtime_fit=_dimension_dict(lead.dimensions["runtime_fit"]), forcing_event=_dimension_dict(lead.dimensions["forcing_event"]),
        inference_attach=_dimension_dict(lead.dimensions["inference_attach"]), budget_path=_dimension_dict(lead.dimensions["budget_path"]),
        strategic_value=_dimension_dict(lead.dimensions["strategic_value"]), apollo_person_id=lead.apollo_person_id,
        domain=lead.domain, headcount=lead.headcount, funding_stage=lead.funding_stage, total_funding=lead.total_funding,
        days_since_funding=lead.days_since_funding, org_short_desc=lead.org_short_desc, org_keywords=lead.org_keywords,
        ephemeral_only=lead.ephemeral_only, vendor_origin_policy=lead.vendor_origin_policy,
        campaign=lead.campaign, candidate_segment=lead.candidate_segment, workload_review=lead.workload_review,
    )


def _dimension_dict(dimension: ScoreDimension) -> dict[str, object]:
    return {"score": dimension.score, "source": dimension.source, "confidence": dimension.confidence, "note": dimension.note}


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    lower = text.lower()
    return [
        str(term)
        for term in terms
        if re.search(rf"(?<!\\w){re.escape(str(term).lower())}(?!\\w)", lower)
    ]


def _has_term(text: str, terms: list[str]) -> bool:
    return bool(_matched_terms(text, terms))


def _stateful_runtime_terms(terms: dict[str, Any]) -> list[str]:
    return [
        "stateful",
        "persistent session",
        "persistent workspace",
        "per-user workspace",
        "long-lived session",
        "workspace per user",
        "firecracker",
        "microvm",
        "e2b",
        "execute untrusted code",
        "code execution",
        "container sandbox",
    ]


def _workload_review_note(candidate_segment: str, flags: dict[str, Any]) -> str:
    segment = _text(flags.get("workload_segment"))
    if segment in {"tier_1", "tier_2", "tier_3"}:
        return f"Confirmed {segment.replace('_', ' ')} workload; record Q0 ownership and session lifetime."
    if candidate_segment == "tier_3":
        return "Tier 3 candidate only; confirm internal users, platform owner, GPU demand, procurement, and region capability."
    return "Tier 1/2 candidate only; confirm Q0 workload ownership, minutes-hours sessions, and whether customers receive a runtime entitlement."


def _suggested_sender(band: str, title: str | None) -> str:
    title_lower = (title or "").lower()
    if band == "pursue" and any(term in title_lower for term in ("cto", "founder", "chief")):
        return "Anthony/Alex"
    return "Laksh" if band in {"pursue", "nurture"} else "-"


def _band_sort(band: str) -> int:
    return {"pursue": 0, "nurture": 1, "partner_track": 2, "pass": 3, "disqualify": 4}.get(band, 9)


def _days_since(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return max((date.today() - parsed).days, 0)


def _money(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if not text:
        return None
    cleaned = text.lower().replace("$", "").replace(",", "").strip()
    multiplier = 1_000_000 if "m" in cleaned else 1_000_000_000 if "b" in cleaned else 1
    digits = "".join(char for char in cleaned if char.isdigit() or char == ".")
    try:
        return float(digits) * multiplier
    except ValueError:
        return None


def _domain(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text.replace("https://", "").replace("http://", "").split("/")[0].lower()


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _name(person: dict[str, Any]) -> str | None:
    return " ".join(part for part in [_text(person.get("first_name")), _text(person.get("last_name"))] if part) or None
