# Mint

Mint turns event guest lists and uploaded spreadsheets into evidence-backed,
ranked sales leads. It resolves person-company identity before paid enrichment,
classifies fit, and sends uncertain records to human review through a chat console.

## Backend

Run the FastAPI service locally:

```bash
uvicorn mint.backend.app:app --reload --port 8000
```

From an uninstalled source checkout, use:

```bash
PYTHONPATH=src uvicorn mint.backend.app:app --reload --port 8000
```

Required environment variables are shown in `.env.example`: Apollo, Exa, Luma, GMI, and `DATABASE_URL`.

Install the locked development environment and run the backend suite with:

```bash
uv sync --frozen
uv run pytest -q
```

`uv.lock` is committed intentionally. Update it only when dependencies in
`pyproject.toml` change; CI and the production container both use the frozen lock.

The current component boundaries and background-job limitations are documented
in [`docs/architecture.md`](docs/architecture.md).

HubSpot and Clay are implemented locally but currently undeployed; see `docs/integrations.md`.

## AgentBox lead sourcing

Run the outbound source -> score -> XLSX workflow with:

```bash
python -m mint.source --campaign product_runtime_us --out agentbox-linkedin-leads.xlsx
```

It uses Apollo people search and Exa runtime-fit research when both keys are present, then uses Apollo bulk people enrichment only for `pursue` and `nurture` leads that need LinkedIn profile URLs. The CLI workbook contains qualified leads with a returned LinkedIn URL. With either key absent it completes in `dry-run` mode using mock Apollo leads and Apollo-blurb-only runtime evidence, without spending credits. The same workflow is available from the Source tab and `POST /source/agentbox`.

Scheduled managed-event sync is exposed at `POST /admin/sync-luma` and protected by `MINT_SYNC_TOKEN`. Render cron setup is documented in `DEPLOY.md`.

Manual CSV/XLSX uploads are exposed at `POST /events/upload`; the chat UI also has an attach control that posts to this endpoint and then summarizes the ingest in the thread.

## Agent Chat Frontend

The chat console is a Next.js App Router app at the repo root. It talks to the FastAPI backend through
`NEXT_PUBLIC_API_BASE`.

```bash
npm install
npm run dev
```

Then open `http://localhost:3000`.

Useful frontend commands:

```bash
npm run test:frontend
npm run typecheck
npm run build
```

The UI uses Tailwind, Lucide icons, local shadcn-style primitives, and bundled Hanken Grotesk / IBM Plex Mono fonts.

## Public source

See [the public source and data boundary](docs/public-source.md). Production data
and operational prospect lists are excluded from this source snapshot.
