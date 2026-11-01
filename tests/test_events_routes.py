import pytest

from mint.backend.models import IngestSummary, LeadError
from mint.backend.ingestion.backfill import ensure_persistence_succeeded


def test_total_storage_failure_stops_false_success() -> None:
    summary = IngestSummary(
        total_guests=2,
        going_count=2,
        enriched_count=2,
        lead_errors=[
            LeadError(email="a@example.com", errors=["storage: save failed"]),
            LeadError(email="b@example.com", errors=["storage: save failed"]),
        ],
    )

    with pytest.raises(RuntimeError, match="persistence failed for all 2 leads"):
        ensure_persistence_succeeded(summary)


def test_partial_storage_failure_remains_visible_in_summary() -> None:
    summary = IngestSummary(
        total_guests=2,
        going_count=2,
        enriched_count=2,
        lead_errors=[LeadError(email="a@example.com", errors=["storage: save failed"])],
    )

    ensure_persistence_succeeded(summary)
