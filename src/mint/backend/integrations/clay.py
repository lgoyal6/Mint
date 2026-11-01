from __future__ import annotations

from typing import Any

import httpx


class ClayClient:
    """Asynchronous Clay table integration using a table webhook source."""

    def __init__(self, webhook_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=30, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def enqueue_identity_review(self, lead: dict[str, Any]) -> dict[str, Any]:
        person = lead.get("person") if isinstance(lead.get("person"), dict) else {}
        company = lead.get("company") if isinstance(lead.get("company"), dict) else {}
        payload = {
            "mint_person_id": person.get("id"),
            "email": person.get("email"),
            "first_name": person.get("first_name"),
            "last_name": person.get("last_name"),
            "title": person.get("title") or person.get("self_reported_title"),
            "linkedin_url": person.get("linkedin_url"),
            "self_reported_company": person.get("self_reported_company"),
            "self_reported_building": person.get("self_reported_building"),
            "current_company": company.get("name"),
            "current_domain": company.get("domain"),
            "affiliation_state": person.get("affiliation_state"),
            "resolution_confidence": person.get("resolution_confidence"),
            "resolution_evidence": person.get("resolution_evidence"),
            "candidate_companies": person.get("candidate_companies"),
            "mint_callback_path": "/integrations/clay/identity-callback",
        }
        response = await self._client.post(self.webhook_url, json=payload)
        response.raise_for_status()
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return {
            "queued": True,
            "person_id": person.get("id"),
            "status_code": response.status_code,
            "clay_response": body,
        }
