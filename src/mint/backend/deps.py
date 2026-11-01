from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from mint.backend.agent.client import GMIAgentClient
from mint.backend.agent.loop import AgentLoop
from mint.backend.agent.prompts import AGENT_SYSTEM_PROMPT
from mint.backend.agent.tools import MintAgentTools
from mint.backend.classification.classifier import Classifier, load_icp_config
from mint.backend.classification.gmi import GMIClassificationClient
from mint.backend.classification.service import ClassificationService
from mint.backend.db.chat_storage import ChatStorage
from mint.backend.db.classification_storage import ClassificationStorage
from mint.backend.db.company_aliases import CompanyAliasStore
from mint.backend.db.event_storage import EventStorage
from mint.backend.db.job_store import JobStore
from mint.backend.db.read_storage import ReadStorage
from mint.backend.db.storage import EnrichmentStorage
from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.company_resolver import CompanyResolver
from mint.backend.enrichment.exa import ExaClient
from mint.backend.enrichment.service import EnrichmentService
from mint.backend.exports.leads import LeadExportService
from mint.backend.integrations.hubspot import HubSpotClient
from mint.backend.integrations.clay import ClayClient
from mint.backend.ingestion.luma import LumaClient
from mint.backend.ingestion.orchestrator import IngestionOrchestrator
from mint.backend.ingestion.poller import LumaSyncService
from mint.backend.jobs import JobManager
from mint.backend.review.auto_reviewer import AutoReviewService
from mint.backend.settings import Settings


@dataclass(slots=True)
class AppContext:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    apollo: ApolloClient
    exa: ExaClient
    resolver: CompanyResolver
    enrichment_service: EnrichmentService
    enrichment_storage: EnrichmentStorage
    event_storage: EventStorage
    classification_storage: ClassificationStorage
    read_storage: ReadStorage
    classifier: Classifier
    classification_service: ClassificationService
    luma_client: LumaClient
    orchestrator: IngestionOrchestrator
    luma_sync_service: LumaSyncService
    job_manager: JobManager
    job_store: JobStore | None = None
    chat_storage: ChatStorage | None = None
    lead_export_service: LeadExportService | None = None
    auto_review_service: AutoReviewService | None = None
    gmi_client: GMIClassificationClient | None = None
    gmi_agent_client: GMIAgentClient | None = None
    agent_tools: MintAgentTools | None = None
    agent_loop: AgentLoop | None = None
    hubspot_client: HubSpotClient | None = None
    clay_client: ClayClient | None = None


async def build_context(settings: Settings) -> AppContext:
    database_url = _required(settings.database_url, "DATABASE_URL")
    luma_api_key = _required(settings.luma_api_key, "LUMA_API_KEY")

    engine = create_async_engine(database_url, pool_size=10, max_overflow=20)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    apollo = ApolloClient(
        settings.apollo_api_key,
        base_url=settings.apollo_base_url,
        credit_cost_usd=settings.apollo_credit_cost_usd,
    )
    exa = ExaClient(
        settings.exa_api_key,
        base_url=settings.exa_base_url,
        rate_limit_per_second=settings.exa_rate_limit_per_second,
        rate_limit_burst=settings.exa_rate_limit_burst,
    )
    company_aliases = CompanyAliasStore(session_factory)
    resolver = CompanyResolver(exa, alias_store=company_aliases)

    enrichment_storage = EnrichmentStorage(session_factory)
    enrichment_service = EnrichmentService(
        apollo,
        exa,
        resolver,
        storage=enrichment_storage,
    )
    event_storage = EventStorage(session_factory)
    classification_storage = ClassificationStorage(session_factory)
    read_storage = ReadStorage(session_factory, alias_store=company_aliases)

    gmi_client = (
        GMIClassificationClient(
            settings.gmi_api_key,
            base_url=settings.gmi_base_url,
            api_format=settings.gmi_api_format,
            model=settings.gmi_classifier_model,
        )
        if settings.gmi_api_key
        else None
    )
    classifier = Classifier(
        load_icp_config(settings.icp_config_path),
        llm_client=gmi_client,
        model=settings.gmi_classifier_model,
        competitor_terms=_load_terms(settings.competitor_terms_path),
        bigtech_terms=_load_terms(settings.bigtech_terms_path),
    )
    classification_service = ClassificationService(classification_storage, classifier)

    auto_review_service = (
        AutoReviewService(
            classification_storage,
            read_storage,
            gmi_client,
            load_icp_config(settings.icp_config_path),
            model=settings.gmi_reviewer_model,
            enrichment_service=enrichment_service,
            classification_service=classification_service,
        )
        if gmi_client
        else None
    )

    luma_client = LumaClient(luma_api_key)
    hubspot_client = (
        HubSpotClient(settings.hubspot_access_token, base_url=settings.hubspot_base_url)
        if settings.hubspot_access_token
        else None
    )
    clay_client = ClayClient(settings.clay_webhook_url) if settings.clay_webhook_url else None
    orchestrator = IngestionOrchestrator(
        luma_client,
        enrichment_service,
        enrichment_storage,
        event_storage,
    )
    luma_sync_service = LumaSyncService(
        luma_client,
        event_storage,
        orchestrator,
        classification_service,
    )
    job_store = JobStore(session_factory)
    job_manager = JobManager(store=job_store)
    chat_storage = ChatStorage(session_factory)
    lead_export_service = LeadExportService(Path(tempfile.gettempdir()) / "mint-exports")
    try:
        await job_store.fail_running("The backend restarted while this job was running. Start it again.")
    except Exception:
        logging.getLogger(__name__).warning("Could not mark interrupted jobs as failed", exc_info=True)
    gmi_agent_client = (
        GMIAgentClient(
            settings.gmi_api_key,
            base_url=settings.gmi_base_url,
            api_format=settings.gmi_api_format,
            model=settings.gmi_agent_model,
        )
        if settings.gmi_api_key
        else None
    )

    ctx = AppContext(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        apollo=apollo,
        exa=exa,
        resolver=resolver,
        enrichment_service=enrichment_service,
        enrichment_storage=enrichment_storage,
        event_storage=event_storage,
        classification_storage=classification_storage,
        read_storage=read_storage,
        classifier=classifier,
        classification_service=classification_service,
        luma_client=luma_client,
        orchestrator=orchestrator,
        luma_sync_service=luma_sync_service,
        job_manager=job_manager,
        job_store=job_store,
        chat_storage=chat_storage,
        lead_export_service=lead_export_service,
        auto_review_service=auto_review_service,
        gmi_client=gmi_client,
        gmi_agent_client=gmi_agent_client,
        agent_loop=None,
        hubspot_client=hubspot_client,
        clay_client=clay_client,
    )
    agent_tools = MintAgentTools(ctx)
    ctx.agent_tools = agent_tools
    ctx.agent_loop = (
        AgentLoop(
            gmi_agent_client,
            agent_tools,
            system_prompt=AGENT_SYSTEM_PROMPT,
        )
        if gmi_agent_client is not None
        else None
    )
    return ctx


async def close_context(ctx: Any) -> None:
    enrichment_service = getattr(ctx, "enrichment_service", None)
    if enrichment_service is not None and hasattr(enrichment_service, "close"):
        await enrichment_service.close()

    luma_client = getattr(ctx, "luma_client", None)
    if luma_client is not None and hasattr(luma_client, "close"):
        await luma_client.close()

    gmi_client = getattr(ctx, "gmi_client", None)
    if gmi_client is not None and hasattr(gmi_client, "close"):
        await gmi_client.close()

    gmi_agent_client = getattr(ctx, "gmi_agent_client", None)
    if gmi_agent_client is not None and hasattr(gmi_agent_client, "close"):
        await gmi_agent_client.close()

    hubspot_client = getattr(ctx, "hubspot_client", None)
    if hubspot_client is not None and hasattr(hubspot_client, "close"):
        await hubspot_client.close()

    clay_client = getattr(ctx, "clay_client", None)
    if clay_client is not None and hasattr(clay_client, "close"):
        await clay_client.close()

    engine = getattr(ctx, "engine", None)
    if engine is not None and hasattr(engine, "dispose"):
        await engine.dispose()


def _required(value: str | None, env_name: str) -> str:
    if not value:
        raise RuntimeError(f"{env_name} is required to start mint")
    return value


def _load_terms(path: str | Path) -> set[str]:
    try:
        return {
            line.strip().lower()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    except FileNotFoundError:
        return set()
