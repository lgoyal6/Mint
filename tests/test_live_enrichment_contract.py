import asyncio
import json
import os
from pathlib import Path

import pytest

from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.company_resolver import CompanyResolver
from mint.backend.enrichment.exa import ExaClient
from mint.backend.enrichment.service import EnrichmentService
from mint.backend.models import EnrichmentInput


def test_live_enrichment_batch_contract_against_real_apis() -> None:
    if os.getenv("RUN_LIVE_ENRICHMENT_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_ENRICHMENT_TESTS=1 with APOLLO_API_KEY and EXA_API_KEY to run live API tests.")
    if not os.getenv("APOLLO_API_KEY") or not os.getenv("EXA_API_KEY"):
        pytest.skip("APOLLO_API_KEY and EXA_API_KEY are required for live enrichment tests.")

    async def scenario() -> None:
        apollo = ApolloClient(api_key=os.environ["APOLLO_API_KEY"])
        exa = ExaClient(api_key=os.environ["EXA_API_KEY"])
        resolver = CompanyResolver(exa=exa)
        service = EnrichmentService(apollo=apollo, exa=exa, resolver=resolver)
        try:
            fixture_path = Path(__file__).parent / "fixtures" / "enrichment_leads.json"
            leads = [EnrichmentInput(**row, source="fixture") for row in json.loads(fixture_path.read_text())]
            results = await service.enrich_batch(leads, max_concurrent=3)
        finally:
            await service.close()

        assert len(results) == len(leads)
        assert any(result.person and result.person.matched for result in results)
        assert any(result.resolution.company_source == "email_domain" for result in results)
        assert any(result.resolution.confidence == "low" for result in results)
        assert all(result.errors is not None for result in results)
        for result in results:
            if result.company_exa is not None:
                assert result.company_exa.what_they_do
                assert result.company_exa.target_vertical
                assert result.company_exa.stage
                assert result.company_exa.revenue_signals is not None
                assert result.company_exa.funding_signals is not None
                assert result.company_exa.inference_signals is not None
                assert result.company_exa.spend_signals is not None
                assert result.company_exa.person_role_signals is not None
                assert result.company_exa.data_quality in {"rich", "moderate", "sparse"}

    asyncio.run(scenario())
