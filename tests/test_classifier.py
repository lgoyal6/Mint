import asyncio
import logging

import httpx

from mint.backend.classification.classifier import Classifier, load_icp_config
from mint.backend.db.tables import Company, Person


def run(coro):
    return asyncio.run(coro)


class TierLlm:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def classify_tier(self, context, **kwargs):
        self.calls.append((context, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def person(
    *,
    email: str = "alex@quadex.ai",
    title: str | None = "Founder",
    seniority: str | None = "founder",
    resolution_confidence: str = "high",
    affiliation_state: str = "verified",
    role_signals: str = "Founder likely evaluates GPU infrastructure.",
    self_reported_company: str | None = None,
    self_reported_title: str | None = None,
    building: str | None = "AI agents",
) -> Person:
    return Person(
        email=email,
        first_name="Alex",
        last_name="Rivera",
        resolution_confidence=resolution_confidence,
        affiliation_state=affiliation_state,
        apollo_person_title=title,
        apollo_person_seniority=seniority,
        exa_person_role_signals=role_signals,
        self_reported_company=self_reported_company,
        self_reported_title=self_reported_title,
        self_reported_building=building,
    )


def company(
    *,
    name: str = "Bria AI",
    domain: str = "bria.ai",
    vertical: str = "agentic",
    stage: str = "growth",
    funding_stage: str | None = "Series A",
    total_funding: str | None = "$24M",
    revenue: str | None = "$1M",
    employee_count: int | None = 75,
    industry: str = "software",
    data_quality: str = "rich",
    what_they_do: str | None = None,
    inference: str = "Runs GPU-heavy image generation inference APIs for customers.",
    spend: str = "Strong evidence of production GPU inference demand.",
    funding: str | None = None,
    revenue_signals: str | None = None,
) -> Company:
    return Company(
        name=name,
        domain=domain,
        exa_target_vertical=vertical,
        exa_stage=stage,
        exa_what_they_do=what_they_do or f"{name} builds AI products.",
        exa_data_quality=data_quality,
        apollo_org_funding_stage=funding_stage,
        apollo_org_total_funding=total_funding,
        apollo_org_revenue=revenue,
        apollo_org_employee_count=employee_count,
        apollo_org_industry=industry,
        exa_enrichment={
            "what_they_do": what_they_do or f"{name} builds AI products.",
            "stage": stage,
            "funding_signals": funding if funding is not None else funding_stage or "unknown",
            "revenue_signals": revenue_signals if revenue_signals is not None else revenue or "",
            "inference_signals": inference,
            "spend_signals": spend,
            "customer_signals": "Customer traction in production.",
            "competitors_or_current_infra": "Uses cloud GPUs today.",
            "hiring_signals": ["Hiring ML infrastructure engineers"],
            "recent_activity": ["Recent product launch"],
            "person_role_signals": "Founder likely evaluates infrastructure.",
            "data_quality": data_quality,
        },
    )


def classifier(llm_client=None) -> Classifier:
    return Classifier(load_icp_config(), llm_client=llm_client)


def decision(
    tier: str,
    *,
    confidence: int = 4,
    np_trigger: str | None = None,
    product_fit: list[str] | None = None,
    primary_product: str | None = "maas",
    key_signal: str = "Strong GPU inference fit.",
    key_risk: str = "Execution depends on source accuracy.",
    reasoning: str = "Grounded classification from provided enrichment context.",
) -> dict:
    return {
        "product_fit": ["maas"] if product_fit is None else product_fit,
        "primary_product": primary_product,
        "tier": tier,
        "confidence": confidence,
        "np_trigger": np_trigger,
        "key_signal": key_signal,
        "key_risk": key_risk,
        "reasoning": reasoning,
    }


def test_llm_receives_full_enrichment_context_and_can_assign_p0() -> None:
    llm = TierLlm(decision("P0", confidence=5, key_signal="Funded GPU inference company with CTO buyer."))

    result = run(
        classifier(llm).classify_person(
            person(title="CTO", seniority="c_suite"),
            company(name="Bria AI", domain="bria.ai", vertical="video_ai_multimodal"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.confidence == 5
    assert result.pipeline_version == "mint-classifier-mql-spend-v3"
    assert result.product_fit == ["maas"]
    assert result.primary_product == "maas"
    assert result.vertical == "video_ai_multimodal"
    assert result.vertical_type == "target"
    assert result.employee_count == 75
    assert not hasattr(result, "spend_band")

    context, kwargs = llm.calls[0]
    assert kwargs["model"] is None
    assert "P0" in kwargs["system_prompt"]
    assert context["company"]["name"] == "Bria AI"
    assert context["company"]["apollo_employee_count"] == 75
    assert context["company"]["exa_data_quality"] == "rich"
    assert context["company"]["exa_signals"]["inference_signals"].startswith("Runs GPU-heavy")
    assert context["company"]["exa_signals"]["hiring_signals"] == "Hiring ML infrastructure engineers"
    assert context["person"]["title"] == "CTO"
    assert context["person"]["exa_person_role_signals"] == "Founder likely evaluates GPU infrastructure."
    assert context["person"]["affiliation_state"] == "verified"
    assert context["resolution_confidence"] == "high"
    assert "MaaS" in kwargs["system_prompt"]
    assert "product_fit" in kwargs["system_prompt"]


def test_api_first_agentic_founder_can_be_p0_as_maas_fit() -> None:
    llm = TierLlm(
        decision(
            "P0",
            confidence=4,
            product_fit=["maas"],
            primary_product="maas",
            key_signal="Founder at API-first agentic company with production inference.",
            key_risk="Early company but inference demand is grounded.",
            reasoning="The company routes RAG/agent inference through APIs for production users, making it a MaaS fit.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Founder / CEO", seniority="founder"),
            company(
                name="Skald",
                domain="skald.ai",
                vertical="agentic",
                stage="early-revenue",
                data_quality="moderate",
                employee_count=2,
                inference="Runs RAG and agent inference through model APIs for production users.",
                spend="API inference cost growth as traffic scales.",
                revenue_signals="Early paid users.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.product_fit == ["maas"]
    assert result.primary_product == "maas"


def test_company_owning_inference_on_real_gpus_can_be_p0() -> None:
    llm = TierLlm(
        decision(
            "P0",
            confidence=5,
            product_fit=["dedicated", "gpu"],
            primary_product="dedicated",
            key_signal="CTO at company serving production inference on H200s.",
            key_risk="Capacity needs may span serving and training.",
            reasoning="The enrichment says the company owns production inference on H200s and needs reserved/private serving capacity.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="CTO", seniority="c_suite"),
            company(
                name="InferenceWorks",
                domain="inferenceworks.ai",
                vertical="video_ai_multimodal",
                data_quality="rich",
                inference="Owns production inference on H200 GPUs for customer workloads.",
                spend="Reserved GPU capacity and high concurrency serving needs.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.product_fit == ["dedicated", "gpu"]
    assert result.primary_product == "dedicated"


def test_no_product_fit_forces_np_even_if_llm_returns_p1() -> None:
    result = run(
        classifier(
            TierLlm(
                decision(
                    "P1",
                    confidence=4,
                    product_fit=[],
                    primary_product=None,
                    key_signal="Observability SaaS with AI-adjacent customers.",
                    key_risk="Customers run inference elsewhere.",
                    reasoning="The company sells observability/BYOK tooling and does not appear to buy inference or GPU capacity itself.",
                )
            )
        ).classify_person(
            person(title="Founder"),
            company(
                name="TraceObs",
                domain="traceobs.ai",
                vertical="agentic",
                data_quality="moderate",
                inference="BYOK observability tool; customer workloads run elsewhere.",
                spend="No evidence the company buys inference or GPUs.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NR"
    assert result.np_trigger == "no_product_fit"
    assert result.product_fit == []
    assert result.primary_product is None


def test_training_lab_multigpu_research_lead_classifies_with_gpu_fit() -> None:
    llm = TierLlm(
        decision(
            "P1",
            confidence=4,
            product_fit=["gpu"],
            primary_product="gpu",
            key_signal="Research lead at multi-GPU model-training lab.",
            key_risk="Research lead may influence rather than own budget.",
            reasoning="The company trains and fine-tunes multi-GPU models, making GPU clusters the strongest product fit.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(
                title="Research Scientist",
                seniority="individual_contributor",
                role_signals="Research lead for model training; influences infrastructure choices.",
            ),
            company(
                name="OpenModel Lab",
                domain="openmodel.ai",
                vertical="research_labs",
                data_quality="rich",
                inference="Trains and fine-tunes 7B+ models on multi-GPU infrastructure.",
                spend="Multi-node GPU training workloads.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.product_fit == ["gpu"]
    assert result.primary_product == "gpu"


def test_sparse_one_employee_on_device_company_is_np_when_llm_says_no_fit() -> None:
    llm = TierLlm(
        decision(
            "NP",
            confidence=2,
            np_trigger="no_product_fit",
            product_fit=[],
            primary_product=None,
            key_signal="On-device inference with sparse data.",
            key_risk="No evidence of cloud GPU spend.",
            reasoning="The context says the company is one person, stage is unknown, data is sparse, and inference runs locally/on-device.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Founder"),
            company(
                name="Chinese Powered Labs",
                domain="chinesepoweredlabs.com",
                vertical="agentic",
                stage="unknown",
                funding_stage=None,
                revenue=None,
                employee_count=1,
                data_quality="sparse",
                inference="On-device/offline inference on the user's machine.",
                spend="No verified cloud GPU spend.",
                funding="unknown",
                revenue_signals="unknown",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "no_product_fit"
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"


def test_unidentifiable_sparse_company_is_np() -> None:
    llm = TierLlm(
        decision(
            "NP",
            confidence=2,
            np_trigger="sparse_data",
            key_signal="Insufficient verified company evidence.",
            key_risk="Company identity and maturity are unclear.",
            reasoning="The enrichment context is sparse and says there is insufficient evidence of product, funding, revenue, or GPU need.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(email="builder@openmagi.ai"),
            company(
                name="Open Magi",
                domain="openmagi.ai",
                vertical="agentic",
                stage="unknown",
                funding_stage=None,
                revenue=None,
                employee_count=None,
                data_quality="sparse",
                what_they_do="Insufficient verified details found.",
                inference="unknown",
                spend="unknown",
                funding="unknown",
                revenue_signals="unknown",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "sparse_data"
    assert result.confidence == 2


def test_open_source_dev_tool_is_not_promoted_to_p0() -> None:
    llm = TierLlm(
        decision(
            "P1",
            confidence=3,
            np_trigger=None,
            key_signal="Relevant dev-tooling project but limited company maturity evidence.",
            key_risk="No verified funding or revenue.",
            reasoning="RocketRide is relevant to coding platforms, but the context lacks funding/revenue proof, so it cannot be P0.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Founder"),
            company(
                name="RocketRide",
                domain="rocketride.dev",
                vertical="coding_platforms",
                stage="unknown",
                funding_stage=None,
                revenue=None,
                employee_count=3,
                data_quality="moderate",
                inference="Open-source developer tool with unclear production GPU usage.",
                spend="No verified spend.",
                funding="unknown",
                revenue_signals="unknown",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.confidence == 3


def test_non_buyer_role_is_capped_below_p0_by_llm() -> None:
    llm = TierLlm(
        decision(
            "P1",
            confidence=3,
            key_signal="Good company, weak buyer fit.",
            key_risk="Lead is a software engineer, not an infra/GPU buyer.",
            reasoning="The company may fit, but the role signals say this person is not an infrastructure buyer, so it is capped below P0.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Software Engineer", role_signals="Software Engineer; no evidence he is an infra/GPU buyer."),
            company(name="GoodGPU", domain="goodgpu.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.confidence == 3
    assert "not an infrastructure buyer" in result.reasoning


def test_ic_at_strong_target_company_is_capped_to_p1_even_if_llm_returns_p0() -> None:
    llm = TierLlm(
        decision(
            "P0",
            confidence=4,
            key_signal="Strong company fit.",
            key_risk="Role is an IC.",
            reasoning="The company is a strong target, but the lead is a Member of Technical Staff.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(
                title="Member of Technical Staff",
                seniority="individual_contributor",
                role_signals="Technical IC; can influence but does not own infrastructure purchasing.",
            ),
            company(name="Edison Scientific", domain="edisonscientific.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.confidence == 4
    assert "IC role capped P0 to P1" in result.reasoning


def test_decision_maker_at_grounded_target_company_can_be_p0() -> None:
    llm = TierLlm(
        decision(
            "P0",
            confidence=4,
            key_signal="Founder at grounded target-vertical inference company.",
            key_risk="Normal enrichment uncertainty.",
            reasoning="The lead is the Founder/CEO at a grounded target-vertical company with production GPU inference signals.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Founder / CEO", seniority="founder"),
            company(name="Asapi", domain="asapi.ai", vertical="agentic", data_quality="moderate"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.confidence == 4
    context, kwargs = llm.calls[0]
    assert context["person"]["role_category"] == "decision_maker"
    assert "decision_maker" in kwargs["system_prompt"]


def test_non_buyer_role_is_np_even_if_llm_returns_p1() -> None:
    result = run(
        classifier(TierLlm(decision("P1", confidence=3))).classify_person(
            person(
                title="Sales Development Representative",
                role_signals="SDR; no plausible relationship to GPU or infrastructure purchasing.",
            ),
            company(name="Pylon", domain="pylon.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "NR"
    assert result.np_trigger == "non_buyer_role"
    assert result.confidence == 3


def test_confidence_two_ic_with_product_fit_is_kept_as_p1_review() -> None:
    result = run(
        classifier(TierLlm(decision("P1", confidence=2))).classify_person(
            person(
                title="Software Engineer",
                seniority="individual_contributor",
                role_signals="IC engineer who can influence but does not own infrastructure buying.",
            ),
            company(name="Thin Agent Co", domain="thinagent.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"
    assert "Confidence <= 2" in result.reasoning


def test_confidence_two_decision_maker_with_product_fit_goes_to_p1_review() -> None:
    result = run(
        classifier(
            TierLlm(
                decision(
                    "P0",
                    confidence=2,
                    product_fit=["maas"],
                    primary_product="maas",
                    key_signal="Founder at target-vertical API inference company.",
                    key_risk="Confidence is low but the product fit is real.",
                    reasoning="The company has grounded API inference need, but the model is only confidence 2.",
                )
            )
        ).classify_person(
            person(title="Founder / CEO"),
            company(
                name="Kalavai",
                domain="kalavai.ai",
                vertical="agentic",
                data_quality="moderate",
                inference="Runs API inference for agent workflows.",
                spend="Model API usage is scaling with users.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"
    assert "Confidence <= 2" in result.reasoning


def test_adjacent_vertical_decision_maker_is_capped_to_p1_even_if_llm_returns_p0() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(title="Founder"),
            company(name="Vision Autonomy", domain="visionautonomy.ai", vertical="adjacent"),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert "Adjacent vertical capped P0 to P1" in result.reasoning


def test_sparse_decision_maker_target_with_product_fit_can_stay_p0_but_needs_review() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(title="Founder"),
            company(
                name="Sparse Agent Lab",
                domain="sparseagent.ai",
                vertical="agentic",
                data_quality="sparse",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.np_trigger is None
    assert result.review_status == "NEEDS_REVIEW"


def test_sparse_ic_with_product_fit_is_kept_as_p1_review() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(title="Software Engineer", seniority="senior", role_signals="IC engineer."),
            company(
                name="Sparse Agent Lab",
                domain="sparseagent.ai",
                vertical="agentic",
                data_quality="sparse",
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.review_status == "NEEDS_REVIEW"
    assert "Sparse" in result.reasoning


def test_sparse_non_target_still_downgrades_to_np() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(title="Founder"),
            company(
                name="Sparse Fintech",
                domain="sparsefintech.com",
                vertical="no_match",
                data_quality="sparse",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NR"
    assert result.np_trigger == "not_target"


def test_staff_engineer_at_target_company_can_stay_p0() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(
                title="Staff Software Engineer",
                seniority="individual_contributor",
                role_signals="Senior IC who owns the inference platform roadmap.",
            ),
            company(name="Edison Scientific", domain="edisonscientific.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.np_trigger is None


def test_head_seniority_ic_can_stay_p0() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(
                title="ML Infrastructure Engineer",
                seniority="head",
                role_signals="Heads the inference platform team.",
            ),
            company(name="Edison Scientific", domain="edisonscientific.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.np_trigger is None


def test_unverified_affiliation_for_target_decision_maker_can_be_p0_but_needs_review() -> None:
    llm = TierLlm(
        decision(
            "P0",
            confidence=3,
            key_signal="GPU inference infrastructure company.",
            key_risk="Affiliation is unverified.",
            reasoning="Delos has relevant GPU infrastructure signals and the lead is a founder-level buyer.",
        )
    )

    result = run(
        classifier(llm).classify_person(
            person(
                email="founder@gmail.com",
                title="Founder",
                affiliation_state="unverified",
                role_signals="May not actually work at Delos.",
            ),
            company(name="Delos", domain="delos.ai", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.review_status == "NEEDS_REVIEW"


def test_contradicted_affiliation_still_caps_p0_to_p1() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4))).classify_person(
            person(title="Founder", affiliation_state="contradicted"),
            company(name="Delos", domain="delos.ai", vertical="agentic", data_quality="moderate"),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.review_status == "NEEDS_REVIEW"
    assert "Contradicted affiliation capped P0 to P1" in result.reasoning


def test_direct_competitor_flag_is_reviewed_by_llm() -> None:
    llm = TierLlm(
        decision("NP", confidence=3, np_trigger="direct_competitor", product_fit=[], primary_product=None)
    )

    result = run(
        classifier(llm).classify_person(
            person(email="founder@replicate.com"),
            company(name="Replicate", domain="replicate.com"),
        )
    )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "direct_competitor"
    assert "direct_competitor" in result.flags
    assert result.review_status == "NEEDS_REVIEW"
    assert len(llm.calls) == 1


def test_large_corp_employee_count_flag_is_reviewed_by_llm() -> None:
    llm = TierLlm(decision("P1", confidence=4, product_fit=["gpu"], primary_product="gpu"))

    result = run(
        classifier(llm).classify_person(
            person(email="aman@example.com"),
            company(
                name="Ouster",
                domain="ouster.com",
                vertical="robotics",
                employee_count=50000,
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.review_status == "NEEDS_REVIEW"
    assert result.confidence == 3
    assert "bigtech_or_large_corp" in result.flags
    assert len(llm.calls) == 1


def test_student_title_is_review_flag_but_edu_email_alone_is_not() -> None:
    llm = TierLlm(decision("P1", confidence=3))
    student = run(
        classifier(llm).classify_person(
            person(email="builder@berkeley.edu", title="Student"),
            company(name="Campus Agent Lab", domain="campus.ai"),
        )
    )
    edu_email_founder = run(
        classifier(llm).classify_person(
            person(email="founder@berkeley.edu", title="Founder"),
            company(name="Caprae Capital Partners", domain="capraecapital.com"),
        )
    )

    assert student is not None
    assert student.tier == "P1"
    assert student.np_trigger is None
    assert student.review_status == "NEEDS_REVIEW"
    assert "student_or_academic_role" in student.flags

    assert edu_email_founder is not None
    assert edu_email_founder.tier == "P1"
    assert "student_or_academic_role" not in edu_email_founder.flags


def test_university_company_cannot_be_promoted_to_target_vertical() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=4, product_fit=["gpu"], primary_product="gpu"))).classify_person(
            person(email="founder@startup.com", title="Founder"),
            company(
                name="University of Florida",
                domain="ufl.edu",
                vertical="robotics",
                industry="higher education",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NR"
    assert result.np_trigger == "not_target"
    assert result.vertical == "not_target"
    assert result.vertical_type == "not_target"
    assert "academic_institution" in result.flags
    assert result.review_status == "NEEDS_REVIEW"


def test_university_mentions_do_not_make_company_an_academic_institution() -> None:
    target_company = company(
        name="Vidu AI",
        domain="vidu.com",
        vertical="video_ai_multimodal",
        industry="software",
    )
    target_company.exa_enrichment["what_they_do"] = (
        "Vidu AI builds video generation models. Some public sources mention university researchers "
        "and academic labs in the founding story."
    )
    llm = TierLlm(decision("P1", confidence=3))

    result = run(classifier(llm).classify_person(person(email="founder@vidu.com"), target_company))

    assert result is not None
    assert result.tier == "P1"
    assert result.vertical == "video_ai_multimodal"
    assert "academic_institution" not in result.flags
    assert len(llm.calls) == 1


def test_prose_vertical_labels_are_normalized_to_target_verticals() -> None:
    llm = TierLlm(decision("P1", confidence=3, product_fit=["gpu"], primary_product="gpu"))

    result = run(
        classifier(llm).classify_person(
            person(title="Research Scientist"),
            company(
                name="Luma AI",
                domain="lumalabs.ai",
                vertical=(
                    "video_ai_multimodal (Luma is a leading developer of multimodal "
                    "generative AI models, specifically video generation)."
                ),
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.vertical == "video_ai_multimodal"
    assert result.vertical_type == "target"
    assert len(llm.calls) == 1


def test_agentic_generative_ai_prose_vertical_is_target() -> None:
    llm = TierLlm(decision("P1", confidence=3, product_fit=["gpu"], primary_product="gpu"))

    result = run(
        classifier(llm).classify_person(
            person(title="Co-Founder / Chief Multimodal Architect"),
            company(
                name="Elorian AI",
                domain="elorian.ai",
                vertical=(
                    "agentic/generative AI. While the company targets robotics, engineering, "
                    "and manufacturing applications, their core business is multimodal models."
                ),
            ),
        )
    )

    assert result is not None
    assert result.tier == "P1"
    assert result.np_trigger is None
    assert result.vertical == "agentic"
    assert result.vertical_type == "target"
    assert len(llm.calls) == 1


def test_government_customer_mentions_do_not_hard_gate_commercial_company() -> None:
    target_company = company(
        name="DiffuseDrive",
        domain="diffusedrive.com",
        vertical="robotics; video_ai_multimodal. Serves robotics, autonomous vehicles, defense, and aerospace teams.",
        industry="software",
        what_they_do=(
            "DiffuseDrive builds synthetic data software for robotics, autonomous vehicles, "
            "defense, and aerospace customers."
        ),
    )
    llm = TierLlm(decision("P1", confidence=3, product_fit=["gpu"], primary_product="gpu"))

    result = run(classifier(llm).classify_person(person(title="Founding GenAI Engineer"), target_company))

    assert result is not None
    assert result.tier == "P1"
    assert "government_public_sector" not in result.flags
    assert len(llm.calls) == 1


def test_government_entity_flag_is_reviewed_by_llm() -> None:
    llm = TierLlm(
        decision("NP", confidence=3, np_trigger="government_public_sector", product_fit=[], primary_product=None)
    )

    result = run(
        classifier(llm).classify_person(
            person(title="Director"),
            company(
                name="Department of Defense",
                domain="defense.gov",
                vertical="agentic",
                industry="government",
                what_they_do="The Department of Defense is a government agency.",
            ),
        )
    )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "government_public_sector"
    assert "government_public_sector" in result.flags
    assert result.review_status == "NEEDS_REVIEW"
    assert len(llm.calls) == 1


def test_nonprofit_customer_mentions_do_not_make_company_an_npo() -> None:
    target_company = company(
        name="DonorFlow AI",
        domain="donorflow.ai",
        vertical="agentic",
        industry="software",
    )
    target_company.exa_enrichment["what_they_do"] = (
        "DonorFlow AI builds AI workflow software for nonprofit organizations and foundation grantmakers."
    )
    llm = TierLlm(decision("P1", confidence=3))

    result = run(classifier(llm).classify_person(person(email="founder@donorflow.ai"), target_company))

    assert result is not None
    assert result.tier == "P1"
    assert "npo_ngo" not in result.flags
    assert len(llm.calls) == 1


def test_npo_ngo_is_review_flag_unless_compute_heavy_research_nonprofit() -> None:
    ngo = run(
        classifier(
            TierLlm(decision("NP", confidence=3, np_trigger="npo_ngo", product_fit=[], primary_product=None))
        ).classify_person(
            person(email="founder@helpful.org"),
            company(
                name="Helpful NGO",
                domain="helpful.org",
                industry="non-profit",
                vertical="agentic",
            ),
        )
    )
    research_nonprofit = run(
        classifier(TierLlm(decision("P0", confidence=5))).classify_person(
            person(email="researcher@allenai.org"),
            company(
                name="Allen Institute for AI",
                domain="allenai.org",
                industry="nonprofit AI research lab",
                vertical="research_labs",
            ),
        )
    )

    assert ngo is not None
    assert ngo.tier == "NP"
    assert ngo.np_trigger == "npo_ngo"
    assert "npo_ngo" in ngo.flags
    assert ngo.review_status == "NEEDS_REVIEW"

    assert research_nonprofit is not None
    assert "npo_ngo" not in research_nonprofit.flags
    assert research_nonprofit.tier == "P0"


def test_soft_gate_keeps_llm_tier_but_caps_confidence_and_needs_review() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=5))).classify_person(
            person(),
            company(name="Clinical Agent Lab", industry="healthcare biotech", vertical="agentic"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.review_status == "NEEDS_REVIEW"
    assert result.confidence == 3
    assert "healthcare_biotech_pharma" in result.flags


def test_low_resolution_self_reported_luma_p0_is_capped_and_needs_review() -> None:
    llm = TierLlm(decision("P0", confidence=5, key_signal="Lu.ma says the founder is building video inference."))

    result = run(
        classifier(llm).classify_person(
            person(
                email="founder@example.com",
                title=None,
                seniority=None,
                resolution_confidence="low",
                affiliation_state="unverified",
                role_signals=None,
                self_reported_company="Five Tables",
                self_reported_title="Founder",
                building="We are building video model inference for creators.",
            ),
            company(name="Five Tables", domain="fivetables.ai", vertical="video_ai_multimodal"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.confidence == 3
    assert result.review_status == "NEEDS_REVIEW"
    assert llm.calls[0][0]["person"]["self_reported_title"] == "Founder"
    assert llm.calls[0][0]["person"]["self_reported_company"] == "Five Tables"


def test_correlated_luma_p0_can_keep_high_confidence() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=5))).classify_person(
            person(
                title="Founder",
                seniority="founder",
                resolution_confidence="high",
                affiliation_state="verified",
                self_reported_company="Bria AI",
                building="We are scaling production image generation inference.",
            ),
            company(name="Bria AI", domain="bria.ai", vertical="video_ai_multimodal"),
        )
    )

    assert result is not None
    assert result.tier == "P0"
    assert result.confidence == 5
    assert result.review_status == "CLASSIFIED"


def test_low_resolution_non_buyer_luma_signal_does_not_inflate_to_p0() -> None:
    result = run(
        classifier(TierLlm(decision("P0", confidence=5))).classify_person(
            person(
                title=None,
                seniority=None,
                resolution_confidence="low",
                affiliation_state="unverified",
                role_signals=None,
                self_reported_company="Five Tables",
                self_reported_title="Marketing Manager",
                building="We are exploring AI content workflows.",
            ),
            company(name="Five Tables", domain="fivetables.ai", vertical="video_ai_multimodal"),
        )
    )

    assert result is not None
    assert result.tier == "NR"
    assert result.np_trigger == "non_buyer_role"
    assert result.confidence == 3
    assert result.review_status == "NEEDS_REVIEW"


def test_unresolved_event_records_receive_reviewable_nurture_classification_without_llm() -> None:
    cls = classifier(TierLlm(AssertionError("LLM should not be called for ineligible records")))

    results = [
        run(cls.classify_person(person(resolution_confidence="low", building=None), company())),
        run(cls.classify_person(person(resolution_confidence="flagged"), company())),
        run(cls.classify_person(person(resolution_confidence="low"), None)),
    ]

    assert all(result is not None for result in results)
    assert all(result.tier == "NR" for result in results if result is not None)
    assert all(result.np_trigger == "sparse_data" for result in results if result is not None)
    assert all(result.review_status == "NEEDS_REVIEW" for result in results if result is not None)


def test_llm_json_fence_response_is_parsed() -> None:
    llm = TierLlm(
        """```json
        {
          "product_fit": ["maas"],
          "primary_product": "maas",
          "tier": "NP",
          "confidence": 2,
          "np_trigger": "non_buyer_role",
          "key_signal": "Company fit but wrong persona.",
          "key_risk": "Not an infra buyer.",
          "reasoning": "The role signals say this person is not a buyer."
        }
        ```"""
    )

    result = run(classifier(llm).classify_person(person(title="SDR"), company()))

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "non_buyer_role"
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"


def test_llm_http_failure_is_labeled_unavailable_and_logged(caplog) -> None:
    request = httpx.Request("POST", "https://api.gmi-serving.com/v1/messages")
    response = httpx.Response(502, request=request, text="bad gateway from provider")
    error = httpx.HTTPStatusError("bad gateway", request=request, response=response)

    with caplog.at_level(logging.WARNING, logger="mint.backend.classification.classifier"):
        result = run(
            classifier(TierLlm(error)).classify_person(
                person(email="founder@asapi.ai"),
                company(name="Asapi", domain="asapi.ai"),
            )
        )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "llm_unavailable"
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"
    assert result.flags == ["llm_unavailable"]
    assert "HTTPStatusError" in result.reasoning
    assert "founder@asapi.ai" in caplog.text
    assert "Asapi" in caplog.text
    assert "502" in caplog.text


def test_llm_parse_failure_is_labeled_parse_error() -> None:
    result = run(
        classifier(TierLlm("not json")).classify_person(
            person(),
            company(),
        )
    )

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "parse_error"
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"
    assert result.flags == ["parse_error"]
    assert "parse" in result.reasoning.lower()


def test_missing_llm_client_falls_back_to_conservative_np() -> None:
    result = run(classifier().classify_person(person(), company()))

    assert result is not None
    assert result.tier == "NP"
    assert result.np_trigger == "llm_unavailable"
    assert result.confidence == 2
    assert result.review_status == "NEEDS_REVIEW"
