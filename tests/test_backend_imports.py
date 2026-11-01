from mint.backend.enrichment.service import EnrichmentService
from mint.backend.models import EnrichmentInput
from mint.backend.settings import Settings


def test_backend_package_is_public_import_boundary() -> None:
    assert EnrichmentInput(email="alex@example.com").email == "alex@example.com"
    assert EnrichmentService is not None


def test_agent_model_defaults_to_sonnet_4_6(monkeypatch) -> None:
    monkeypatch.delenv("GMI_AGENT_MODEL", raising=False)

    assert Settings(_env_file=None).gmi_agent_model == "anthropic/claude-sonnet-4-6"
