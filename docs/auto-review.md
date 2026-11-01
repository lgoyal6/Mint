# AutoReview: automated review-queue processing

## Problem

Every classification run flags most results for human review (`review_status = NEEDS_REVIEW`): after the v2 recalibration reset, 648 of 799 classifications needed review, the large majority low-stakes NP confirmations. Separately, flagged company conflicts require a human to pick the right company. The review queues are too large for humans to keep up with.

## Decisions (user-confirmed)

- Layered automation: deterministic triage + LLM second-opinion + LLM company-conflict adjudication.
- Full autonomy with an audit trail: the AI confirms or changes tiers on its own; every decision is recorded (`mint-auto-reviewer`) and overridable. Humans only see items the AI marks uncertain.
- Runs after every classification run (CSV upload, event backfill, Luma sync cron, admin reset), plus a token-guarded manual endpoint.

## Design

### AutoReviewService (`src/mint/backend/review/auto_reviewer.py`)

`sweep()` processes two work lists and returns a summary
(`reviews_confirmed/adjusted/uncertain`, `conflicts_resolved/uncertain`, `errors`).

**1. Pending classification reviews** — current classifications with
`review_status = 'NEEDS_REVIEW'` and `auto_review_status IS NULL`:

- **Triage (no LLM)**: NP rows whose `np_trigger` is structural
  (`bigtech_or_large_corp`, `student_or_academic_role`, `government_public_sector`,
  `academic_institution`, `npo_ngo`, `direct_competitor`, `large_corp_10k_plus`,
  `not_target`, `non_buyer_role`, `no_product_fit`) → confirmed immediately.
- **LLM review** (model: `GMI_REVIEWER_MODEL`, default `anthropic/claude-opus-4.5`,
  capped at `max_llm_reviews = 200` per sweep): receives the same lead context the
  classifier saw plus the existing classification and why it was flagged; returns
  `confirm | adjust | uncertain` with tier + reasoning. No fresh Exa research in v1 —
  "needs more data" is what `uncertain` is for.
- **Verdicts**:
  - confirm → `review_status = 'CLASSIFIED'`, `auto_review_status = 'confirmed'` (leaves queue).
  - adjust → new classification row via `reclassify_person(..., reviewed_by='mint-auto-reviewer', mark_human_review=False)`; old row marked `auto_review_status = 'adjusted'`. History preserved; because `reviewed_at` stays NULL for auto decisions, the human-override guard still only protects humans and future reclassify runs can overrule the bot.
  - uncertain → `auto_review_status = 'uncertain'` + note; stays in the human queue with the AI's note appended to the item's reason.

**2. Pending company conflicts** — people with `resolution_confidence = 'flagged'`,
not dismissed, `auto_resolve_attempted_at IS NULL`:

- LLM weighs `candidate_companies` against the evidence (self-reported company, email
  domain, resolution evidence, people-search hits); returns `resolve(chosen_domain) | uncertain`.
- resolve → existing `resolve_flagged_person` + enrich/reclassify follow-up
  (same as the manual resolve path).
- uncertain → `auto_resolve_attempted_at` + note set; left for a human; not re-judged.

### Schema (migration `20260610_0012`)

- `classifications.auto_review_status TEXT`, `auto_reviewed_at timestamptz`, `auto_review_note TEXT`
- `people.auto_resolve_attempted_at timestamptz`, `auto_resolve_note TEXT`

### Wiring

- `AppContext.auto_review_service` (None when no GMI key).
- Sweeps run after classification completes in: CSV upload job, event backfill job,
  `sync_managed_events`, admin reset's `reclassify_all` job.
- `POST /admin/auto-review` (X-Mint-Sync-Token) for manual sweeps / draining backlog.
- `list_needs_review` appends "AI reviewer: <note>" to the reason for uncertain items.
- No frontend changes: queues shrink on their own.

## Testing

Unit tests with fakes for triage/confirm/adjust/uncertain/conflict paths and the LLM cap;
DB-gated tests for the new storage methods and `mark_human_review=False`; route tests for
the admin endpoint and post-classification trigger; full-chain alembic verification.
