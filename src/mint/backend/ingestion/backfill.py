from __future__ import annotations

import math
import time
from typing import Any
from uuid import UUID

from mint.backend.classification.service import ClassificationScope
from mint.backend.models import ClassifyResult


async def run_event_backfill(
    ctx: Any,
    event_id: UUID,
    *,
    classify: bool,
    progress: Any = None,
) -> dict[str, Any]:
    """Enrich and optionally classify one stored Mint event.

    Both the HTTP endpoint and the agent tool use this workflow. Keeping the
    provider routing, persistence guard, progress reporting, and auto-review in
    one place prevents the two entry points from drifting apart.
    """
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise ValueError("event not found")

    on_progress = stage_progress(progress, "enriching", "Enriching {done} of {total} leads")
    if event.get("luma_event_id"):
        summary = await ctx.orchestrator.ingest_luma_event(
            str(event["luma_event_id"]),
            enrich=True,
            on_progress=on_progress,
        )
    else:
        summary = await ctx.orchestrator.enrich_event_registrations(
            event_id,
            on_progress=on_progress,
        )

    ensure_persistence_succeeded(summary)
    response = summary.model_dump(mode="json")
    if classify:
        classification = await classify_event(ctx, summary.event_id, progress=progress)
        response["classification"] = classification.model_dump(mode="json")
        await run_auto_review(ctx, response)
    return response


def ensure_persistence_succeeded(summary: Any) -> None:
    """Reject a run where every provider result failed to reach storage."""
    storage_failures = sum(
        1
        for lead in summary.lead_errors
        if any(str(error).startswith("storage:") for error in lead.errors)
    )
    if summary.enriched_count > 0 and storage_failures >= summary.enriched_count:
        raise RuntimeError(
            f"Enrichment providers returned results, but persistence failed for all {storage_failures} leads. "
            "Classification was stopped to avoid reporting a false success."
        )


async def classify_event(ctx: Any, event_id: UUID | None, *, progress: Any = None) -> ClassifyResult:
    if event_id is None:
        return ClassifyResult(errors=["event_id unavailable for classification"])
    return await ctx.classification_service.classify_batch(
        ClassificationScope(event_id=event_id, force_reclassify=True),
        on_progress=stage_progress(progress, "classifying", "Classifying {done} of {total} leads"),
    )


async def run_auto_review(ctx: Any, response: dict[str, Any]) -> None:
    service = getattr(ctx, "auto_review_service", None)
    if service is None:
        return
    try:
        summary = await service.sweep()
    except Exception as exc:
        response.setdefault("errors", []).append(f"auto_review: {exc}")
        return
    response["auto_review"] = summary.model_dump(mode="json")


def stage_progress(progress: Any, stage: str, message_template: str):
    if progress is None:
        return None

    started_at = time.monotonic()

    async def on_progress(done: int, total: int) -> None:
        elapsed = max(time.monotonic() - started_at, 0)
        rate = done / elapsed if done and elapsed else 0
        eta = math.ceil((total - done) / rate) if rate and done < total else 0 if done >= total else None
        message = message_template.format(done=done, total=total)
        if eta is not None and done < total:
            message += f" · about {duration(eta)} remaining"
        await progress.update(
            stage=stage,
            done=done,
            total=total,
            message=message,
            eta_seconds=eta,
            elapsed_seconds=math.floor(elapsed),
            rate_per_minute=round(rate * 60, 1) if rate else None,
        )

    return on_progress


def duration(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, remaining = divmod(minutes, 60)
    return f"{hours}h {remaining}m" if remaining else f"{hours}h"
