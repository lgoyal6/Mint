# Deployment Notes

## Backend: Render + Neon

Create a Render web service from this repo using the included `Dockerfile`. Neon provides the Postgres database through `DATABASE_URL`. The container runs migrations before starting FastAPI:

```bash
alembic upgrade head && uvicorn mint.backend.app:app --host 0.0.0.0 --port $PORT
```

Set these Render backend environment variables:

- `APOLLO_API_KEY`
- `EXA_API_KEY`
- `GMI_API_KEY`
- `GMI_BASE_URL`
- `GMI_CLASSIFIER_MODEL`
- `GMI_AGENT_MODEL`
- `GMI_REVIEWER_MODEL`
- `DATABASE_URL`
- `LUMA_API_KEY`
- `MINT_SYNC_TOKEN`
- `MINT_AUTH_ENABLED=true`
- `MINT_AUTH_PASSWORD`
- `MINT_SESSION_SECRET`
- `MINT_COOKIE_SECURE=true`
- `MINT_COOKIE_SAMESITE=none`
- `MINT_FRONTEND_ORIGIN=https://<your-vercel-app>.vercel.app`

HubSpot and Clay are currently undeployed. Leave `HUBSPOT_ACCESS_TOKEN`,
`CLAY_WEBHOOK_URL`, and `CLAY_CALLBACK_TOKEN` unset until their production
permissions and external workflows have been tested; see `docs/integrations.md`.

Optional usage-monitor configuration:

- `APOLLO_CREDIT_COST_USD`: effective dollar cost per Apollo enrichment credit

After Render gives you the backend URL, verify both the service and Neon connection:

```bash
curl -fsS https://<render-backend>/health
curl -fsS https://<render-backend>/health/db
curl -fsS https://<render-backend>/health/integrations
```

The integrations check validates Apollo through its no-credit auth-health endpoint.
Exa is reported as configured or disabled only: Mint deliberately does not issue an
Exa search from a health check because Exa does not document a no-cost key validator.

## Frontend: Vercel

Create a Vercel project from this repo. The repo includes `vercel.json`, which runs `npm ci` and `npm run build`.

Set this Vercel environment variable:

- `NEXT_PUBLIC_API_BASE=https://<render-backend>`

After Vercel gives you the frontend URL, update Render `MINT_FRONTEND_ORIGIN` to that exact origin and redeploy the backend so credentialed CORS matches production.

## Scheduled Luma Sync

Luma ingestion runs through polling, not webhooks. Run the Luma poller from a Render cron job rather than an in-process scheduler. The cron should call the protected backend endpoint every 15-30 minutes:

```bash
curl -fsS -X POST "$MINT_BACKEND_URL/admin/sync-luma" \
  -H "X-Mint-Sync-Token: $MINT_SYNC_TOKEN" \
  -H "content-type: application/json" \
  -d '{"classify": true}'
```

Set these environment variables on the cron service:

- `MINT_BACKEND_URL`: deployed backend base URL, for example `https://mint-backend.example.com`
- `MINT_SYNC_TOKEN`: shared secret matching the backend's `MINT_SYNC_TOKEN`

The endpoint runs inline today. If sync duration grows past request timeouts, move the endpoint body behind a durable queue/worker and return a job id.

The scheduled Lu.ma sync does not run auto-review. Run `POST /admin/auto-review` explicitly when you want the reviewer to adjust pending review classifications.
