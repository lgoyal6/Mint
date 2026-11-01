from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DataQuality = Literal["rich", "moderate", "sparse"]
AffiliationState = Literal["verified", "inferred", "unverified", "contradicted"]
ResolutionConfidence = Literal["high", "medium", "low", "flagged"]
PersonSource = Literal["apollo", "exa_people", "linkedin_url", "self_reported"]
CompanySource = Literal[
    "email_domain",
    "apollo_org",
    "self_reported_website",
    "exa_company_search",
    "exa_people_search",
    "none",
]
ClassificationTier = Literal["P0", "P1", "P2", "NR", "NP"]
NpTrigger = Literal[
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
]
ReviewStatus = Literal["CLASSIFIED", "NEEDS_REVIEW"]
ProductFit = Literal["maas", "dedicated", "gpu"]
FundingStage = Literal["pre_seed", "seed", "series_a", "series_b", "series_c_plus", "public", "bootstrapped", "unknown"]
class EnrichmentInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    website_url: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    what_they_are_building: str | None = None
    source: str | None = None
    event_id: str | None = None
    survey_responses: dict[str, Any] | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("email is required")
        return value.strip().lower()

    @field_validator(
        "first_name",
        "last_name",
        "company_name",
        "website_url",
        "title",
        "linkedin_url",
        "what_they_are_building",
        "source",
        "event_id",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class ApolloPersonResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    matched: bool = False
    match_status: Literal["matched", "partial", "not_found"] = "not_found"
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = None
    title: str | None = None
    seniority: str | None = None
    departments: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None
    location: str | None = None
    organization_name: str | None = None
    organization_domain: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sync_match_status(self) -> "ApolloPersonResult":
        if self.matched and self.match_status == "not_found":
            self.match_status = "matched"
        return self


class ApolloOrgResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: str | None = None
    name: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    founded_year: int | None = None
    funding_stage: str | None = None
    latest_funding_round_date: str | None = None
    total_funding: str | None = None
    revenue: str | None = None
    technologies: list[str] = Field(default_factory=list)
    headcount_growth_6mo: float | None = None
    headcount_growth_12mo: float | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class ExaEnrichmentResult(BaseModel):
    """Legacy-expanded Exa payload used as the persisted classifier context.

    Exa runs with a compressed 8-property outputSchema because of the 10-property cap;
    the Exa client expands that response into these legacy fields before storage.
    The classifier reads these keys from the persisted exa_enrichment JSON.
    """

    model_config = ConfigDict(extra="ignore")

    what_they_do: str
    target_vertical: str
    business_model: str | None = None
    stage: str
    revenue_signals: str
    customer_signals: str = ""
    funding_signals: str
    inference_signals: str
    spend_signals: str
    competitors_or_current_infra: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    hiring_signals: list[str] = Field(default_factory=list)
    recent_activity: list[str] = Field(default_factory=list)
    person_role_signals: str
    data_quality: DataQuality
    exa_grounding: list[dict[str, Any]] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class CandidateCompany(BaseModel):
    model_config = ConfigDict(extra="ignore")

    domain: str | None = None
    name: str | None = None
    source: CompanySource
    note: str | None = None


class ResolvedIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_source: PersonSource
    name: str | None = None
    title: str | None = None
    seniority: str | None = None

    company_source: CompanySource
    domain: str | None = None
    company_name: str | None = None

    affiliation_state: AffiliationState
    confidence: ResolutionConfidence

    evidence: dict[str, Any] = Field(default_factory=dict)
    candidate_companies: list[CandidateCompany] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: ResolvedIdentity
    person: ApolloPersonResult | None = None
    company_apollo: ApolloOrgResult | None = None
    company_exa: ExaEnrichmentResult | None = None
    people_search: list[dict[str, Any]] | None = None
    data_quality: DataQuality
    enriched: bool = False
    enrichment_sources: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EventInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    luma_event_id: str | None = None
    name: str
    event_date: datetime | None = None
    location: str | None = None
    url: str | None = None
    status: str
    guest_count: int = 0
    enrichment_enabled: bool = False

    @field_validator("luma_event_id", "name", "location", "url", "status", mode="before")
    @classmethod
    def normalize_event_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    approval_status: str
    checked_in_at: datetime | None = None
    survey_responses: dict[str, Any] | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_registration_email(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("email is required")
        return value.strip().lower()

    @field_validator("approval_status", mode="before")
    @classmethod
    def normalize_approval_status(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text or "going"


class LeadError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    errors: list[str] = Field(default_factory=list)


class IngestSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID | None = None
    total_guests: int = 0
    going_count: int = 0
    enriched_count: int = 0
    skipped_fresh_count: int = 0
    already_known_count: int = 0
    flagged_count: int = 0
    errors: list[str] = Field(default_factory=list)
    lead_errors: list[LeadError] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: ClassificationTier
    np_trigger: NpTrigger | None = None
    product_fit: list[ProductFit] = Field(default_factory=list)
    primary_product: ProductFit | None = None
    confidence: int = Field(ge=1, le=5)
    reasoning: str
    key_signal: str
    key_risk: str
    flags: list[str] = Field(default_factory=list)
    review_status: ReviewStatus
    pipeline_version: str = "mint-classifier-productfit-v1"

    vertical: str
    vertical_type: Literal["target", "adjacent", "not_target", "no_match"] = "not_target"
    funding_stage: FundingStage
    employee_count: int | None = None
    has_revenue: bool
    is_pre_revenue: bool

    @model_validator(mode="after")
    def require_np_trigger_for_np(self) -> "ClassificationResult":
        if self.tier == "NP" and self.np_trigger is None:
            raise ValueError("NP classifications require np_trigger")
        if self.tier not in {"NP", "NR"} and self.np_trigger is not None:
            raise ValueError("np_trigger is only valid for NP/NR classifications")
        return self


class ClassifyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_count: int = 0
    classified_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class SyncSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events_discovered: int = 0
    events_new: int = 0
    events_processed: int = 0
    events_skipped_stale: int = 0
    total_enriched: int = 0
    total_skipped_fresh: int = 0
    total_classified: int = 0
    errors: list[str] = Field(default_factory=list)
