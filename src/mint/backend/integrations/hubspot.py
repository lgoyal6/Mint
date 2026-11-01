from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

import httpx

from mint.backend.exports.leads import LeadExportData, LeadExportRow


HUBSPOT_CONTACT_OBJECT_TYPE_ID = "0-1"


@dataclass(frozen=True, slots=True)
class HubSpotImportResult:
    import_id: str | None
    state: str | None
    row_count: int
    list_name: str
    response: dict[str, Any]

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "state": self.state,
            "row_count": self.row_count,
            "list_name": self.list_name,
            "response": self.response,
        }


class HubSpotClient:
    def __init__(self, access_token: str, *, base_url: str = "https://api.hubapi.com") -> None:
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        await self._client.aclose()

    async def check_access(self) -> dict[str, Any]:
        """Verify the configured credential and crm.import scope without creating records."""
        response = await self._client.get(
            f"{self.base_url}/crm/v3/imports",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params={"limit": 1},
        )
        response.raise_for_status()
        payload = response.json()
        return {"status": "ok", "imports_visible": len(payload.get("results") or [])}

    async def import_contacts(
        self,
        export: LeadExportData,
        *,
        list_name: str,
        marketable: bool = False,
        create_list: bool = True,
    ) -> HubSpotImportResult:
        rows = [_hubspot_row(row) for row in export.rows if row.email]
        csv_bytes = _contacts_csv(rows)
        filename = f"{_safe_filename(list_name)}.csv"
        import_request = _import_request(
            name=list_name,
            filename=filename,
            marketable=marketable,
            create_list=create_list,
        )
        response = await self._client.post(
            f"{self.base_url}/crm/v3/imports",
            headers={"Authorization": f"Bearer {self.access_token}"},
            data={"importRequest": json.dumps(import_request)},
            files={"files": (filename, csv_bytes, "text/csv")},
        )
        response.raise_for_status()
        payload = response.json()
        return HubSpotImportResult(
            import_id=str(payload.get("id") or payload.get("importId") or "") or None,
            state=payload.get("state"),
            row_count=len(rows),
            list_name=list_name,
            response=payload,
        )


def _hubspot_row(row: LeadExportRow) -> dict[str, str]:
    return {
        "First Name": row.first_name or _first_name(row.name),
        "Last Name": row.last_name or _last_name(row.name),
        "Email": row.email,
        "Job Title": row.title or "",
        "Company Name": row.company or "",
        "Website URL": row.domain or "",
    }


def _contacts_csv(rows: list[dict[str, str]]) -> bytes:
    headers = ["First Name", "Last Name", "Email", "Job Title", "Company Name", "Website URL"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _import_request(*, name: str, filename: str, marketable: bool, create_list: bool) -> dict[str, Any]:
    return {
        "name": name,
        "importOperations": {HUBSPOT_CONTACT_OBJECT_TYPE_ID: "UPSERT"},
        "dateFormat": "YEAR_MONTH_DAY",
        "marketableContactImport": marketable,
        "createContactListFromImport": create_list,
        "files": [
            {
                "fileName": filename,
                "fileFormat": "CSV",
                "fileImportPage": {
                    "hasHeader": True,
                    "columnMappings": [
                        _mapping("First Name", "firstname"),
                        _mapping("Last Name", "lastname"),
                        _mapping("Email", "email", alternate_id=True),
                        _mapping("Job Title", "jobtitle"),
                        _mapping("Company Name", "company"),
                        _mapping("Website URL", "website"),
                    ],
                },
            }
        ],
    }


def _mapping(column_name: str, property_name: str, *, alternate_id: bool = False) -> dict[str, str]:
    output = {
        "columnObjectTypeId": HUBSPOT_CONTACT_OBJECT_TYPE_ID,
        "columnName": column_name,
        "propertyName": property_name,
    }
    if alternate_id:
        output["columnType"] = "HUBSPOT_ALTERNATE_ID"
    return output


def _first_name(name: str | None) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else ""


def _last_name(name: str | None) -> str:
    parts = (name or "").strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:120] or "mint-hubspot-import"
