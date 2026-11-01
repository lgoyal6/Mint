from mint.backend.db.tables import Classification, EnrichmentLog, Person


def test_review_queue_columns_are_indexed() -> None:
    classification_indexes = {index.name for index in Classification.__table__.indexes}
    person_indexes = {index.name for index in Person.__table__.indexes}

    assert "ix_classifications_review_status" in classification_indexes
    assert "ix_people_current_classification_id" in person_indexes


def test_dead_schema_columns_are_not_mapped() -> None:
    person_columns = set(Person.__table__.columns.keys())
    enrichment_log_columns = set(EnrichmentLog.__table__.columns.keys())

    assert not {
        "has_console_account",
        "console_checked_at",
        "hubspot_contact_id",
        "hubspot_synced_at",
    } & person_columns
    assert not {"cost_dollars", "changes_detected"} & enrichment_log_columns
