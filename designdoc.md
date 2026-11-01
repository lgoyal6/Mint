# mint Unified Enrichment Layer - Design Document

> **Read `HANDOFF.md` first.** This is the original design and the best explanation of *why* the resolve/gate/enrich model works, but it is a vision doc and the code has moved on. The agent's real tools differ from the ones sketched below, and there is now an auto-review step (see `docs/auto-review.md`). When this document and the code disagree, the code wins.

## Overview

Every lead that touches GMI Cloud's GTM motion gets enriched once and stored permanently. This enriched record is the single source of truth for classification, CRM sync, the agent, analytics, and outreach.

Enrichment is two distinct motions, not one. Resolving who a lead is and where they work is a separate problem from gathering intelligence about their company. The system resolves identity first, gates on confidence, then enriches only trustworthy records.

---

## Core Architecture

```
                    ┌──────────────┐     ┌──────────────────┐
                    │  Luma        │     │  Agent UI        │
                    │  Webhooks    │     │                  │
                    └──────┬───────┘     └────────┬─────────┘
                           │                      │
                    automatic               manual, via tools
                           │                      │
                           ▼                      ▼
              ┌────────────────────────────────────────────┐
              │         Enrichment Service                 │
              │                                            │
              │  Resolve (identity) -> Gate -> Enrich      │
              │  Apollo, Exa, dedup, TTL, DB writes         │
              │                                            │
              │  No knowledge of agent/UI/conversations     │
              └────────────────────┬───────────────────────┘
                                   │
                                   ▼
              ┌────────────────────────────────────────────┐
              │              Postgres                      │
              │  people, companies, events, registrations, │
              │  classifications, enrichment_logs           │
              └────────────────────┬───────────────────────┘
                                   │
              Downstream consumers (read):
              Classifier · CRM sync · Agent · Analytics · Outreach
```

### Entry points

**Webhooks (automatic):** Luma registration -> `POST /webhooks/luma` -> enrichment service. No agent involved.

**Agent tools (manual):** User tells agent to enrich/classify/pull guests -> agent calls tool -> tool calls enrichment service -> agent presents results.

**API (programmatic):** `POST /api/enrich` for external integrations.

All three call the same enrichment service. Same code, same database.

### What each source contributes

**Apollo** - structured fields (skeleton):
- Person: verified email, name, title, seniority, department, LinkedIn, location
- Company: domain, industry, employee count, founded year, technologies
- Funding: stage, round date, total funding, funding events
- Financials: revenue, headcount growth (6mo/12mo)

**Exa deep research** - substance (one call, structured output via `outputSchema`):
- What the company does, target vertical, business model
- Stage, revenue evidence, customer signals
- Inference/compute profile, spend signals
- Competitive landscape, tech stack, hiring signals
- Recent activity, person role signals, data quality

Exa returns structured JSON directly, ready for downstream use without a separate summarization step.

---

## Resolve Before Enrich

Collapsing resolution and enrichment creates false confidence: a rich company profile gets attached to a person who may not work there.

**The failure mode:** a lead types "Wobble" into a Luma form. We resolve wobble.dev, pull a rich company profile, and produce a confident P0 - but nobody verified the person actually works at Wobble. Wrong affiliation produces confident-but-wrong classifications, misdirected outreach, and polluted CRM data.

The model splits into two phases with a confidence gate between them:

1. **RESOLVE (cheap)** - Determine person identity, company identity, and the affiliation between them. Output is an identity claim with a confidence state and an evidence chain. Uses Apollo person match, email-domain analysis, and (only when needed) Exa company/people search.
2. **GATE** - Confidence determines whether expensive enrichment runs. High/medium proceeds. Low is stored without deep research. Contradicted goes to review.
3. **ENRICH (expensive, gated)** - Apollo org enrich + Exa deep research, only on trustworthy identities. The deep research query is grounded in a verified person and company.

### Deterministic before probabilistic

Signals are ranked. Strong signals settle the question; weak signals are consulted only when strong ones don't. The system never treats all signals equally or returns whatever clears the lowest confidence bar.

**Person identity signals:**

| Signal | Strength | Resulting State |
|--------|----------|-----------------|
| Apollo person match by verified email | Deterministic | verified |
| LinkedIn URL supplied and confirmed | Strong | verified |
| Exa people search with corroborating fields | Probabilistic | inferred |
| Self-reported Luma data only | Unverified | self_reported |
| Nothing usable | None | unknown |

**Company identity signals:**

| Signal | Strength | Resulting State |
|--------|----------|-----------------|
| Work email domain (alex@wobble.dev) | Deterministic | verified |
| Apollo person match returned employer org | Deterministic | verified |
| Self-reported text resolved via Exa company search | Probabilistic | inferred |
| Resolved via Exa people search | Probabilistic | inferred |
| Unresolvable / garbage ("N/A", "stealth") | None | unresolved / garbage |

### Affiliation is derived, not looked up

The key question - does this person actually work at this company? - is derived from whether the person and company signals corroborate each other. It is never a single lookup.

| Affiliation State | Condition |
|-------------------|-----------|
| verified | Work email domain equals resolved company domain, OR Apollo directly asserts this employer. Strongest when both agree. |
| inferred | Person and company resolved independently, consistent but not directly linked. |
| unverified | Company resolved, but no evidence ties this person to it. (The Wobble case.) |
| contradicted | Signals point to different companies. Never silently pick one - populate candidate_companies. |

Hard rules:
- Personal email + no Apollo match caps affiliation at `unverified` regardless of what Exa returns.
- Affiliation reaches `verified` only when two independent signals agree. A single signal asserting employment is `inferred` at best.

### Confidence is gated by affiliation

Overall confidence is driven by affiliation state, never inflated by how rich the company profile is. A record can be LOW confidence and rich company data simultaneously - we know a lot about a company this person may not work at.

| Affiliation | Person Known | Confidence | Gate Outcome |
|-------------|--------------|------------|--------------|
| verified | yes | HIGH | Enrich |
| inferred | yes | MEDIUM | Enrich |
| unverified | any | LOW | Store, skip deep research |
| contradicted | any | FLAGGED | Review queue |
| (unresolved/garbage) | - | UNRESOLVED | Store email + self-reported only |

`data_quality` (intelligence richness: rich/moderate/sparse) and `resolution_confidence` (link trustworthiness) are SEPARATE axes and are never conflated.

### Every resolution explains itself

Each resolved identity carries an ordered, human-readable evidence chain showing which signals drove the decision:

```
person.state: verified  (source: apollo, matched by email)
company.state: verified  (source: email_domain, wobble.dev)
evidence: ["Apollo matched person by email", "Email domain wobble.dev matches resolved company"]
-> affiliation: verified  ->  confidence: HIGH  ->  proceed to enrichment
```

### People-search confirmation

When Apollo misses and we lean on Exa people search, name alone is weak (many "Lucas Chen"s). A people-search result is only treated as `inferred` when enough corroborating fields agree (company match, supplied LinkedIn URL, title plausibility). Otherwise the person stays `self_reported` and affiliation cannot rise above `unverified`. The corroboration threshold is a documented, tunable rule.

---

## Exa Deep Research Schema

Exa's `outputSchema` caps at 10 properties (max nesting depth 2). The schema is compressed to 8 properties; related signals are merged into denser fields.

```python
ENRICHMENT_SCHEMA = {
    "type": "object",
    "required": [
        "what_they_do", "target_vertical", "stage",
        "funding_and_revenue", "compute_profile", "team_and_activity",
        "person_role_signals", "data_quality",
    ],
    "properties": {
        "what_they_do": {
            "type": "string",
            "description": "2-3 sentence description of core product/service and who they sell to.",
        },
        "target_vertical": {
            "type": "string",
            "description": "research_labs | coding_platforms | agentic | data_labeling | robotics | adjacent | no_match",
        },
        "stage": {
            "type": "string",
            "description": "pre-revenue | early-revenue | growth | mature | public | unknown. Include business model (SaaS, platform, services, hardware, research, marketplace, other) in reasoning.",
        },
        "funding_and_revenue": {
            "type": "string",
            "description": "Funding stage, total raised, investors, round dates. Revenue evidence: pricing pages, paid customers, case studies, customer logos, scale claims like '10K+ teams' or '1M+ API calls/day', or lack thereof. Be specific about what was and wasn't found.",
        },
        "compute_profile": {
            "type": "string",
            "description": "ML inference workloads, model hosting, GPU usage, open-source vs proprietary models, scale indicators (API volume, throughput, user count), current infrastructure providers (AWS, GCP, CoreWeave, Lambda, RunPod, etc), migration signals, and estimated monthly GPU/inference spend signals based on team size, scale, and funding.",
        },
        "team_and_activity": {
            "type": "string",
            "description": "Tech stack and frameworks. Open roles especially ML infra, platform eng, GPU, CUDA, model serving. Recent activity: funding rounds, product launches, partnerships, acquisitions, key hires, layoffs. Include approximate dates when available.",
        },
        "person_role_signals": {
            "type": "string",
            "description": "Lead's current role, seniority, and relevance to GPU/infrastructure purchasing decisions. Is this person a buyer, champion, technical evaluator, or end user?",
        },
        "data_quality": {
            "type": "string",
            "description": "rich (multiple corroborating sources, clear signals) | moderate (some gaps but workable) | sparse (minimal information found)",
        },
    },
}
```

### Exa deep research config

```python
{
    "query": _lead_research_query(company_name, domain, lead),
    "type": "deep",
    "numResults": 10,
    "contents": {"highlights": True},
    "systemPrompt": "You are a research agent qualifying leads for GPU infrastructure and inference compute sales...",
    "additionalQueries": [
        f'"{company_name}" funding revenue customers',
        f'"{company_name}" AI GPU inference infrastructure',
        f'"{company_name}" hiring ML infrastructure GPU',
        f'"{company_name}" {title} {what_they_are_building}',
    ],
    "outputSchema": ENRICHMENT_SCHEMA,
}
```

### Exa company search and people search

**Company search** (`category: "company"`) is a lightweight resolution step, used only when the domain can't be determined from email or Apollo. Not cached.

**People search** (`category: "people"`, Exa's 1B+ LinkedIn profile index) serves resolution - confirming identity and affiliation when borderline. Run during resolve only when it could change the gate decision, not on every Apollo miss.

---

## Luma Input Handling

### Stable fields

Every Luma event has: name, email, LinkedIn URL, Twitter/X handle, approval status, check-in status. These always map the same way.

### Custom questions

Each event has different host-configured questions. "What company do you work for?", "What are you building?", "How many GPUs are you running?" - these change every event.

Custom question responses are stored as-is in a `survey_responses` JSONB blob on the registration record. No attempt to normalize every possible question into a fixed schema.

The enrichment service auto-detects two common mappings by fuzzy-matching the question text:
- Questions matching "company" / "work for" / "organization" -> company resolution input
- Questions matching "building" / "working on" / "project" -> `self_reported_building` on the person record

Everything else passes through as raw context for the Exa deep research query:

```
Lead context:
- name: Alex Rivera
- email: alex@quadex.ai
- Survey Q: "What are you building?" -> "Autonomous coding agent"
- Survey Q: "How many GPUs?" -> "8x H100s"
```

---

## Data Model

### People

```sql
CREATE TABLE people (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    company_id UUID REFERENCES companies(id),  -- set only when affiliation verified/inferred

    -- Resolution (entity resolution layer)
    person_state TEXT,               -- verified | inferred | self_reported | unknown
    company_state TEXT,              -- verified | inferred | unresolved | garbage
    affiliation_state TEXT,          -- verified | inferred | unverified | contradicted
    resolution_confidence TEXT,      -- high | medium | low | flagged | unresolved
    resolution_evidence JSONB,       -- ordered, human-readable evidence chain
    candidate_companies JSONB,       -- populated when contradicted

    -- Apollo person data
    apollo_person_data JSONB,
    apollo_match_status TEXT,        -- matched | partial | not_found
    apollo_person_title TEXT,
    apollo_person_seniority TEXT,
    apollo_person_departments TEXT[],
    apollo_person_linkedin_url TEXT,
    apollo_person_location TEXT,

    -- Exa person signals
    exa_person_role_signals TEXT,
    exa_people_search_results JSONB,

    -- Self-reported
    self_reported_title TEXT,
    self_reported_company TEXT,
    self_reported_building TEXT,

    -- Enrichment metadata
    apollo_enriched_at TIMESTAMPTZ,
    exa_enriched_at TIMESTAMPTZ,
    enrichment_data_quality TEXT,    -- separate axis from resolution_confidence

    -- Current classification (denormalized)
    current_tier TEXT,
    current_confidence INTEGER,
    current_classification_id UUID REFERENCES classifications(id),

    -- Console
    has_console_account BOOLEAN,
    console_checked_at TIMESTAMPTZ,

    -- HubSpot
    hubspot_contact_id TEXT,
    hubspot_synced_at TIMESTAMPTZ,

    -- Behavioral (computed)
    events_registered INTEGER DEFAULT 0,
    events_attended INTEGER DEFAULT 0,
    first_seen_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

`company_id` is set only when affiliation is verified or inferred. For unverified/contradicted records it stays null, which is itself meaningful. `enrichment_data_quality` describes company intelligence richness; `resolution_confidence` describes link trustworthiness. The two are never conflated.

### Companies

```sql
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain TEXT UNIQUE NOT NULL,
    name TEXT,

    -- Apollo org data
    apollo_org_data JSONB,
    apollo_org_industry TEXT,
    apollo_org_employee_count INTEGER,
    apollo_org_founded_year INTEGER,
    apollo_org_funding_stage TEXT,
    apollo_org_total_funding TEXT,
    apollo_org_revenue TEXT,
    apollo_org_technologies TEXT[],
    apollo_org_headcount_growth_6mo NUMERIC,
    apollo_org_headcount_growth_12mo NUMERIC,

    -- Exa deep research (compressed schema, structured output)
    exa_enrichment JSONB,
    exa_what_they_do TEXT,
    exa_target_vertical TEXT,
    exa_stage TEXT,
    exa_funding_and_revenue TEXT,
    exa_compute_profile TEXT,
    exa_team_and_activity TEXT,
    exa_data_quality TEXT,

    -- Raw responses
    exa_deep_raw JSONB,
    exa_grounding JSONB,

    -- Enrichment metadata
    apollo_enriched_at TIMESTAMPTZ,
    exa_enriched_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### Events

```sql
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    luma_event_id TEXT UNIQUE,
    name TEXT NOT NULL,
    event_date TIMESTAMPTZ,
    location TEXT,
    url TEXT,
    status TEXT,              -- upcoming | past | draft
    guest_count INTEGER DEFAULT 0,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### Registrations

```sql
CREATE TABLE registrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID REFERENCES people(id),
    event_id UUID REFERENCES events(id),
    email TEXT NOT NULL,
    approval_status TEXT,     -- going | pending | declined
    checked_in_at TIMESTAMPTZ,
    survey_responses JSONB,   -- raw Luma custom question responses
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(event_id, email)
);
```

### Classifications

The decision and the reasoning. Everything else lives on the enrichment layer. The classifier reads only people where `resolution_confidence` is high or medium - low, flagged, and unresolved records never reach classification (a WHERE clause, not a schema change).

```sql
CREATE TABLE classifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID REFERENCES people(id),
    company_id UUID REFERENCES companies(id),

    -- The decision
    tier TEXT NOT NULL,            -- P0 | P1 | NP
    confidence INTEGER,           -- 1-5
    reasoning TEXT,
    key_signal TEXT,
    key_risk TEXT,
    flags TEXT[],

    -- Review
    review_status TEXT,           -- AUTO_CLASSIFIED | QUICK_REVIEW | MANUAL_REVIEW | REVIEWED
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    reclassified_to TEXT,

    -- Metadata
    pipeline_version TEXT,
    classified_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### Enrichment logs

```sql
CREATE TABLE enrichment_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL, -- person | company
    entity_id UUID NOT NULL,
    source TEXT NOT NULL,      -- apollo_person | apollo_org | exa_deep | exa_people | exa_company_search
    status TEXT,               -- success | failed | skipped_fresh
    cost_dollars NUMERIC,
    request_id TEXT,
    changes_detected JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## Enrichment Flow

The flow is resolve, then gate, then enrich. Resolution is cheap and short-circuits as soon as it reaches a confident answer; expensive enrichment only runs past the gate.

### Phase 1: Resolve (cheap)

```
1. DEDUPLICATE
   Check people table by email.
   -> Exists + fresh -> skip
   -> Exists + stale -> run stale steps only
   -> New -> create record

2. APOLLO PERSON MATCH + EMAIL ANALYSIS
   Match by email (or name + company). Analyze email domain
   (personal vs corporate). If Apollo returns an employer AND
   email domain corroborates -> affiliation verified, short-circuit
   to the gate. No Exa spend on resolution.

3. RESOLVE COMPANY + AFFILIATION
   Only when signals are incomplete or conflicting. Run Exa company
   search on self-reported text and/or Exa people search on the name.
   Score corroboration. Derive person_state, company_state,
   affiliation_state. Garbage blocklist catches "N/A", "stealth",
   job-title-as-company, etc.

4. COMPUTE CONFIDENCE
   Combine states into resolution_confidence with an evidence chain.
   Write resolution columns to the person record.
```

### Phase 2: Gate

```
high | medium  -> proceed to enrichment
low            -> store resolved identity, skip deep research
flagged        -> review queue, candidate_companies populated
unresolved     -> store email + self-reported data only
```

### Phase 3: Enrich (expensive, gated)

```
5. COMPANY LOOKUP
   Check companies table by domain.
   -> Fresh -> reuse
   -> New or stale -> enrich

6. COMPANY ENRICHMENT (parallel)
   a. Apollo org enrich
   b. Exa deep research with compressed ENRICHMENT_SCHEMA, query
      grounded in the verified identity
   Store results, log to enrichment_logs.

7. LINK & DONE
   Set person.company_id. Lead is enriched and available for
   classification, CRM sync, agent queries, analytics.
```

### Caching

```
COMPANY level (keyed by domain, shared across people):
  Apollo org + Exa deep research output + raw responses
  TTL: 30 days

PERSON level (keyed by email):
  Apollo person + Exa person_role_signals + people search results
  TTL: 90 days (Apollo), 30 days (Exa person signals)
```

First person from a company pays full Exa cost. Subsequent people reuse company data. Re-enrichment is triggered both by TTL expiry and by events - when a person appears at a new event, stale data is refreshed.

### Cost per lead

```
Exa deep research:    ~$0.05-0.15/call
Exa company search:   ~$0.005/call (resolution only, when domain unknown)
Exa people search:    ~$0.01-0.03/call (resolution, borderline affiliation only)
Apollo:               per existing plan

New lead, new company:    ~$0.05-0.18 + Apollo
New lead, known company:  ~$0 + Apollo person credits
Cached lead:              $0
Low/flagged/unresolved:   no deep research spend (gated out)

400-person event, ~350 unique companies:
  ~350 Exa deep research:  $17-52
  ~60 Exa company search:  $0.30
  ~50 Exa people search:   $0.50-1.50
  Total:                    ~$18-54 + Apollo
```

---

## What This Enables

1. **Classification becomes a thin scoring pass.** Reads from enriched data. One lightweight LLM call per lead. 400 leads in 1-2 minutes.

2. **Trustworthy records only.** Downstream consumers only ever see records that are both enriched and trustworthy. The Wobble problem is gated out before it reaches the classifier, outreach, or CRM.

3. **Rich HubSpot data on every contact.** Push `what_they_do`, `target_vertical`, `stage`, funding, revenue, compute profile, classification tier + reasoning, talk track, events attended.

4. **Cross-event intelligence.** "Repeat attendees without console accounts" or "companies that sent 3+ people" are SQL queries against existing tables.

5. **Warm lead detection before events.** New registrations checked against existing enrichment instantly. No API calls needed.

6. **Trigger-based re-engagement.** Periodic re-enrichment detects funding changes, hiring signals, revenue status changes.

7. **Account-level intelligence.** Multiple people from same company share a company record. Roll up to account view.

8. **Classifier improves over time.** Gets behavioral data, account context, classification history.

9. **Longitudinal spend estimation.** Track employee growth, funding changes, hiring patterns over time.

10. **Agent becomes knowledgeable.** Reads from enrichment layer. No API calls for lead lookups. The enrichment layer is the agent's memory.

---

## Agent Architecture

### Main agent

Claude Opus via GMI Cloud MaaS. Conversation, reasoning, tool routing. Never writes SQL or calls APIs directly.

### Data sub-agent

Translates natural language to SQL. Called via `query_leads_db` tool. Runs on a smaller, cheaper model.

```
User: "Who from robotics companies came to the last 3 events?"
-> Main agent calls query_leads_db(question)
-> Data sub-agent receives schema + question
-> Writes SELECT, executes on read-only Postgres (5s timeout, 500 row limit)
-> Returns JSON to main agent
-> Main agent interprets and presents
```

Sub-agent prompt includes: table/column names, types, relationships, enum values, common JOINs. SELECT only.

---

## Agent Tools

### Phase 1: Core

```yaml
enrich_leads:
  description: "Enrich leads via resolve -> gate -> enrich. Accepts file, Luma event, or email list."
  inputs: { source_type, file_id?, luma_event_id?, emails? }
  outputs: { enriched_count, resolved_count, gated_out_count, flagged_count, new_companies, cached_companies }

classify_leads:
  description: "Run MQL classification on enriched, trustworthy leads. Reads from DB, does not re-enrich."
  inputs: { scope: event|all|lead_ids, event_id?, lead_ids?, force_reclassify? }
  outputs: { classified_count, p0, p1, np, review_needed }

query_leads_db:
  description: "Natural language query against enrichment database. Delegates to data sub-agent."
  inputs: { question }
  outputs: { results, row_count, query_executed }

reclassify_lead:
  description: "Manually override a lead's classification."
  inputs: { lead_id, new_tier, reason }
  outputs: { previous_tier, new_tier, updated }

lookup_lead:
  description: "Get full enriched profile by email or name."
  inputs: { email?, name? }
  outputs: { lead }

upload_file:
  description: "Upload CSV/XLSX for enrichment or classification."
  inputs: { file }
  outputs: { file_id, filename, row_count, detected_columns }
```

### Phase 2: Luma

```yaml
create_luma_event:
  inputs: { name, date, location, description }
  outputs: { event_id, luma_event_id, luma_url }

pull_luma_guests:
  inputs: { luma_event_id, enrich? }
  outputs: { total_guests, going_count, enriched_count, already_known }

list_luma_events:
  inputs: { status: upcoming|past|all }
  outputs: { events }

pull_luma_checkins:
  inputs: { luma_event_id }
  outputs: { checked_in, no_show }
```

### Phase 3: HubSpot

```yaml
push_to_hubspot:
  inputs: { scope: event|tier|lead_ids, event_id?, tier?, lead_ids?, create_list?, list_name? }
  outputs: { created, updated, errors }

check_hubspot_status:
  inputs: { scope: event|all, event_id? }
  outputs: { synced, not_synced }
```

### Phase 4: Console tracking

```yaml
check_console_accounts:
  inputs: { scope: event|tier|lead_ids|all_unknown }
  outputs: { has_account, no_account, leads }
```

### Phase 5: Outreach and content

```yaml
generate_bd_brief:
  inputs: { event_id, max_leads? }
  outputs: { brief_markdown, priority_leads }

generate_outreach_draft:
  inputs: { lead_id, outreach_type, event_id?, sender_name }
  outputs: { subject, body }

generate_thank_you:
  inputs: { event_id, cta_type, cta_url }
  outputs: { message, subject }

generate_event_description:
  inputs: { proposal, event_name, event_date, tone? }
  outputs: { description_markdown, title_suggestions }
```

### Phase 6: Re-enrichment

```yaml
re_enrich_stale:
  inputs: { scope: all|target_verticals|p0_p1, ttl_override_days? }
  outputs: { re_enriched_companies, re_enriched_people, changes_detected, cost_dollars }

detect_changes:
  inputs: { since_days }
  outputs: { funding_changes, revenue_changes, headcount_changes, new_hiring_signals }
```

### Always available

```yaml
web_search:
  description: "Quick Exa search for ad-hoc research during conversations."
  inputs: { query }
  outputs: { results }
```

---

## Luma Integration

Events originate in mint. Luma is the registration frontend.

### Event creation

Agent tells mint to create event -> mint creates DB record -> calls Luma API (private mode) -> links IDs -> starts watching for registrations.

### Registration ingestion

Only "going" guests trigger enrichment. Pending/declined stored but not enriched.

```
Guest approved on Luma
-> Webhook or manual pull
-> Check people table by email
-> Create/update person + registration records
-> Store survey_responses as JSONB (raw custom question responses)
-> Auto-detect "company" and "building" questions by fuzzy matching question text
-> Trigger resolve -> gate -> enrich if new or stale
```

Status change pending -> going triggers enrichment. Going -> cancelled: data stays.

### Ingestion paths

**Webhooks (live):** Luma -> `POST /webhooks/luma` -> enrichment service. Automatic.

**Manual Luma pull (past events):** Agent calls `pull_luma_guests` -> Luma API -> enrichment service. For backfilling.

**CSV upload (non-Luma):** Upload -> `enrich_leads` tool -> enrichment service. Any source.

All three produce identical results.

### After the event

Pull check-ins -> update `checked_in_at` -> update `events_attended` -> detect no-shows.

---

## HubSpot Sync

mint's data model is built for enrichment and intelligence. HubSpot's is built for CRM workflows. They don't need to match. A mapping layer translates enriched lead records to HubSpot properties; the mapping is a config, not an architectural decision.

```
exa_target_vertical       -> lead_vertical (dropdown)
current_tier              -> lead_classification_tier (dropdown)
current_confidence        -> lead_classification_confidence (number)
exa_what_they_do          -> company_description_enriched (multi-line text)
apollo_org_funding_stage  -> funding_stage (dropdown)
apollo_org_employee_count -> employee_count (number)
exa_compute_profile       -> compute_profile (multi-line text)
events_attended           -> events_attended (number)
```

Internal fields like `exa_deep_raw`, `resolution_evidence`, `apollo_person_data` don't push to HubSpot.
