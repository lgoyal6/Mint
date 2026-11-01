from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mint.backend.db.base import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)

    apollo_org_data: Mapped[dict | None] = mapped_column(JSONB)
    apollo_org_industry: Mapped[str | None] = mapped_column(Text)
    apollo_org_employee_count: Mapped[int | None] = mapped_column(Integer)
    apollo_org_founded_year: Mapped[int | None] = mapped_column(Integer)
    apollo_org_funding_stage: Mapped[str | None] = mapped_column(Text)
    apollo_org_total_funding: Mapped[str | None] = mapped_column(Text)
    apollo_org_revenue: Mapped[str | None] = mapped_column(Text)
    apollo_org_technologies: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    apollo_org_headcount_growth_6mo: Mapped[Decimal | None] = mapped_column(Numeric)
    apollo_org_headcount_growth_12mo: Mapped[Decimal | None] = mapped_column(Numeric)

    exa_enrichment: Mapped[dict | None] = mapped_column(JSONB)
    exa_what_they_do: Mapped[str | None] = mapped_column(Text)
    exa_target_vertical: Mapped[str | None] = mapped_column(Text)
    exa_stage: Mapped[str | None] = mapped_column(Text)
    exa_data_quality: Mapped[str | None] = mapped_column(Text)

    exa_deep_raw: Mapped[dict | None] = mapped_column(JSONB)
    exa_grounding: Mapped[list[dict] | None] = mapped_column(JSONB)

    apollo_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exa_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CompanyDomainAlias(Base):
    """A human-confirmed alternate domain for a canonical company."""

    __tablename__ = "company_domain_aliases"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_domain: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'human_review'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Person(Base):
    __tablename__ = "people"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("companies.id"))

    affiliation_state: Mapped[str | None] = mapped_column(Text)
    resolution_confidence: Mapped[str | None] = mapped_column(Text)
    resolution_evidence: Mapped[dict | None] = mapped_column(JSONB)
    candidate_companies: Mapped[list[dict] | None] = mapped_column(JSONB)
    review_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_resolve_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_resolve_note: Mapped[str | None] = mapped_column(Text)

    apollo_person_data: Mapped[dict | None] = mapped_column(JSONB)
    apollo_match_status: Mapped[str | None] = mapped_column(Text)
    apollo_person_title: Mapped[str | None] = mapped_column(Text)
    apollo_person_seniority: Mapped[str | None] = mapped_column(Text)
    apollo_person_departments: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    apollo_person_linkedin_url: Mapped[str | None] = mapped_column(Text)
    apollo_person_location: Mapped[str | None] = mapped_column(Text)

    exa_person_role_signals: Mapped[str | None] = mapped_column(Text)
    exa_people_search_results: Mapped[list[dict] | None] = mapped_column(JSONB)
    clay_person_data: Mapped[dict | None] = mapped_column(JSONB)
    clay_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    self_reported_title: Mapped[str | None] = mapped_column(Text)
    self_reported_company: Mapped[str | None] = mapped_column(Text)
    self_reported_building: Mapped[str | None] = mapped_column(Text)

    apollo_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exa_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_data_quality: Mapped[str | None] = mapped_column(Text)

    current_tier: Mapped[str | None] = mapped_column(Text)
    current_confidence: Mapped[int | None] = mapped_column(Integer)
    current_classification_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    events_registered: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    events_attended: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EnrichmentLog(Base):
    __tablename__ = "enrichment_logs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"))
    operation: Mapped[str | None] = mapped_column(Text)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    credits: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default=text("0"))
    pricing_basis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    progress: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    luma_event_id: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    guest_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    enrichment_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_registrations_event_email"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    person_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("people.id"))
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    approval_status: Mapped[str | None] = mapped_column(Text)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    survey_responses: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventRating(Base):
    __tablename__ = "event_ratings"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    notes: Mapped[str | None] = mapped_column(Text)
    rated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("thread_id", "position", name="uq_chat_messages_thread_position"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_trace: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    person_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("people.id"))
    company_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("companies.id"))

    tier: Mapped[str] = mapped_column(Text, nullable=False)
    np_trigger: Mapped[str | None] = mapped_column(Text)
    product_fit: Mapped[list | None] = mapped_column(JSONB)
    primary_product: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(Integer)
    reasoning: Mapped[str | None] = mapped_column(Text)
    key_signal: Mapped[str | None] = mapped_column(Text)
    key_risk: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    review_status: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reclassified_to: Mapped[str | None] = mapped_column(Text)
    auto_review_status: Mapped[str | None] = mapped_column(Text)
    auto_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_review_note: Mapped[str | None] = mapped_column(Text)

    pipeline_version: Mapped[str | None] = mapped_column(Text)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("ix_people_email", Person.email)
Index("ix_companies_domain", Company.domain)
Index("ix_people_company_id", Person.company_id)
Index("ix_enrichment_logs_entity_id", EnrichmentLog.entity_id)
Index("ix_enrichment_logs_event_id", EnrichmentLog.event_id)
Index("ix_registrations_person_id", Registration.person_id)
Index("ix_registrations_event_id", Registration.event_id)
Index("ix_registrations_email", Registration.email)
Index("ix_classifications_person_id", Classification.person_id)
Index("ix_classifications_company_id", Classification.company_id)
Index("ix_classifications_review_status", Classification.review_status)
Index("ix_people_current_classification_id", Person.current_classification_id)
Index("ix_jobs_created_at", Job.created_at)
Index("ix_chat_messages_thread_id", ChatMessage.thread_id)
