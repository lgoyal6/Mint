"""Run the AgentBox sourcing workflow without the web app or database."""

import argparse
import asyncio
from pathlib import Path

from mint.backend.classification.classifier import load_icp_config
from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.exa import ExaClient
from mint.backend.exports.leads import AgentBoxExportData, write_agentbox_export
from mint.backend.settings import Settings
from mint.backend.source.agentbox import AgentBoxSourceService, export_row


async def _run(out_path: Path, campaign: str | None = None) -> None:
    settings = Settings()
    apollo = ApolloClient(settings.apollo_api_key, base_url=settings.apollo_base_url)
    exa = ExaClient(settings.exa_api_key, base_url=settings.exa_base_url)
    try:
        run = await AgentBoxSourceService(load_icp_config(settings.icp_config_path), apollo, exa).run(
            campaign_name=campaign,
            include_workbook=False,
        )
        rows = [
            export_row(lead)
            for lead in run.leads
            if lead.band in {"pursue", "nurture"} and lead.linkedin_url
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(write_agentbox_export(AgentBoxExportData(rows=rows)))
        print({"leads_scored": len(run.leads), "linkedin_leads_exported": len(rows), "bands": run.bands, "needs_review": run.needs_review_count, "out_path": str(out_path), "mode": run.mode, "campaign": run.campaign})
    finally:
        await apollo.close()
        await exa.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Source and score AgentBox ICP leads.")
    parser.add_argument("--out", default="agentbox-leads.xlsx", help="Output XLSX path")
    parser.add_argument("--campaign", default="product_runtime_us", help="AgentBox campaign key")
    args = parser.parse_args()
    asyncio.run(_run(Path(args.out), args.campaign))


if __name__ == "__main__":
    main()
