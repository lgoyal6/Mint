import csv
import io

import asyncio
import httpx

from mint.backend.exports.leads import LeadExportRow
from mint.backend.integrations.hubspot import _contacts_csv, _hubspot_row, _import_request
from mint.backend.integrations.hubspot import HubSpotClient


def test_hubspot_contact_csv_uses_standard_contact_columns() -> None:
    row = LeadExportRow(
        tier="P1",
        primary_product="gpu",
        confidence=4,
        review_status="CLASSIFIED",
        first_name="Ada",
        last_name="Lovelace",
        name="Ada Lovelace",
        email="ada@example.com",
        title="Founder",
        linkedin_url=None,
        company="Analytical Engines",
        domain="analytical.example",
        target_vertical="agentic",
        what_they_are_building=None,
        key_signal="Founder",
        key_risk="Early",
        reasoning="Strong fit.",
        product_fit=["gpu"],
        np_trigger=None,
        event_names=["Luma Event"],
        checked_in=True,
        apollo_match="matched",
        resolution_confidence="high",
        data_quality="rich",
    )

    payload = _contacts_csv([_hubspot_row(row)]).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(payload)))

    assert rows == [
        {
            "First Name": "Ada",
            "Last Name": "Lovelace",
            "Email": "ada@example.com",
            "Job Title": "Founder",
            "Company Name": "Analytical Engines",
            "Website URL": "analytical.example",
        }
    ]


def test_hubspot_import_request_creates_static_list_from_upsert_import() -> None:
    request = _import_request(
        name="Mint Luma Event - Founder Night - P0/P1",
        filename="founder-night.csv",
        marketable=False,
        create_list=True,
    )

    assert request["importOperations"] == {"0-1": "UPSERT"}
    assert request["createContactListFromImport"] is True
    assert request["marketableContactImport"] is False
    mappings = request["files"][0]["fileImportPage"]["columnMappings"]
    email_mapping = next(mapping for mapping in mappings if mapping["columnName"] == "Email")
    assert email_mapping["propertyName"] == "email"
    assert email_mapping["columnType"] == "HUBSPOT_ALTERNATE_ID"


def test_hubspot_access_check_is_read_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [{"id": "import-1"}]})

    async def run() -> dict:
        client = HubSpotClient("service-key", base_url="https://hubspot.test")
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.check_access()
        finally:
            await client.close()

    result = asyncio.run(run())
    assert result == {"status": "ok", "imports_visible": 1}
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/crm/v3/imports"
