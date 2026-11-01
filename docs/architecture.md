# Mint architecture

Mint has one Next.js frontend and one FastAPI backend. Postgres is the source
of truth for events, people, enrichment, classifications, chat history, and job
status. Apollo and Exa provide enrichment; GMI classifies enriched leads.

## Workflow boundaries

- `ingestion/` owns CSV/Luma ingestion and event backfills.
- `enrichment/` owns provider calls and normalized enrichment results.
- `db/` owns persistence contracts.
- `classification/` owns tier assignment.
- `agent/` and `routes/` are entry points; they must call shared workflows
  rather than reimplementing enrichment or classification.
- `jobs.py` provides progress and in-process execution. Job state is mirrored
  to Postgres so status remains queryable after completion.

## Operational constraints

Background execution is currently in-process. A Render restart marks an active
job failed; it cannot resume it. Do not deploy while a paid enrichment job is
running. The next infrastructure migration should move paid/long-running jobs
to a durable queue worker with idempotency keys and resumable checkpoints.

Until then, event backfills use a per-event active-job key to prevent duplicate
requests from spending provider credits concurrently.

## Dependency and test policy

`uv.lock` is committed and is the dependency source for local development, CI,
and the production image. CI runs backend tests with Postgres so storage
contract tests cannot silently skip, then runs frontend type checks, tests, and
the production build.
