import json
import re
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

import yaml
from pypdf import PdfReader

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from mint.backend.classification.classifier import load_icp_config
from mint.backend.exports.leads import AgentBoxExportData, AgentBoxExportRow
from mint.backend.source.agentbox import AgentBoxSourceService, export_row


router = APIRouter()


class AgentBoxSourceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manual_flags: dict[str, dict[str, Any]] = Field(default_factory=dict)
    campaign: str | None = None
    limit: int | None = Field(default=None, ge=1, le=600)

class ICPSourceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    icp_name: str = Field(default="Uploaded ICP", min_length=1, max_length=120)
    icp_content: str = Field(min_length=2, max_length=100_000)
    limit: int | None = Field(default=None, ge=1, le=600)


class AgentBoxExportRequest(BaseModel):
    rows: list[AgentBoxExportRow] = Field(min_length=1)


@router.post("/source/icp/parse")
async def parse_icp_file(file: UploadFile = File(...)) -> dict[str, str]:
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="ICP document must be 5 MB or smaller.")
    filename = file.filename or "Uploaded ICP"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if suffix == ".pdf":
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        elif suffix == ".docx":
            with zipfile.ZipFile(BytesIO(content)) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            text = "\n".join(
                "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
                for paragraph in root.iter()
                if paragraph.tag.endswith("}p")
            )
        elif suffix in {".json", ".yaml", ".yml", ".txt", ".md"}:
            text = content.decode("utf-8")
        else:
            raise ValueError("Upload a PDF, DOCX, JSON, YAML, TXT, or Markdown ICP.")
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not read ICP document: {exc}") from exc
    text = text.strip()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="The ICP document did not contain readable text.")
    return {"name": filename.rsplit(".", 1)[0], "content": text[:100_000]}


@router.post("/source/agentbox", status_code=status.HTTP_202_ACCEPTED)
async def source_agentbox(payload: AgentBoxSourceRequest, request: Request) -> dict[str, Any]:
    ctx = request.app.state.ctx
    job_manager = getattr(ctx, "job_manager", None)
    if job_manager is None:
        raise HTTPException(status_code=503, detail="job manager is not configured")

    job = job_manager.start(
        "source_agentbox",
        lambda progress: _source_agentbox(ctx, payload, progress),
    )
    return {"job_id": str(job.id), "status": job.status}

@router.post("/source/icp", status_code=status.HTTP_202_ACCEPTED)
async def source_icp(payload: ICPSourceRequest, request: Request) -> dict[str, Any]:
    ctx = request.app.state.ctx
    job_manager = getattr(ctx, "job_manager", None)
    if job_manager is None:
        raise HTTPException(status_code=503, detail="job manager is not configured")
    try:
        config = _uploaded_icp_config(payload.icp_name, payload.icp_content)
    except (TypeError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = job_manager.start(
        "source_icp",
        lambda progress: _source_icp(ctx, config, payload.limit, progress),
    )
    return {"job_id": str(job.id), "status": job.status}


@router.post("/source/agentbox/export")
async def export_agentbox_shortlist(payload: AgentBoxExportRequest, request: Request) -> dict[str, Any]:
    """Create a workbook only after the user has selected a shortlist."""
    artifact = request.app.state.ctx.lead_export_service.create_agentbox_artifact(
        AgentBoxExportData(rows=payload.rows)
    )
    return artifact.model_dump()

@router.post("/source/icp/export")
async def export_icp_shortlist(payload: AgentBoxExportRequest, request: Request) -> dict[str, Any]:
    return await export_agentbox_shortlist(payload, request)


async def _source_agentbox(ctx, payload: AgentBoxSourceRequest, progress) -> dict[str, Any]:
    await progress.update(stage="sourcing", done=0, total=1, message="Searching Apollo and checking runtime fit")
    run = await AgentBoxSourceService(
        load_icp_config(ctx.settings.icp_config_path),
        ctx.apollo,
        ctx.exa,
    ).run(
        payload.manual_flags,
        payload.campaign,
        include_workbook=False,
        max_results=payload.limit,
    )
    candidates = [
        export_row(lead).model_dump()
        for lead in run.leads
        if lead.band in {"pursue", "nurture", "partner_track"}
    ]
    rejected = [
        export_row(lead).model_dump()
        for lead in run.leads
        if lead.band not in {"pursue", "nurture", "partner_track"}
    ]
    result = {
        "leads": len(run.leads),
        "bands": run.bands,
        "needs_review": run.needs_review_count,
        "candidates": candidates,
        "rejected": rejected,
        "mode": run.mode,
        "campaign": run.campaign,
    }
    await progress.update(stage="ready", done=1, total=1, message="Review candidates and export a shortlist")
    return result


async def _source_icp(ctx, config: dict[str, Any], limit: int | None, progress) -> dict[str, Any]:
    await progress.update(stage="sourcing", done=0, total=1, message="Searching Apollo and validating ICP fit")
    run = await AgentBoxSourceService(config, ctx.apollo, ctx.exa).run(
        campaign_name="uploaded_icp",
        include_workbook=False,
        max_results=limit,
    )
    candidates = [export_row(lead).model_dump() for lead in run.leads if lead.band in {"pursue", "nurture", "partner_track"}]
    rejected = [export_row(lead).model_dump() for lead in run.leads if lead.band not in {"pursue", "nurture", "partner_track"}]
    result = {
        "leads": len(run.leads), "bands": run.bands, "needs_review": run.needs_review_count,
        "candidates": candidates, "rejected": rejected, "mode": run.mode,
        "campaign": run.campaign, "icp_name": config["agentbox_campaign"]["label"],
    }
    await progress.update(stage="ready", done=1, total=1, message="Review candidates and export your shortlist")
    return result


def _uploaded_icp_config(name: str, content: str) -> dict[str, Any]:
    """Normalize a portable JSON/YAML ICP into the existing sourcing engine."""
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raw = yaml.safe_load(content)
    if isinstance(raw, str):
        raw = _structured_icp_from_text(raw)
    if not isinstance(raw, dict):
        raise ValueError("ICP file must contain a JSON or YAML object.")

    def values(*keys: str) -> list[str]:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str):
                return [part.strip() for part in value.split(",") if part.strip()]
            if isinstance(value, list):
                return [str(part).strip() for part in value if str(part).strip()]
        return []

    titles = values("person_titles", "titles", "buyer_titles")
    keywords = values("company_keywords", "org_keywords", "keywords", "industries")
    strong = values("strong_fit_signals", "fit_signals", "strong_signals")
    medium = values("medium_fit_signals", "medium_signals") or keywords
    if not titles:
        raise ValueError("ICP needs person_titles (or buyer_titles).")
    if not keywords:
        raise ValueError("ICP needs company_keywords (or org_keywords/industries).")
    campaign = {
        "label": name,
        "candidate_segment": "uploaded_icp",
        "person_titles": titles,
        "person_seniorities": values("person_seniorities", "seniorities"),
        "person_locations": values("person_locations", "locations", "geographies"),
        "org_headcount_ranges": values("org_headcount_ranges", "headcount_ranges", "company_sizes"),
        "org_keywords": keywords,
        "per_page": min(int(raw.get("per_page") or 100), 100),
        "max_pages": min(int(raw.get("max_pages") or 1), 5),
    }
    return {"agentbox_campaign": {
        "label": name,
        "default_campaign": "uploaded_icp",
        "campaigns": {"uploaded_icp": campaign},
        "runtime_terms": {
            "strong": strong or keywords,
            "medium": medium,
            "ephemeral": [],
        },
        "exclusion_terms": values("exclusion_terms", "exclude", "negative_keywords"),
        "competitor_terms": values("competitor_terms", "competitors"),
        "hard_exclusion_terms": values("hard_exclusion_terms", "hard_exclusions", "non_target_terms"),
    }}


def _structured_icp_from_text(content: str) -> dict[str, list[str]]:
    """Extract common ICP fields from a human-written document."""
    sections: dict[str, list[str]] = {}
    current = "company_keywords"
    aliases = {
        "title": "person_titles",
        "persona": "person_titles",
        "role": "person_titles",
        "industry": "company_keywords",
        "vertical": "company_keywords",
        "company": "company_keywords",
        "geograph": "locations",
        "location": "locations",
        "region": "locations",
        "signal": "strong_fit_signals",
        "exclude": "exclusion_terms",
        "competitor": "competitor_terms",
        "headcount": "headcount_ranges",
        "company size": "headcount_ranges",
    }
    for raw_line in content.splitlines():
        line = raw_line.strip().strip("•-*–— \t")
        if not line:
            continue
        heading = line.lower().rstrip(":")
        matched = next((field for key, field in aliases.items() if key in heading and len(line) < 90), None)
        if matched and (raw_line.strip().endswith(":") or len(line.split()) <= 7):
            current = matched
            continue
        values = [part.strip() for part in re.split(r"[,;|]", line) if part.strip()]
        sections.setdefault(current, []).extend(values)
    titles = sections.get("person_titles", [])
    if not titles:
        title_pattern = re.compile(r"\b(?:chief|cto|cio|ceo|vp|vice president|head|director|founder)[^.;,\n]{0,45}", re.I)
        titles = [match.group(0).strip() for match in title_pattern.finditer(content)]
    keywords = sections.get("company_keywords", [])
    if not keywords:
        keywords = [
            line.strip("•-*–— ")
            for line in content.splitlines()
            if 2 <= len(line.split()) <= 8 and len(line) < 90
        ][:20]
    sections["person_titles"] = list(dict.fromkeys(titles))[:30]
    sections["company_keywords"] = list(dict.fromkeys(keywords))[:40]
    return sections
