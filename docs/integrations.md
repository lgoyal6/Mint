# Integration status

## Deployed pipeline integrations

- Luma: event and guest ingestion.
- Apollo: person and company enrichment.
- Exa: company resolution, people search, and deep research.
- GMI Cloud MaaS: classifier, agent, and reviewer models.

## Implemented locally, not deployed

### HubSpot

The local backend can import event contacts through HubSpot's CRM imports API and
create a static list. It is disabled until `HUBSPOT_ACCESS_TOKEN` is configured
with the required contact/import/list permissions. Do not describe HubSpot sync
as live until a production token has been permission-tested.

### Clay

Mint can send one identity-review record into a Clay table webhook. Clay should
run its identity/company waterfall, then use an HTTP action to call
`POST /integrations/clay/identity-callback` with `X-Mint-Clay-Token` and a
payload containing `person_id`, `company_domain`, optional person fields, and
evidence. The callback stores the Clay person result, resolves affiliation, and
starts Mint's normal enrichment/reclassification follow-up.

Clay is disabled until both `CLAY_WEBHOOK_URL` and `CLAY_CALLBACK_TOKEN` are
configured and the external Clay table/callback action has been tested.
