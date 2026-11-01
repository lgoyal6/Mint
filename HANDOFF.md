# Mint, owner's handoff

Start here. This is the orientation for whoever owns and runs Mint. Read it once top to bottom, then keep it open. It describes what is **actually shipped** today; when this file and `designdoc.md` disagree, this file (and the code) win.

## What Mint is

Mint is GMI Cloud's lead enrichment and MQL classification system. It turns an event guest list (or a CSV) into trustworthy, tiered leads (P0 / P1 / NP). You drive it from a chat "agent console" (the Next.js frontend) talking to a FastAPI backend over Postgres.

The pipeline is **resolve, then gate, then enrich, then classify**: figure out who a person is and where they actually work, gate on how confident that link is, only then spend money enriching the company (Apollo + Exa), and finally score the lead against the ICP rubric. The gate is the load-bearing idea; `designdoc.md` has the full rationale and is worth reading once for the "why."

## The docs, and what to trust

| Doc | What it is |
|---|---|
| **HANDOFF.md** (this file) | Current state, real layout, how to run / deploy / debug. Trust this. |
| **README.md** | Local run commands. |
| **DEPLOY.md** | Production deploy: Render backend, Neon Postgres, Vercel frontend, Luma cron. |
| **designdoc.md** | The original design and the reasoning behind resolve/gate/enrich. Still the best explanation of the model, but it is a **vision doc**: parts were never built (see below). Code wins on any conflict. |
| **docs/auto-review.md** | How the automated review-queue sweep works. |
| **config/icp.yaml** | The ICP rubric: verticals, tiers, gates. The file you will edit most. |

## What's shipped vs planned (so the design doc doesn't mislead you)

- **There is no natural-language `query_leads_db` tool.** The agent has the specific, typed tools listed below, not a freeform SQL agent.
- **Auto-review exists and is not in the original doc.** An LLM reviewer clears the `NEEDS_REVIEW` queue after each classification run. See `docs/auto-review.md`.
- **Leads leave Mint as an export.** The agent's `export_leads` tool produces an XLSX/CSV workbook; that's the handoff artifact to BD.

## Real module map (`src/mint/backend/`)

- `routes/` — the HTTP layer: `health`, `auth` (prefix `/auth`), `events`, `leads`, `review`, `pipeline`, `exports`, `jobs`, `agent`, `chat`, `admin` (prefix `/admin`).
- `enrichment/` — the brain: `service.py` (resolve, gate, enrich), `apollo.py`, `exa.py`, `company_resolver.py`. Tread carefully here.
- `ingestion/` — getting leads in: `luma.py`, `csv_loader.py`, `normalizer.py`, `orchestrator.py`, `poller.py`.
- `classification/` — `classifier.py` (the scorer), `gmi.py` (GMI MaaS client), `service.py`.
- `review/` — `auto_reviewer.py` (the automated review sweep).
- `agent/` — `loop.py`, `tools.py`, `prompts.py`, `client.py` (the chat agent).
- `db/` — SQLAlchemy models and storage helpers. `migrations/` — Alembic (13 migrations).
- `exports/leads.py` — the XLSX/CSV workbook writer.
- `settings.py` — all env config. `utils/` — domain and http helpers.

`integrations/` contains local HubSpot and Clay implementations. Both are currently undeployed; see `docs/integrations.md`. The deployed vendor clients live under `enrichment/` (Apollo, Exa) and `ingestion/` (Luma).

## The agent's actual tools (`agent/tools.py`)

`pull_event`, `classify_event`, `list_events`, `event_summary`, `event_analytics`, `list_leads`, `get_lead`, `list_flagged`, `list_needs_review`, `resolve_flagged`, `reclassify_lead`, `export_leads`, `pipeline_summary`, `data_quality_summary`.

## Run it locally

```bash
cp .env.example .env            # then fill in the keys
PYTHONPATH=src uvicorn mint.backend.app:app --reload --port 8000
npm install && npm run dev      # frontend on http://localhost:3000
```

Confirm `GET /health` returns OK, then drive the agent on a small test event before touching production. See `README.md` for more.

## Configuration (env)

`.env.example` lists every variable and is kept in sync with `settings.py`. The ones that matter most:

- **Keys:** `APOLLO_API_KEY`, `EXA_API_KEY`, `GMI_API_KEY`, `LUMA_API_KEY`, `DATABASE_URL`.
- **Models (all via GMI MaaS):** `GMI_CLASSIFIER_MODEL` (Haiku), `GMI_AGENT_MODEL` (Sonnet), `GMI_REVIEWER_MODEL` (Opus).
- **Auth / CORS in prod:** `MINT_AUTH_ENABLED`, `MINT_AUTH_PASSWORD`, `MINT_SESSION_SECRET`, `MINT_FRONTEND_ORIGIN`.
- **Ops:** `MINT_SYNC_TOKEN` guards `POST /admin/sync-luma` and `POST /admin/auto-review`.
- **Rubric paths:** `ICP_CONFIG_PATH`, `COMPETITOR_TERMS_PATH`, `BIGTECH_TERMS_PATH`.

## Deploy and ops

Full detail in `DEPLOY.md`. In short: push to `main`, Render runs `alembic upgrade head` against Neon and starts the backend, and Vercel builds the frontend. A Render cron job hits `POST /admin/sync-luma` every 15 to 30 minutes. Run `POST /admin/auto-review` to sweep the review queue (the cron does not auto-review on its own).

## Make a change safely

```bash
git checkout -b fix/x
pytest && ruff check .                 # backend
npm run typecheck && npm run build     # frontend, if you touched it
```

Tune the rubric in `config/icp.yaml` (no code change). Add exclusions in `config/*_terms.txt`. A new DB column means a new Alembic migration, which runs on deploy, so keep migrations additive (add columns and tables, avoid drops). Do not rewrite the resolve-before-enrich gate without a test and a clear reason; it is the load-bearing design.

## Debugging, fast

- **A lead looks misclassified** — it is almost always resolution, not the classifier. Inspect the person's resolution evidence and affiliation state; LOW and FLAGGED never reach the classifier by design.
- **An enrichment failed** — check the `enrichment_logs` table (status, error) for that person or company.
- **Luma is not syncing** — confirm the cron, then hit `POST /admin/sync-luma` with the `X-Mint-Sync-Token` header.
- **The review queue is stuck** — run `POST /admin/auto-review`.
