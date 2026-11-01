import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.company_resolver import CompanyResolver
from mint.backend.enrichment.exa import ExaClient
from mint.backend.enrichment.service import EnrichmentService
from mint.backend.ingestion.csv_loader import load_csv
from mint.backend.settings import Settings


CSV_PATH = Path(
    "/Users/pynay/Downloads/Hard Problems Night for Agent Builders - Guests - checked_in - 2026-05-27-06-11-54.csv"
)
OUTPUT_DIR = Path("outputs/hard_problems")


def text(value, limit=900):
    if value in (None, "", [], {}):
        return ""
    value = str(value).replace("\r", " ").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def lead_name(lead):
    return " ".join(part for part in [lead.first_name, lead.last_name] if part) or lead.email


def resolution_state(resolution, key):
    evidence = resolution.evidence or {}
    return evidence.get(key) if isinstance(evidence, dict) else None


def resolution_chain(resolution):
    evidence = resolution.evidence or {}
    if isinstance(evidence, dict):
        return evidence.get("chain") or []
    return evidence


def summarize(results):
    return {
        "lead_count": len(results),
        "enriched_count": sum(1 for r in results if r.enriched),
        "skipped_by_gate_count": sum(1 for r in results if not r.enriched),
        "apollo_person_matched": sum(1 for r in results if r.person and r.person.matched),
        "resolved_domains": sum(1 for r in results if r.resolution.domain),
        "company_apollo_count": sum(1 for r in results if r.company_apollo),
        "company_exa_count": sum(1 for r in results if r.company_exa),
        "people_search_count": sum(1 for r in results if r.people_search),
        "total_errors": sum(len(r.errors) for r in results),
        "confidence_counts": dict(Counter(r.resolution.confidence for r in results)),
        "affiliation_counts": dict(Counter(r.resolution.affiliation_state for r in results)),
        "company_state_counts": dict(Counter(resolution_state(r.resolution, "company_state") for r in results)),
        "company_source_counts": dict(Counter(r.resolution.company_source for r in results)),
        "data_quality_counts": dict(Counter(r.data_quality for r in results)),
        "source_counts": dict(Counter(source for r in results for source in r.enrichment_sources)),
    }


def classifier_signal_summary(result):
    if not result.enriched or not result.company_exa:
        return "No company intelligence - did not pass resolution gate"
    exa = result.company_exa
    return (
        f"vertical={text(exa.target_vertical, 180)}; "
        f"stage={text(exa.stage, 180)}; "
        f"compute/spend={text(exa.spend_signals or exa.inference_signals, 260)}; "
        f"person={text(exa.person_role_signals, 220)}"
    )


def grouped_index(leads, results):
    groups = defaultdict(list)
    for idx, (lead, result) in enumerate(zip(leads, results), start=1):
        groups[result.resolution.confidence].append((idx, lead, result))
    lines = ["## Review Index", ""]
    for confidence in ["high", "medium", "low", "flagged"]:
        items = groups.get(confidence, [])
        if not items:
            continue
        lines.append(f"### {confidence.upper()} ({len(items)})")
        for idx, lead, result in items:
            res = result.resolution
            lines.append(
                f"- {idx}. {lead_name(lead)} - `{lead.company_name or ''}` -> "
                f"`{res.company_name or ''}` `{res.domain or ''}`; "
                f"affiliation `{res.affiliation_state}`; enriched `{result.enriched}`; data `{result.data_quality}`"
            )
        lines.append("")
    return lines


def make_markdown(leads, results, summary):
    lines = [
        "# Hard Problems Agent Builders Enrichment Report",
        "",
        f"- CSV: `{CSV_PATH}`",
        f"- Generated UTC: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",
        f"- Leads: `{summary['lead_count']}`",
        f"- Enriched past gate: `{summary['enriched_count']}`",
        f"- Skipped by gate: `{summary['skipped_by_gate_count']}`",
        f"- Apollo person matched: `{summary['apollo_person_matched']}`",
        f"- People-search confirmations/searches surfaced: `{summary['people_search_count']}`",
        f"- Errors: `{summary['total_errors']}`",
        "",
        "## Summary Counts",
        "",
        f"- Confidence: `{summary['confidence_counts']}`",
        f"- Affiliation: `{summary['affiliation_counts']}`",
        f"- Company state: `{summary['company_state_counts']}`",
        f"- Company source: `{summary['company_source_counts']}`",
        f"- Data quality: `{summary['data_quality_counts']}`",
        f"- Sources: `{summary['source_counts']}`",
        "",
    ]
    lines.extend(grouped_index(leads, results))
    lines.extend(["## Full Records", ""])
    for idx, (lead, result) in enumerate(zip(leads, results), start=1):
        res = result.resolution
        lines.extend(
            [
                f"### {idx}. {lead_name(lead)}",
                "",
                f"- Email: `{lead.email}`",
                f"- Raw company claim: `{lead.company_name or ''}`",
                f"- Building/context: {text(lead.what_they_are_building, 350)}",
                (
                    f"- Resolution: `{res.confidence}` confidence, `{res.affiliation_state}` affiliation, "
                    f"person `{resolution_state(res, 'person_state') or ''}` via `{res.person_source}`, "
                    f"company `{resolution_state(res, 'company_state') or ''}` "
                    f"via `{res.company_source}`"
                ),
                f"- Resolved company: `{res.company_name or ''}` `{res.domain or ''}`",
                f"- Enriched: `{result.enriched}`; data_quality: `{result.data_quality}`; sources: `{result.enrichment_sources}`",
                f"- Classifier input signals: {classifier_signal_summary(result)}",
            ]
        )
        if res.candidate_companies:
            candidates = ", ".join(
                f"{c.name or ''} {c.domain or ''} via {c.source}".strip() for c in res.candidate_companies
            )
            lines.append(f"- Candidate companies: {candidates}")
        if result.errors:
            lines.append(f"- Errors: `{result.errors}`")
        lines.append("- Evidence:")
        for item in resolution_chain(res):
            lines.append(f"  - {item}")
        if result.company_apollo:
            apollo = result.company_apollo
            lines.extend(
                [
                    "- Apollo org:",
                    (
                        f"  - Name: `{apollo.name or ''}`; domain: `{apollo.domain or ''}`; "
                        f"employees: `{apollo.employee_count or ''}`; industry: `{apollo.industry or ''}`"
                    ),
                    (
                        f"  - Funding: `{apollo.funding_stage or ''}` `{apollo.total_funding or ''}`; "
                        f"revenue: `{apollo.revenue or ''}`"
                    ),
                ]
            )
        if result.company_exa:
            exa = result.company_exa
            lines.extend(
                [
                    "- Exa company intelligence:",
                    f"  - What they do: {text(exa.what_they_do)}",
                    f"  - Target vertical: `{exa.target_vertical}`; stage: `{exa.stage}`",
                    f"  - Funding/revenue: {text(exa.funding_signals)}",
                    f"  - Compute profile: {text(exa.inference_signals)}",
                    f"  - Team/activity: {text('; '.join(exa.hiring_signals + exa.recent_activity), 900)}",
                    f"  - Person role: {text(exa.person_role_signals)}",
                ]
            )
        if result.people_search:
            lines.append("- People search used during resolve:")
            for item in result.people_search[:5]:
                lines.append(f"  - {text(item.get('title'), 240)} `{item.get('url', '')}`")
        lines.append("")
    return "\n".join(lines)


async def main():
    settings = Settings()
    if not settings.apollo_api_key:
        raise SystemExit("APOLLO_API_KEY missing")
    if not settings.exa_api_key:
        raise SystemExit("EXA_API_KEY missing")

    leads = load_csv(str(CSV_PATH))
    print(f"loaded_leads={len(leads)}", flush=True)
    apollo = ApolloClient(api_key=settings.apollo_api_key, base_url=settings.apollo_base_url)
    exa = ExaClient(
        api_key=settings.exa_api_key,
        base_url=settings.exa_base_url,
        rate_limit_per_second=settings.exa_rate_limit_per_second,
        rate_limit_burst=settings.exa_rate_limit_burst,
    )
    resolver = CompanyResolver(exa=exa)
    service = EnrichmentService(apollo=apollo, exa=exa, resolver=resolver)
    try:
        results = await service.enrich_batch(leads, max_concurrent=5)
    finally:
        await service.close()

    summary = summarize(results)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = OUTPUT_DIR / f"hard_problems_checked_in_enrichment_readable_{timestamp}.json"
    md_path = OUTPUT_DIR / f"hard_problems_checked_in_enrichment_readable_{timestamp}.md"
    payload = {
        "csv_path": str(CSV_PATH),
        "run_at": timestamp,
        "summary": summary,
        "records": [
            {"input": lead.model_dump(mode="json"), "result": result.model_dump(mode="json")}
            for lead, result in zip(leads, results)
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(make_markdown(leads, results, summary), encoding="utf-8")
    print(json.dumps({"summary": summary, "json_path": str(json_path), "markdown_path": str(md_path)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
