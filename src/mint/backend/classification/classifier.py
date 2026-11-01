import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import yaml

from mint.backend.classification.gmi import LLMParseError
from mint.backend.db.tables import Company, Person
from mint.backend.models import ClassificationResult


TARGET_VERTICALS = {
    "research_labs",
    "coding_platforms",
    "agentic",
    "data_labeling",
    "robotics",
    "video_ai_multimodal",
}
PRODUCT_FITS = {"maas", "dedicated", "gpu"}
PIPELINE_VERSION = "mint-classifier-mql-spend-v3"
SELF_REPORTED_PIPELINE_VERSION = "mint-classifier-self-reported-v1"
REVIEW_THRESHOLD = 2
CLASSIFICATION_FAILED_FLAG = "classification_failed"
LLM_UNAVAILABLE_FLAG = "llm_unavailable"
PARSE_ERROR_FLAG = "parse_error"
FAILURE_NP_TRIGGERS = (CLASSIFICATION_FAILED_FLAG, LLM_UNAVAILABLE_FLAG, PARSE_ERROR_FLAG)
logger = logging.getLogger(__name__)

LLM_CLASSIFICATION_SYSTEM_PROMPT = """
You classify event leads for GMI Cloud. GMI has three offerings, and a lead is valuable if it fits any one of them.

Return JSON only with:
{
  "product_fit": ["maas"] | ["dedicated"] | ["gpu"] | ["maas","dedicated"] | [],
  "primary_product": "maas" | "dedicated" | "gpu" | null,
  "tier": "P0" | "P1" | "P2" | "NR",
  "confidence": 1-5,
  "np_trigger": string | null,
  "key_signal": string,
  "key_risk": string,
  "reasoning": string
}

GMI offerings:
1. MaaS (Model-as-a-Service): API inference. Fit = AI-native company running or scaling inference through model APIs, any real production traffic/users, AI is part of the product. Signals: serving AI features, scaling inference, multiple model vendors, API cost growth.
2. Dedicated Endpoints: reserved/private inference capacity. Fit = production AI at volume needing SLA/concurrency/private deployment. Signals: high request volume, rate-limit/latency pain, VPC/private deployment, AI is core revenue driver.
3. GPU Clusters: bare-metal training/fine-tuning. Fit = model training, fine-tuning 7B+ models, multi-GPU/multi-node, research labs, model companies. Signals: training budget, owns/needs GPUs, InfiniBand/bare-metal language, hyperscaler cost complaints for training.

Classify product fit first. Then estimate monthly AI infrastructure spend using compute_profile, spend_signals, funding/revenue evidence, company scale, and product usage. Spend estimates are directional bands, not exact accounting. Do not penalize a MaaS-fit lead for lacking GPU-cluster signals.

Priority rubric:
- P0: target vertical + estimated spend >= $20k/mo + Seed or above or clear revenue signal.
- P1: target vertical + estimated spend $10k-$20k/mo OR adjacent vertical + estimated spend >= $20k/mo.
- P2: target vertical + estimated spend $1k-$10k/mo OR adjacent vertical + estimated spend >= $10k/mo.
- NR: pre-revenue with no clear infra budget, estimated spend < $1k/mo, no product fit, not target/adjacent, non-buyer role, or structurally excluded.

Core target verticals: research_labs, coding_platforms, agentic, data_labeling, robotics, video_ai_multimodal.
Adjacent verticals include autonomous vehicles and CV/vision platforms.

Role definitions:
- decision_maker / buyer: founder, co-founder, C-level (CEO/CTO/CPO/etc.), VP, Head of Eng/Infra/AI/ML/Platform, Chief AI Officer, or role signals identify the person as the buyer/economic decision-maker for infrastructure.
- IC / non-decision-maker: engineers, scientists, data scientists, members of technical staff, developers, analysts - individual contributors, even senior ones, who influence but do not own the buying decision.
- non-buyer: SDR/AE/sales, marketing, recruiting, product marketing, ops, support - people with no plausible relationship to GPU/infra purchasing.

Guardrails:
- A junior IC is capped below P0 unless the company itself is clearly P0 and the person is a strong technical champion.
- A decision-maker at a target-vertical company with >= $20k/mo likely spend and Seed+/revenue signal should be P0.
- An early-stage agentic/API-first startup with production model API usage can be P0 only if spend appears >= $20k/mo and Seed+/revenue signal is present; otherwise use P1/P2 by spend band.
- A non-buyer role is NR unless there is a clear relationship path worth nurturing, in which case P2 is acceptable.
- product_fit=[] or primary_product=null is NR regardless of company quality.
- Respect Exa's uncertainty. If spend or revenue evidence is thin, choose the lower tier and flag for review rather than inflating P0/P1.
- Treat Lu.ma self-reported event data as first-party evidence. Specific self-reported company, role, and "what are you building" answers can establish product fit even when Apollo/Exa do not corroborate affiliation. Use Apollo/Exa to increase or challenge confidence, not as a prerequisite for classifying a specific Lu.ma claim.
- Confidence source policy: confidence 5 requires strong Lu.ma/self-reported evidence plus Apollo/Exa corroboration of company, role, and product need. Confidence 3 is appropriate when a strong P0/P1 fit rests mainly on Lu.ma self-reported data with no external affiliation corroboration.
- Gate flags are caution signals for review, not automatic exclusions. If a flag appears false-positive or irrelevant to the buyer/product-fit evidence, explain that and classify the lead normally with review. If the evidence cannot resolve the flag, choose the conservative tier and leave enough reasoning for human review.
- Weigh the person as a buyer. If role signals say the person is not an infra/GPU buyer, cap below P0.
- Respect affiliation. If affiliation_state is contradicted, do not assign P0 because the link is in conflict. If affiliation_state is unverified (resolved company but uncorroborated person link, common for early founders on personal email), you may still assign P0/P1 but flag for review.
- Company maturity sanity: one-person/hobby/pre-product/unidentifiable companies are not P0. On-device/offline/local inference implies little cloud GPU spend unless other strong evidence exists.
- Observability/BYOK tooling whose customers run inference elsewhere usually has product_fit=[] because the company itself is not buying GMI compute.
- Do not invent facts. Unknown means unknown.
""".strip()

SELF_REPORTED_SYSTEM_PROMPT = LLM_CLASSIFICATION_SYSTEM_PROMPT + """

Evidence policy for this run:
- Base product fit, buyer authority, spend-band inference, vertical, and tier only on the attendee's self-reported answers.
- The externally verified funding stage is supplied only as company-stage context; do not use other Apollo or Exa claims.
- Do not invent missing workload, authority, revenue, customers, or spend. Missing evidence must lower the tier or confidence.
- Prefix reasoning with "Self-reported-first:" so this run is auditable.
"""

STUDENT_RE = re.compile(
    r"\b(student|undergraduate|graduate student|phd student|masters student|ms student|"
    r"teaching assistant|research assistant)\b",
    re.IGNORECASE,
)
ACADEMIC_INSTITUTION_RE = re.compile(
    r"\b(university|college|higher education|school of|institute of technology)\b",
    re.IGNORECASE,
)
ACADEMIC_DESCRIPTION_RE = re.compile(
    r"\b(is|are|operates as|operating as)\s+(a|an|the)?\s*"
    r"(public|private|research)?\s*(university|college|higher education institution|institute of technology)\b",
    re.IGNORECASE,
)
GOVERNMENT_RE = re.compile(
    r"\b(government|public sector|defense|defence|dod|military|space agency|ffrdc|"
    r"state-owned|government-controlled)\b",
    re.IGNORECASE,
)
GOVERNMENT_DESCRIPTION_RE = re.compile(
    r"\b(is|are|operates as|operating as|registered as|organized as)\s+(a|an|the)?\s*"
    r"(government|public sector|state-owned|government-controlled|military|defense|defence)\s+"
    r"(agency|department|organization|organisation|entity|body|institution)\b",
    re.IGNORECASE,
)
HEALTHCARE_RE = re.compile(
    r"\b(healthcare|health care|clinical|biotech|pharma|pharmaceutical|hospital|"
    r"medical|medicine|patient|therapeutics|diagnostics)\b",
    re.IGNORECASE,
)
VC_FUND_RE = re.compile(
    r"\b(vc|venture capital|accelerator|incubator|investment fund|capital allocator|"
    r"private equity)\b",
    re.IGNORECASE,
)
CONSULTING_RE = re.compile(
    r"\b(consulting|consultancy|services firm|agency|design agency|studio)\b",
    re.IGNORECASE,
)
MARKETPLACE_RE = re.compile(
    r"\b(marketplace|aggregator|reseller|licensable stock|third-party content)\b",
    re.IGNORECASE,
)
NPO_NGO_RE = re.compile(
    r"\b(nonprofit|non-profit|not[- ]for[- ]profit|npo|ngo|foundation|charity|"
    r"501c3|501\(c\)\(3\)|association)\b",
    re.IGNORECASE,
)
NPO_NGO_DESCRIPTION_RE = re.compile(
    r"\b(is|are|operates as|operating as|registered as|organized as)\s+(a|an|the)?\s*"
    r"(nonprofit|non-profit|not[- ]for[- ]profit|npo|ngo|foundation|charity|association)\b|"
    r"\b501c3\b|\b501\(c\)\(3\)\b",
    re.IGNORECASE,
)
COMPUTE_HEAVY_RESEARCH_NONPROFIT_RE = re.compile(
    r"\b(ai2|allen institute for ai|allen ai|eleutherai|eleuther ai|laion|mlcommons?)\b",
    re.IGNORECASE,
)
DECISION_MAKER_RE = re.compile(
    r"\b(founder|co[- ]?founder|ceo|cto|cpo|cio|coo|chief (ai|technology|product|"
    r"executive|information|operating) officer|vp|vice president|head of "
    r"(engineering|eng|infra|infrastructure|ai|ml|machine learning|platform))\b",
    re.IGNORECASE,
)
IC_ROLE_RE = re.compile(
    r"\b(engineer|developer|scientist|data scientist|researcher|research scientist|"
    r"member of technical staff|technical staff|mts|analyst|programmer)\b",
    re.IGNORECASE,
)
NON_BUYER_ROLE_RE = re.compile(
    r"\b(sdr|sales development representative|account executive|sales|marketing|"
    r"product marketing|recruiter|recruiting|talent|operations|ops|support|"
    r"customer success|community|business development|bizdev)\b",
    re.IGNORECASE,
)
SENIOR_IC_TITLE_RE = re.compile(r"\b(staff|principal|distinguished|lead)\b", re.IGNORECASE)
TECHNICAL_STAFF_RE = re.compile(r"\b(member of )?technical staff\b", re.IGNORECASE)
SENIOR_IC_SENIORITIES = {"head", "director"}
BUYER_SIGNAL_RE = re.compile(
    r"\b(buyer|economic decision[- ]maker|owns? (the )?(gpu|infrastructure|infra|"
    r"compute) (budget|purchasing|procurement|decision)|evaluates? (gpu|infrastructure|"
    r"infra|compute))\b",
    re.IGNORECASE,
)


def load_icp_config(path: str | Path = "config/icp.yaml") -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class Classifier:
    def __init__(
        self,
        icp_config: dict[str, Any],
        llm_client: Any = None,
        model: str | None = None,
        competitor_terms: set[str] | None = None,
        bigtech_terms: set[str] | None = None,
    ) -> None:
        self.icp_config = icp_config
        self.llm_client = llm_client
        self.model = model
        self.target_verticals = _target_verticals(icp_config)
        self.competitor_terms = competitor_terms or _load_terms("config/competitor_terms.txt")
        self.bigtech_terms = bigtech_terms or _load_terms("config/bigtech_terms.txt")

    async def classify_person(
        self,
        person: Person,
        company: Company | None,
        *,
        evidence_mode: str = "hybrid",
    ) -> ClassificationResult | None:
        if (
            company is None
            or person.resolution_confidence not in {"high", "medium", "low"}
            or (
                person.resolution_confidence not in {"high", "medium"}
                and not _has_self_reported_evidence(person)
            )
        ):
            return self._result(
                evidence={
                    "affiliation_state": person.affiliation_state or "unverified",
                    "resolution_confidence": person.resolution_confidence or "low",
                    "data_quality": person.enrichment_data_quality or "sparse",
                    "vertical": "not_target",
                    "vertical_type": "not_target",
                    "funding_stage": "unknown",
                    "employee_count": None,
                    "has_revenue": False,
                    "is_pre_revenue": False,
                },
                tier="NR",
                np_trigger="sparse_data",
                product_fit=[],
                primary_product=None,
                confidence=1,
                key_signal="Registration retained for event review",
                key_risk="Company or identity could not be resolved well enough to assess product fit",
                reasoning="Insufficient verified company evidence; retained as a low-confidence nurture record instead of being omitted.",
                flags=["sparse_data", "unresolved_event_guest"],
                soft_gates=[],
            )

        evidence = self._extract_evidence(person, company, evidence_mode=evidence_mode)
        if evidence_mode == "self_reported":
            hard_gates, soft_gates = [], []
        else:
            hard_gates, soft_gates = self._gates(person, company, evidence)
        confidence_cap = _confidence_cap(evidence)

        soft_gates = _unique([*hard_gates, *soft_gates])
        flags = _unique(soft_gates)
        if self.llm_client is None:
            decision = _conservative_decision(
                "llm unavailable: no LLM client configured",
                np_trigger=LLM_UNAVAILABLE_FLAG,
            )
            flags = _unique([*flags, LLM_UNAVAILABLE_FLAG])
        else:
            try:
                decision = await self._llm_decision(
                    person,
                    company,
                    evidence,
                    soft_gates,
                    evidence_mode=evidence_mode,
                )
            except Exception as exc:
                failure_trigger = _failure_np_trigger(exc)
                _log_llm_failure(person, company, exc, failure_trigger)
                decision = _conservative_decision(
                    _failure_reason(exc, failure_trigger),
                    np_trigger=failure_trigger,
                )
                flags = _unique([*flags, failure_trigger])

        tier = _normalize_tier(decision.get("tier"))
        product_fit = _normalize_product_fit(decision.get("product_fit"), decision.get("primary_product"))
        primary_product = _normalize_primary_product(decision.get("primary_product"), product_fit)
        np_trigger = _normalize_np_trigger(decision.get("np_trigger"), tier)
        confidence = _normalize_confidence(decision.get("confidence"))
        if soft_gates:
            confidence = min(confidence, 3)
        if confidence_cap is not None:
            confidence = min(confidence, confidence_cap)
        tier, np_trigger, key_signal, key_risk, reasoning = _apply_boundary_post_checks(
            evidence=evidence,
            product_fit=product_fit,
            primary_product=primary_product,
            tier=tier,
            np_trigger=np_trigger,
            confidence=confidence,
            key_signal=_text_or_default(decision.get("key_signal"), f"{evidence['vertical']} fit"),
            key_risk=_text_or_default(decision.get("key_risk"), np_trigger or "Classification depends on source accuracy."),
            reasoning=_text_or_default(decision.get("reasoning"), "LLM returned no reasoning."),
        )
        if (
            tier in {"P0", "P1", "P2"}
            and evidence["affiliation_state"] == "unverified"
            and _has_self_reported_evidence(person)
        ):
            flags = _unique([*flags, "provisional_self_reported"])
            key_risk = (
                "Provisional fit based on attendee-supplied company or workload evidence; "
                "verify affiliation before outreach."
            )
            reasoning = _append_post_check(
                reasoning,
                "Kept as a provisional lead from first-party event evidence pending affiliation verification.",
            )
        return self._result(
            evidence=evidence,
            tier=tier,
            np_trigger=np_trigger,
            product_fit=product_fit,
            primary_product=primary_product,
            confidence=confidence,
            key_signal=key_signal,
            key_risk=key_risk,
            reasoning=reasoning,
            flags=flags,
            soft_gates=soft_gates,
            pipeline_version=(
                SELF_REPORTED_PIPELINE_VERSION if evidence_mode == "self_reported" else PIPELINE_VERSION
            ),
        )

    def _result(
        self,
        *,
        evidence: dict[str, Any],
        tier: str,
        np_trigger: str | None,
        product_fit: list[str],
        primary_product: str | None,
        confidence: int,
        key_signal: str,
        key_risk: str,
        reasoning: str,
        flags: list[str],
        soft_gates: list[str],
        pipeline_version: str = PIPELINE_VERSION,
    ) -> ClassificationResult:
        review_status = (
            "NEEDS_REVIEW"
            if confidence <= REVIEW_THRESHOLD
            or bool(soft_gates)
            or evidence["affiliation_state"] in {"unverified", "contradicted"}
            or evidence["resolution_confidence"] == "low"
            or evidence["data_quality"] in {"sparse", "unknown"}
            else "CLASSIFIED"
        )
        return ClassificationResult(
            tier=tier,
            np_trigger=np_trigger,
            product_fit=product_fit,
            primary_product=primary_product,
            confidence=confidence,
            reasoning=reasoning,
            key_signal=key_signal,
            key_risk=key_risk,
            flags=flags,
            review_status=review_status,
            pipeline_version=pipeline_version,
            vertical=evidence["vertical"],
            vertical_type=evidence["vertical_type"],
            funding_stage=evidence["funding_stage"],
            employee_count=evidence["employee_count"],
            has_revenue=evidence["has_revenue"],
            is_pre_revenue=evidence["is_pre_revenue"],
        )

    def _extract_evidence(
        self,
        person: Person,
        company: Company,
        *,
        evidence_mode: str = "hybrid",
    ) -> dict[str, Any]:
        if evidence_mode == "self_reported":
            self_text = _joined_text(
                person.self_reported_company,
                person.self_reported_title,
                person.self_reported_building,
            )
            vertical = _normalize_vertical(person.self_reported_building)
            funding_stage, _, _ = funding_stage_details(company, person.self_reported_building)
            has_revenue = bool(
                re.search(r"\b(revenue|arr|mrr|paying customers?|sales)\b", self_text, re.IGNORECASE)
            )
            is_pre_revenue = bool(re.search(r"\bpre[- ]revenue\b", self_text, re.IGNORECASE))
            if is_pre_revenue:
                has_revenue = False
            title = person.self_reported_title or ""
            role_category = _role_category(title, self_text)
            return {
                "email": person.email,
                "title": title,
                "apollo_seniority": None,
                "role_category": role_category,
                "is_senior_ic": _is_senior_ic(role_category, title, None),
                "affiliation_state": person.affiliation_state,
                "resolution_confidence": person.resolution_confidence,
                "self_reported_company": person.self_reported_company,
                "self_reported_title": person.self_reported_title,
                "self_reported_building": person.self_reported_building,
                "has_self_reported_evidence": _has_self_reported_evidence(person),
                "vertical": vertical,
                "vertical_type": (
                    "target" if vertical in self.target_verticals else "adjacent" if vertical == "adjacent" else "not_target"
                ),
                "funding_stage": funding_stage,
                "has_revenue": has_revenue,
                "is_pre_revenue": is_pre_revenue,
                "employee_count": None,
                "is_large_corp": False,
                "is_academic_institution": False,
                "data_quality": "moderate" if self_text else "sparse",
                "stage": funding_stage,
            }
        is_academic_institution = _is_academic_institution(company)
        vertical = "not_target" if is_academic_institution else _normalize_vertical(company.exa_target_vertical)
        vertical_type = "target" if vertical in self.target_verticals else "adjacent" if vertical == "adjacent" else "not_target"
        funding_stage = _funding_stage(company, person.self_reported_building)
        has_revenue = _has_revenue(company)
        is_pre_revenue = _is_pre_revenue(company)
        if is_pre_revenue:
            has_revenue = False
        employee_count = company.apollo_org_employee_count
        is_large_corp = bool(employee_count is not None and employee_count > 10000)
        clay_data = person.clay_person_data or {}
        clay_title = clay_data.get("title") if isinstance(clay_data, dict) else None
        title = person.apollo_person_title or clay_title or person.self_reported_title or ""
        role_text = _joined_text(title, person.apollo_person_seniority, person.exa_person_role_signals)
        role_category = _role_category(title, role_text)
        return {
            "email": person.email,
            "title": title,
            "apollo_seniority": person.apollo_person_seniority,
            "role_category": role_category,
            "is_senior_ic": _is_senior_ic(role_category, title, person.apollo_person_seniority),
            "affiliation_state": person.affiliation_state,
            "resolution_confidence": person.resolution_confidence,
            "self_reported_company": person.self_reported_company,
            "self_reported_title": person.self_reported_title,
            "self_reported_building": person.self_reported_building,
            "has_self_reported_evidence": _has_self_reported_evidence(person),
            "vertical": vertical,
            "vertical_type": vertical_type,
            "funding_stage": funding_stage,
            "has_revenue": has_revenue,
            "is_pre_revenue": is_pre_revenue,
            "employee_count": employee_count,
            "is_large_corp": is_large_corp,
            "is_academic_institution": is_academic_institution,
            "data_quality": _normalize_data_quality(company.exa_data_quality),
            "stage": str(company.exa_stage or "unknown").strip().lower() or "unknown",
        }

    def _gates(
        self,
        person: Person,
        company: Company,
        evidence: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        company_text = _joined_text(company.name, company.domain, person.email)
        title_text = _joined_text(evidence["title"])
        full_text = _joined_text(
            company_text,
            title_text,
            company.apollo_org_industry,
            company.exa_what_they_do,
            company.exa_stage,
            company.exa_enrichment,
        )
        identity_text = _joined_text(
            company_text,
            company.apollo_org_industry,
            company.exa_what_they_do,
        )

        hard: list[str] = []
        soft: list[str] = []
        if _contains_term(company_text, self.competitor_terms):
            hard.append("direct_competitor")
        evidence["bigtech_term_match"] = _contains_term(company_text, self.bigtech_terms)
        if evidence["bigtech_term_match"] or evidence["is_large_corp"]:
            hard.append("bigtech_or_large_corp")
        if STUDENT_RE.search(_joined_text(title_text, company.exa_enrichment)):
            hard.append("student_or_academic_role")
        if evidence["is_academic_institution"]:
            hard.append("academic_institution")
        government_identity_text = _joined_text(company.name, company.domain, company.apollo_org_industry)
        if GOVERNMENT_RE.search(government_identity_text) or GOVERNMENT_DESCRIPTION_RE.search(company.exa_what_they_do or ""):
            hard.append("government_public_sector")
        if _is_npo_ngo(company):
            hard.append("npo_ngo")

        if HEALTHCARE_RE.search(identity_text):
            soft.append("healthcare_biotech_pharma")
        if VC_FUND_RE.search(full_text):
            soft.append("vc_fund_capital_allocator")
        if CONSULTING_RE.search(full_text):
            soft.append("consulting_services_agency")
        if MARKETPLACE_RE.search(full_text):
            soft.append("marketplace_aggregator")
        return hard, soft

    async def _llm_decision(
        self,
        person: Person,
        company: Company,
        evidence: dict[str, Any],
        soft_gates: list[str],
        *,
        evidence_mode: str = "hybrid",
    ) -> dict[str, Any]:
        context = (
            _self_reported_llm_context(person, company, evidence)
            if evidence_mode == "self_reported"
            else _llm_context(person, company, evidence, soft_gates)
        )
        system_prompt = (
            SELF_REPORTED_SYSTEM_PROMPT
            if evidence_mode == "self_reported"
            else LLM_CLASSIFICATION_SYSTEM_PROMPT
        )
        if hasattr(self.llm_client, "classify_tier"):
            response = await self.llm_client.classify_tier(
                context,
                system_prompt=system_prompt,
                model=self.model,
            )
        elif hasattr(self.llm_client, "complete_classification"):
            response = await self.llm_client.complete_classification(
                context,
                system_prompt=system_prompt,
                model=self.model,
            )
        else:
            raise RuntimeError("LLM client does not implement classify_tier")
        return _parse_llm_decision(response)


def _target_verticals(config: dict[str, Any]) -> set[str]:
    raw = config.get("target_verticals") or TARGET_VERTICALS
    if isinstance(raw, list):
        values = {item.get("name") if isinstance(item, dict) else str(item) for item in raw}
        return {_normalize_vertical(value) for value in values if _normalize_vertical(value) in TARGET_VERTICALS}
    return set(TARGET_VERTICALS)


def _load_terms(path: str | Path) -> set[str]:
    try:
        return {
            line.strip().lower()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    except FileNotFoundError:
        return set()


def _normalize_vertical(value: Any) -> str:
    raw = str(value or "not_target").strip().lower()
    text = raw.replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "no_match": "not_target",
        "not_a_target": "not_target",
        "non_target": "not_target",
        "not_target": "not_target",
        "autonomous_vehicle": "adjacent",
        "autonomous_vehicles": "adjacent",
        "av": "adjacent",
        "cv": "adjacent",
        "computer_vision": "adjacent",
        "cv_vision": "adjacent",
        "vision": "adjacent",
        "vision_platform": "adjacent",
        "vision_platforms": "adjacent",
        "cv_vision_platforms": "adjacent",
        "video_ai": "video_ai_multimodal",
        "multimodal": "video_ai_multimodal",
        "video_multimodal": "video_ai_multimodal",
    }
    normalized = aliases.get(text, text)
    if normalized in TARGET_VERTICALS | {"adjacent", "not_target"}:
        return normalized

    plain = re.sub(r"[^a-z0-9]+", " ", raw)
    prose_patterns = [
        ("research_labs", r"\b(research labs?|ai research labs?|independent ai research)\b"),
        ("coding_platforms", r"\b(coding platforms?|developer tools?|dev tools?|code generation|coding agents?)\b"),
        ("agentic", r"\b(agentic|generative ai|genai|ai agents?)\b"),
        ("data_labeling", r"\b(data labeling|data annotation|labeling platforms?)\b"),
        ("robotics", r"\b(robotics?|robotic systems?)\b"),
        ("video_ai_multimodal", r"\b(video ai|video generation|multimodal|video multimodal|video models?)\b"),
    ]
    normalized_text = re.sub(r"[^a-z0-9_]+", "_", raw)
    explicit_matches = [
        (normalized_text.find(vertical), vertical)
        for vertical in TARGET_VERTICALS
        if vertical in normalized_text
    ]
    if explicit_matches:
        return min(explicit_matches)[1]
    for vertical, pattern in prose_patterns:
        if re.search(pattern, plain, re.IGNORECASE):
            return vertical
    if re.search(r"\b(autonomous vehicles?|computer vision|cv vision|vision platforms?|sensor fusion)\b", plain):
        return "adjacent"
    return "not_target"


def _is_academic_institution(company: Company) -> bool:
    domain = str(company.domain or "").strip().lower()
    identity_text = _joined_text(company.name, company.apollo_org_industry)
    description_text = _joined_text(company.exa_what_they_do)
    return (
        _is_academic_domain(domain)
        or bool(ACADEMIC_INSTITUTION_RE.search(identity_text))
        or bool(ACADEMIC_DESCRIPTION_RE.search(description_text))
    )


def _is_academic_domain(domain: str) -> bool:
    return bool(re.search(r"(\.edu|\.edu\.[a-z]{2,3}|\.ac\.[a-z]{2,3})$", domain, re.IGNORECASE))


def _is_npo_ngo(company: Company) -> bool:
    identity_text = _joined_text(company.name, company.domain, company.apollo_org_industry)
    description_text = _joined_text(company.exa_what_they_do)
    exception_text = _joined_text(identity_text, description_text)
    if COMPUTE_HEAVY_RESEARCH_NONPROFIT_RE.search(exception_text):
        return False
    return bool(NPO_NGO_RE.search(identity_text) or NPO_NGO_DESCRIPTION_RE.search(description_text))


def _funding_stage(company: Company, self_reported_text: str | None = None) -> str:
    return funding_stage_details(company, self_reported_text)[0]


def funding_stage_details(
    company: Company | None,
    self_reported_text: str | None = None,
) -> tuple[str, str, str]:
    """Return normalized stage, provenance, and confidence without fresh provider calls."""
    if company is None:
        from_self = _normalize_funding_stage(self_reported_text)
        return (from_self, "self_reported", "claimed") if from_self != "unknown" else ("unknown", "unknown", "unknown")
    from_apollo = _normalize_funding_stage(company.apollo_org_funding_stage)
    if from_apollo != "unknown":
        return from_apollo, "apollo", "verified"
    from_exa = _normalize_funding_stage(
        _joined_text((company.exa_enrichment or {}).get("funding_signals"), company.exa_enrichment)
    )
    if from_exa != "unknown":
        return from_exa, "exa", "verified"
    from_self = _normalize_funding_stage(self_reported_text)
    if from_self != "unknown":
        return from_self, "self_reported", "claimed"
    return "unknown", "unknown", "unknown"


def _normalize_funding_stage(value: Any) -> str:
    raw = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "preseed": "pre_seed",
        "pre_seed": "pre_seed",
        "seed": "seed",
        "series_a": "series_a",
        "series_b": "series_b",
        "series_c": "series_c_plus",
        "series_d": "series_c_plus",
        "series_e": "series_c_plus",
        "series_c_plus": "series_c_plus",
        "public": "public",
        "ipo": "public",
        "bootstrapped": "bootstrapped",
    }
    if raw in aliases:
        return aliases[raw]
    text = raw.replace("_", " ")
    if re.search(r"\b(pre seed|preseed)\b", text):
        return "pre_seed"
    if "bootstrapp" in text or "self funded" in text or "self-funded" in text:
        return "bootstrapped"
    if "series a" in text:
        return "series_a"
    if "series b" in text:
        return "series_b"
    if any(stage in text for stage in ("series c", "series d", "series e")):
        return "series_c_plus"
    if "public" in text or "ipo" in text:
        return "public"
    if "seed" in text:
        return "seed"
    return "unknown"


def _has_revenue(company: Company) -> bool:
    revenue = str(company.apollo_org_revenue or "").strip().lower()
    if revenue and revenue not in {"0", "$0", "none", "unknown", "n/a"}:
        return True
    stage = str(company.exa_stage or "").lower()
    if any(term in stage for term in ("early-revenue", "growth", "mature", "public")):
        return True
    text = _joined_text((company.exa_enrichment or {}).get("revenue_signals"))
    return bool(re.search(r"\b(pricing|paid|revenue|customer logos|customers|arr|sales)\b", text, re.IGNORECASE))


def _is_pre_revenue(company: Company) -> bool:
    stage = str(company.exa_stage or "").strip().lower()
    if "pre-revenue" in stage or "pre revenue" in stage:
        return True
    text = _joined_text((company.exa_enrichment or {}).get("revenue_signals"), company.exa_enrichment)
    return bool(re.search(r"\bpre[- ]revenue\b", text, re.IGNORECASE))


def _llm_context(
    person: Person,
    company: Company,
    evidence: dict[str, Any],
    soft_gates: list[str],
) -> dict[str, Any]:
    exa = company.exa_enrichment or {}
    return {
        "company": {
            "name": company.name,
            "domain": company.domain,
            "apollo_industry": company.apollo_org_industry,
            "apollo_employee_count": company.apollo_org_employee_count,
            "apollo_funding_stage": company.apollo_org_funding_stage,
            "apollo_total_funding": company.apollo_org_total_funding,
            "apollo_revenue": company.apollo_org_revenue,
            "exa_target_vertical": evidence["vertical"],
            "exa_target_vertical_raw": company.exa_target_vertical,
            "exa_stage": company.exa_stage,
            "exa_data_quality": company.exa_data_quality,
            "exa_what_they_do": company.exa_what_they_do or exa.get("what_they_do"),
            "exa_signals": {
                "inference_signals": _context_text(exa.get("inference_signals")),
                "spend_signals": _context_text(exa.get("spend_signals")),
                "funding_signals": _context_text(exa.get("funding_signals")),
                "revenue_signals": _context_text(exa.get("revenue_signals")),
                "customer_signals": _context_text(exa.get("customer_signals")),
                "competitors_or_current_infra": _context_text(exa.get("competitors_or_current_infra")),
                "hiring_signals": _context_text(exa.get("hiring_signals")),
                "recent_activity": _context_text(exa.get("recent_activity")),
            },
        },
        "person": {
            "title": evidence["title"],
            "apollo_seniority": person.apollo_person_seniority,
            "role_category": evidence["role_category"],
            "exa_person_role_signals": person.exa_person_role_signals or _context_text(exa.get("person_role_signals")),
            "self_reported_company": person.self_reported_company,
            "self_reported_title": person.self_reported_title,
            "self_reported_building": person.self_reported_building,
            "affiliation_state": person.affiliation_state,
        },
        "resolution_confidence": person.resolution_confidence,
        "gmi_offerings": {
            "maas": "Model-as-a-Service API inference for AI-native companies serving AI features through model APIs.",
            "dedicated": "Reserved/private inference endpoints for production AI at volume, SLA, concurrency, or private deployment needs.",
            "gpu": "Bare-metal GPU clusters for model training, fine-tuning, multi-GPU/multi-node, and research workloads.",
        },
        "facts": {
            "vertical": evidence["vertical"],
            "vertical_type": evidence["vertical_type"],
            "funding_stage": evidence["funding_stage"],
            "has_revenue": evidence["has_revenue"],
            "is_pre_revenue": evidence["is_pre_revenue"],
            "employee_count": evidence["employee_count"],
            "data_quality": evidence["data_quality"],
            "stage": evidence["stage"],
        },
        "soft_gates": soft_gates,
    }


def _self_reported_llm_context(
    person: Person,
    company: Company,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    stage, stage_source, stage_confidence = funding_stage_details(
        company,
        person.self_reported_building,
    )
    return {
        "evidence_policy": "self_reported_only_except_company_stage",
        "attendee_answers": {
            "company": person.self_reported_company,
            "title": person.self_reported_title,
            "answers": person.self_reported_building,
        },
        "company_stage": {
            "value": stage,
            "source": stage_source,
            "confidence": stage_confidence,
        },
        "derived_from_attendee_answers": {
            "vertical": evidence["vertical"],
            "vertical_type": evidence["vertical_type"],
            "role_category": evidence["role_category"],
            "has_revenue_claim": evidence["has_revenue"],
            "is_pre_revenue_claim": evidence["is_pre_revenue"],
        },
        "gmi_offerings": {
            "maas": "Model-as-a-Service API inference.",
            "dedicated": "Reserved/private production inference endpoints.",
            "gpu": "Bare-metal GPU clusters for training and fine-tuning.",
        },
    }


def _role_category(title: str, role_text: str) -> str:
    if NON_BUYER_ROLE_RE.search(title):
        return "non_buyer"
    if DECISION_MAKER_RE.search(title):
        return "decision_maker"
    if IC_ROLE_RE.search(title):
        return "ic"
    if NON_BUYER_ROLE_RE.search(role_text):
        return "non_buyer"
    if BUYER_SIGNAL_RE.search(role_text) and not _negates_buyer_signal(role_text):
        return "decision_maker"
    if IC_ROLE_RE.search(role_text):
        return "ic"
    return "unknown"


def _is_senior_ic(role_category: str, title: str, apollo_seniority: str | None) -> bool:
    if role_category != "ic":
        return False
    if str(apollo_seniority or "").strip().lower() in SENIOR_IC_SENIORITIES:
        return True
    # "Member of Technical Staff" must not count as a staff-level title.
    cleaned_title = TECHNICAL_STAFF_RE.sub("", title)
    return bool(SENIOR_IC_TITLE_RE.search(cleaned_title))


def _has_self_reported_evidence(person: Person) -> bool:
    return bool(
        _context_text(person.self_reported_company)
        or _context_text(person.self_reported_title)
        or _context_text(person.self_reported_building)
    )


def _confidence_cap(evidence: dict[str, Any]) -> int | None:
    if evidence["resolution_confidence"] == "low" and evidence["has_self_reported_evidence"]:
        return 3
    return None


def _negates_buyer_signal(text: str) -> bool:
    return bool(
        re.search(
            r"\b(no evidence|not|does not|doesn't|do not|don't|cannot|can't|unlikely)\b.{0,80}"
            r"\b(buyer|decision[- ]maker|own|owns|purchasing|procurement|budget|evaluates?)\b",
            text,
            re.IGNORECASE,
        )
    )


def _normalize_data_quality(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in {"rich", "moderate", "sparse"} else "unknown"


def _apply_boundary_post_checks(
    *,
    evidence: dict[str, Any],
    product_fit: list[str],
    primary_product: str | None,
    tier: str,
    np_trigger: str | None,
    confidence: int,
    key_signal: str,
    key_risk: str,
    reasoning: str,
) -> tuple[str, str | None, str, str, str]:
    if tier not in {"P0", "P1", "P2"}:
        return tier, np_trigger, key_signal, key_risk, reasoning

    if not product_fit or primary_product is None:
        return (
            "NR",
            "no_product_fit",
            key_signal,
            "No GMI offering fit was identified.",
            _append_post_check(reasoning, "No product fit moved the lead to NR."),
        )
    if evidence["role_category"] == "non_buyer":
        return (
            "NR",
            "non_buyer_role",
            key_signal,
            "Lead role is not related to GPU/infra purchasing.",
            _append_post_check(reasoning, "Non-buyer role moved the lead to NR."),
        )
    if evidence["vertical_type"] == "not_target":
        return (
            "NR",
            "not_target",
            key_signal,
            "Company is not in a target or adjacent vertical.",
            _append_post_check(reasoning, "Not-target vertical moved the lead to NR."),
        )
    decision_maker_with_fit = (
        evidence["role_category"] == "decision_maker" and primary_product is not None
    )
    if evidence["data_quality"] in {"sparse", "unknown"} and not decision_maker_with_fit:
        if tier == "P0":
            return (
                "P1",
                None,
                key_signal,
                "Sparse or unknown enrichment quality; review before pursuit.",
                _append_post_check(reasoning, "Sparse/unknown data quality kept the lead at P1 pending review."),
            )
        reasoning = _append_post_check(reasoning, "Sparse/unknown data quality flagged the lead for review.")
    if confidence <= REVIEW_THRESHOLD:
        return (
            "P1" if tier == "P0" else tier,
            None,
            key_signal,
            "Confidence <= 2; needs human review before pursuit.",
            _append_post_check(reasoning, "Confidence <= 2 kept the lead at P1 needing review."),
        )

    if tier == "P0":
        if evidence["vertical_type"] == "adjacent":
            return (
                "P1",
                None,
                key_signal,
                key_risk,
                _append_post_check(reasoning, "Adjacent vertical capped P0 to P1."),
            )
        if evidence["affiliation_state"] == "contradicted":
            return (
                "P1",
                None,
                key_signal,
                key_risk,
                _append_post_check(reasoning, "Contradicted affiliation capped P0 to P1."),
            )
        if evidence["role_category"] != "decision_maker" and not evidence["is_senior_ic"]:
            note = (
                "IC role capped P0 to P1."
                if evidence["role_category"] == "ic"
                else "Non-decision-maker role capped P0 to P1."
            )
            return (
                "P1",
                None,
                key_signal,
                key_risk,
                _append_post_check(reasoning, note),
            )

    return tier, np_trigger, key_signal, key_risk, reasoning


def _append_post_check(reasoning: str, note: str) -> str:
    return f"{reasoning} Post-check: {note}"


def _context_text(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        return "; ".join(str(item) for item in value if str(item).strip()) or None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _parse_llm_decision(response: Any) -> dict[str, Any]:
    if isinstance(response, dict) and response.get("tier"):
        return response
    text = _response_text(response)
    if not text:
        raise ValueError("LLM response was empty")
    try:
        parsed = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object")
    return parsed


def _normalize_product_fit(value: Any, primary_product: Any = None) -> list[str]:
    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = re.split(r"[,;/\s]+", value)
    else:
        raw_values = []

    products: list[str] = []
    for item in raw_values:
        product = _normalize_product_name(item)
        if product and product not in products:
            products.append(product)

    primary = _normalize_product_name(primary_product)
    if primary and primary not in products:
        products.insert(0, primary)
    return products


def _normalize_primary_product(value: Any, product_fit: list[str]) -> str | None:
    primary = _normalize_product_name(value)
    if primary and primary in product_fit:
        return primary
    return product_fit[0] if product_fit else None


def _normalize_product_name(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "model_as_a_service": "maas",
        "model_api": "maas",
        "api_inference": "maas",
        "api": "maas",
        "inference_api": "maas",
        "dedicated_endpoint": "dedicated",
        "dedicated_endpoints": "dedicated",
        "private_endpoint": "dedicated",
        "private_endpoints": "dedicated",
        "reserved_inference": "dedicated",
        "gpu_cluster": "gpu",
        "gpu_clusters": "gpu",
        "bare_metal": "gpu",
        "bare_metal_gpus": "gpu",
        "training": "gpu",
        "none": "",
        "null": "",
        "no_product_fit": "",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in PRODUCT_FITS else None


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list):
            return "\n".join(str(item.get("text")) for item in content if isinstance(item, dict) and item.get("text"))
        if isinstance(content, str):
            return content
    return ""


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _normalize_tier(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in {"P0", "P1", "P2", "NR", "NP"} else "NR"


def _normalize_np_trigger(value: Any, tier: str) -> str | None:
    if tier not in {"NP", "NR"}:
        return None
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {
        "direct_competitor",
        "bigtech_or_large_corp",
        "student_or_academic_role",
        "academic_institution",
        "government_public_sector",
        "npo_ngo",
        "large_corp_10k_plus",
        "pre_revenue",
        "sparse_data",
        "low_confidence",
        "no_product_fit",
        "llm_unavailable",
        "parse_error",
        "non_buyer_role",
        "pre_product",
        "not_target",
        "classification_failed",
        "does_not_meet",
    }
    return text if text in allowed else "does_not_meet"


def _normalize_confidence(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(5, parsed))


def _text_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _conservative_decision(reason: str, *, np_trigger: str = "classification_failed") -> dict[str, Any]:
    return {
        "product_fit": [],
        "primary_product": None,
        "tier": "NP",
        "confidence": 2,
        "np_trigger": np_trigger,
        "key_signal": "Classification unavailable.",
        "key_risk": "Needs review before outreach.",
        "reasoning": reason,
    }


def _failure_np_trigger(exc: Exception) -> str:
    if isinstance(exc, (LLMParseError, json.JSONDecodeError, ValueError)):
        return PARSE_ERROR_FLAG
    if isinstance(exc, (httpx.HTTPStatusError, httpx.TimeoutException)):
        return LLM_UNAVAILABLE_FLAG
    return CLASSIFICATION_FAILED_FLAG


def _failure_reason(exc: Exception, np_trigger: str) -> str:
    summary = f"{np_trigger}: {type(exc).__name__}: {str(exc)[:300]}"
    snippet = _exception_snippet(exc)
    if snippet:
        summary = f"{summary}; response_snippet={snippet}"
    return summary


def _log_llm_failure(person: Person, company: Company, exc: Exception, np_trigger: str) -> None:
    logger.warning(
        "LLM classification failed email=%s company=%s domain=%s trigger=%s error_type=%s status=%s error=%s snippet=%s",
        person.email,
        company.name,
        company.domain,
        np_trigger,
        type(exc).__name__,
        _exception_status(exc),
        str(exc),
        _exception_snippet(exc),
    )


def _exception_status(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def _exception_snippet(exc: Exception) -> str | None:
    raw_text = getattr(exc, "raw_text", None)
    if raw_text:
        return str(raw_text)[-500:].replace("\n", " ")
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.text[:500].replace("\n", " ")
    return None


def _contains_term(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered)
    for term in terms:
        term_lower = term.lower()
        if "." in term_lower or "-" in term_lower:
            if term_lower in lowered:
                return True
            continue
        term_normalized = re.sub(r"[^a-z0-9]+", " ", term_lower).strip()
        if term_normalized and re.search(rf"\b{re.escape(term_normalized)}\b", normalized):
            return True
    return False


def _joined_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    return " ".join(parts)


def _unique(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            output.append(flag)
    return output
