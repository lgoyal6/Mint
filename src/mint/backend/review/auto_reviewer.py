import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from mint.backend.classification.classifier import Classifier, _llm_context
from mint.backend.classification.service import ClassificationScope
from mint.backend.db.classification_storage import AutoReviewRecord


AUTO_REVIEWER_NAME = "mint-auto-reviewer"
STRUCTURAL_NP_TRIGGERS = {
    "bigtech_or_large_corp",
    "student_or_academic_role",
    "government_public_sector",
    "academic_institution",
    "npo_ngo",
    "direct_competitor",
    "large_corp_10k_plus",
    "not_target",
    "non_buyer_role",
    "no_product_fit",
}
logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """
You are a senior reviewer auditing lead classifications for GMI Cloud (GPU/inference infrastructure).
You receive the same enrichment context the classifier saw, plus its existing classification and why
it was flagged for review. Decide whether the assigned tier (P0 = pursue now, P1 = strong MQL,
P2 = potential lead, NR/NP = nurture / not ready) is right.

Return JSON only:
{
  "verdict": "confirm" | "adjust" | "uncertain",
  "tier": "P0" | "P1" | "P2" | "NR" | "NP" | null,
  "reasoning": string
}

- confirm: the existing tier is right; clear the review flag.
- adjust: the tier is wrong; set "tier" to the correct one and explain the decisive evidence.
- uncertain: the evidence genuinely cannot settle it; a human should look. Say what is missing.
Only adjust when the evidence clearly supports a different tier. Do not invent facts.
""".strip()

RESOLVE_SYSTEM_PROMPT = """
You adjudicate company-identity conflicts for event leads. A person has multiple candidate companies
and the system could not pick one. Using the evidence (self-reported company and title, email domain,
resolution evidence, people-search results), decide which candidate company the person actually
belongs to today.

Return JSON only:
{
  "verdict": "resolve" | "uncertain",
  "chosen_domain": string | null,
  "reasoning": string
}

- resolve: the evidence clearly favors one candidate; chosen_domain MUST be one of the candidate domains.
- uncertain: the evidence is genuinely tied or stale; a human should decide. Say why.
Do not invent domains. Prefer current employment over past employment.
""".strip()


class AutoReviewSummary(BaseModel):
    reviews_confirmed: int = 0
    reviews_adjusted: int = 0
    reviews_uncertain: int = 0
    conflicts_resolved: int = 0
    conflicts_uncertain: int = 0
    errors: list[str] = Field(default_factory=list)


class AutoReviewService:
    def __init__(
        self,
        classification_storage: Any,
        read_storage: Any,
        llm_client: Any,
        icp_config: dict[str, Any],
        *,
        model: str | None = None,
        enrichment_service: Any = None,
        classification_service: Any = None,
        max_llm_reviews: int = 200,
    ) -> None:
        self.storage = classification_storage
        self.read_storage = read_storage
        self.llm_client = llm_client
        self.model = model
        self.enrichment_service = enrichment_service
        self.classification_service = classification_service
        self.max_llm_reviews = max_llm_reviews
        # Used only to build the same evidence/context the classifier saw.
        self._context_builder = Classifier(icp_config)

    async def sweep(self) -> AutoReviewSummary:
        summary = AutoReviewSummary()
        llm_calls = 0

        for record in await self.storage.pending_auto_reviews(limit=self.max_llm_reviews * 2):
            try:
                used_llm = await self._review_one(record, summary, llm_budget_left=self.max_llm_reviews - llm_calls)
            except Exception as exc:
                summary.errors.append(f"review {record.person.email}: {exc}")
                continue
            llm_calls += used_llm

        for person in await self.storage.pending_flagged_resolutions(limit=self.max_llm_reviews):
            if llm_calls >= self.max_llm_reviews:
                break
            try:
                llm_calls += await self._adjudicate_flagged(person, summary)
            except Exception as exc:
                summary.errors.append(f"resolve {person.email}: {exc}")

        return summary

    async def _review_one(self, record: AutoReviewRecord, summary: AutoReviewSummary, *, llm_budget_left: int) -> int:
        classification = record.classification
        if classification.tier == "NP" and classification.np_trigger in STRUCTURAL_NP_TRIGGERS:
            await self.storage.mark_auto_review(
                classification.id,
                status="confirmed",
                note=f"Structural NP ({classification.np_trigger}); confirmed without LLM review.",
            )
            summary.reviews_confirmed += 1
            return 0

        if record.company is None:
            await self.storage.mark_auto_review(
                classification.id,
                status="uncertain",
                note="No company linked to this lead; needs a human look.",
            )
            summary.reviews_uncertain += 1
            return 0

        if llm_budget_left <= 0:
            return 0

        evidence = self._context_builder._extract_evidence(record.person, record.company)
        context = {
            "lead": _llm_context(record.person, record.company, evidence, list(classification.flags or [])),
            "existing_classification": {
                "tier": classification.tier,
                "np_trigger": classification.np_trigger,
                "product_fit": classification.product_fit,
                "primary_product": classification.primary_product,
                "confidence": classification.confidence,
                "reasoning": classification.reasoning,
                "flags": classification.flags,
            },
        }
        response = await self.llm_client.classify_tier(context, system_prompt=REVIEW_SYSTEM_PROMPT, model=self.model)
        decision = _parse_json_response(response)
        verdict = str(decision.get("verdict") or "uncertain").strip().lower()
        reasoning = str(decision.get("reasoning") or "No reasoning returned.").strip()
        new_tier = str(decision.get("tier") or "").strip().upper()

        if verdict == "adjust" and new_tier in {"P0", "P1", "P2", "NR", "NP"} and new_tier != classification.tier:
            await self.storage.mark_auto_review(classification.id, status="adjusted", note=reasoning)
            summary.reviews_adjusted += 1
        elif verdict in {"confirm", "adjust"}:
            # "adjust" to the same tier is a confirmation.
            await self.storage.mark_auto_review(classification.id, status="confirmed", note=reasoning)
            summary.reviews_confirmed += 1
        else:
            await self.storage.mark_auto_review(classification.id, status="uncertain", note=reasoning)
            summary.reviews_uncertain += 1
        return 1

    async def _adjudicate_flagged(self, person: Any, summary: AutoReviewSummary) -> int:
        candidates = [
            candidate
            for candidate in (person.candidate_companies or [])
            if isinstance(candidate, dict) and candidate.get("domain")
        ]
        context = {
            "person": {
                "email": person.email,
                "name": " ".join(part for part in [person.first_name, person.last_name] if part) or None,
                "self_reported_company": person.self_reported_company,
                "self_reported_title": person.self_reported_title,
                "self_reported_building": person.self_reported_building,
                "resolution_evidence": person.resolution_evidence,
                "people_search_results": person.exa_people_search_results,
            },
            "candidate_companies": candidates,
        }
        response = await self.llm_client.classify_tier(context, system_prompt=RESOLVE_SYSTEM_PROMPT, model=self.model)
        decision = _parse_json_response(response)
        verdict = str(decision.get("verdict") or "uncertain").strip().lower()
        reasoning = str(decision.get("reasoning") or "No reasoning returned.").strip()
        chosen = str(decision.get("chosen_domain") or "").strip().lower()
        candidate_domains = {str(candidate["domain"]).strip().lower() for candidate in candidates}

        if verdict == "resolve" and chosen in candidate_domains:
            await self.read_storage.resolve_flagged_person(person.id, chosen_domain=chosen)
            await self._post_resolution_followup(person.id)
            await self.storage.mark_auto_resolve_attempt(person.id, note=f"Resolved to {chosen}: {reasoning}")
            summary.conflicts_resolved += 1
        else:
            note = reasoning
            if verdict == "resolve":
                note = f"Proposed domain {chosen or '(none)'} is not a known candidate; left for a human. {reasoning}"
            await self.storage.mark_auto_resolve_attempt(person.id, note=note)
            summary.conflicts_uncertain += 1
        return 1

    async def _post_resolution_followup(self, person_id: Any) -> None:
        if self.enrichment_service is not None:
            try:
                context = await self.read_storage.enrichment_context_for_person(person_id)
            except Exception:
                context = None
            if context is not None:
                await self.enrichment_service.enrich_resolved_lead(context.input, context.resolution, context.person)
        if self.classification_service is not None:
            await self.classification_service.classify_batch(
                ClassificationScope(person_ids=[person_id], force_reclassify=True)
            )


def _parse_json_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict) and "verdict" in response:
        return response
    text = response if isinstance(response, str) else _response_text(response)
    if not text:
        raise ValueError("auto-review LLM response was empty")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("auto-review LLM response was not a JSON object")
    return parsed


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            return "\n".join(
                str(block.get("text"))
                for block in content
                if isinstance(block, dict) and block.get("text")
            )
        if isinstance(content, str):
            return content
    return ""
