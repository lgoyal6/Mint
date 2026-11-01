# Growth ICP and MQL Analysis Notes

This note captures the working GTM framing for Mint: what GMI Cloud sells, how to think about lead quality, how the current ICP should evolve, and how to use Mint for MQL analysis.

## What GMI Cloud Does

GMI Cloud sells AI infrastructure. In simple terms, it gives AI companies access to the computing power needed to build, run, and scale AI products.

AI workloads need specialized hardware because models do a large amount of parallel math. GPUs are the hardware used for this. A GPU cluster is a group of GPUs connected together so they can handle larger jobs such as model training, fine-tuning, high-volume inference, or video generation.

GMI's product surface can be thought of in three main buckets:

- **MaaS / Model-as-a-Service:** API access to models for inference. Useful for AI apps, agents, copilots, summarization, document processing, image/video/audio generation, and other model-backed features.
- **Dedicated Endpoints:** Reserved or private inference capacity for production AI workloads. Useful when customers need predictable latency, throughput, privacy, concurrency, or SLA-like behavior.
- **GPU Clusters:** Bare-metal or managed GPU infrastructure for training, fine-tuning, batch generation, and heavier custom workloads.

These products are similar to parts of CoreWeave, Lambda, Together AI, Fireworks, Replicate, Baseten, Modal, RunPod, and hyperscalers like AWS/GCP/Azure. GMI's angle is an AI-native cloud that can support the path from easy model APIs to dedicated production inference and larger GPU infrastructure.

## Why This Matters Now

AI companies are moving from prototypes to production. That creates several pain points:

- model API costs rise quickly;
- latency and reliability start mattering;
- GPU availability is constrained;
- teams need private or reserved capacity;
- startups need credits and hands-on help to get to production;
- enterprises want controlled deployments and predictable infrastructure.

This makes AI infrastructure a GTM category with both developer-led and sales-led motions.

## Growth, BD, and Customer Motions

GMI can grow through several motions:

- **SCALE / startup motion:** find AI-native startups early, offer credits or GPU vouchers, help them activate, then convert usage into paid workloads.
- **MaaS self-serve motion:** attract developers building AI products who need model APIs and fast deployment.
- **Dedicated endpoint motion:** convert teams with production inference volume, latency pain, or privacy requirements.
- **Enterprise GPU motion:** sell reserved GPU capacity or clusters to companies with training, fine-tuning, or high-throughput workloads.
- **Partner/channel motion:** work with model providers, accelerators, VCs, resellers, public-sector partners, and enterprise integrators.

The best growth questions are:

- What is the customer building?
- Are they training, fine-tuning, serving inference, or all three?
- What models or providers do they use today?
- What is their current or expected AI infra spend?
- Do they have latency, cost, rate-limit, privacy, or capacity pain?
- Who owns the technical and budget decision?
- Is the next action credits, BD outreach, technical onboarding, or nurture?

## Current Mint ICP

The current Mint ICP in `config/icp.yaml` uses:

- `P0`: sales-ready / highest priority;
- `P1`: strong MQL;
- `P2`: potential lead;
- `NR`: nurture relationship for now;
- `NP`: legacy not-a-prospect tier for older records.

It evaluates:

- product fit for MaaS, Dedicated Endpoints, or GPU Clusters;
- target vertical fit;
- buyer/person fit;
- confidence and evidence quality;
- gates such as competitor, big tech, academia, government, nonprofit, healthcare, consulting, and VC/fund categories.

Current target verticals:

- research labs;
- coding platforms;
- agentic;
- data labeling;
- robotics;
- video AI / multimodal.

This is a strong first pass for AI startup events, especially because Mint resolves person-company affiliation before spending enrichment and classification work.

## Proposed ICP Evolution

The current ICP answers: "Is this lead P0, P1, P2, or NR?"

A stronger growth ICP should answer: "How strong is this lead, which GTM motion does it belong to, which product fits, and what should we do next?"

Recommended output fields:

- `priority_tier`: `P0`, `P1`, `P2`, or `NR`
- `gtm_motion`: `SCALE`, `MaaS`, `Dedicated Endpoint`, `Enterprise GPU`, `Partner`, or `Nurture`
- `product_fit`: `MaaS`, `Dedicated`, `GPU Cluster`, or multiple
- `next_action`: the recommended GTM action

### Priority Tiers

`P0` means act now.

Typical P0:

- clear GMI product fit;
- target vertical or very strong adjacent vertical;
- founder, exec, infra/ML buyer, or strong senior technical influencer;
- current or near-term workload;
- grounded evidence from Lu.ma, Apollo, Exa, website data, or self-reported answers.

`P1` means qualified, but needs review, sequencing, or a better route.

Typical P1:

- real product fit, but not enough urgency for P0;
- junior or unclear buyer at a strong company;
- strong company but thin evidence;
- adjacent vertical;
- unclear current infra pain;
- affiliation is unverified or needs review.

`P2` means potential lead / nurture.

Typical P2:

- early/pre-product founder;
- student founder with a real AI project;
- junior IC at a target company;
- VC, accelerator, or community contact;
- weak but plausible AI compute relevance;
- possible future SCALE candidate;
- useful relationship, but not sales-ready.

`NP` means no meaningful GMI fit right now.

Typical NP:

- no AI product or workload;
- non-buyer at irrelevant company;
- invalid or contradicted affiliation;
- competitor with no partnership angle;
- generic consulting/agency lead with no internal compute need;
- sales, recruiting, or marketing role with no path to infrastructure decision.

### Motions

Priority and motion should be separate. `P0` should not mean SCALE, and `P1` should not mean enterprise. A SCALE lead can be weak or strong. An enterprise lead can be weak or strong.

Examples:

- `P0 + SCALE + MaaS`: founder of an AI video startup with an active product and immediate inference need.
- `P1 + SCALE + GPU Cluster`: early robotics founder with plausible training need but thin evidence.
- `P0 + Enterprise GPU + GPU Cluster`: head of ML at a company actively looking for H100/H200 capacity.
- `P1 + Dedicated Endpoint`: senior engineer at a production AI company with clear workload but unclear buying authority.
- `P2 + Nurture + MaaS`: student founder experimenting with agents, not ready yet.

### Expanded Verticals

The current target verticals are good but likely too narrow. Consider adding:

- generative media;
- synthetic data;
- voice AI;
- legal AI;
- enterprise AI applications;
- cybersecurity AI;
- AI infrastructure tools;
- model providers.

### Motion-Specific Gates

Some current hard gates should become motion-specific.

For SCALE, big tech, government, academia, and large enterprises are usually poor fits.

For enterprise, partner, or public-sector motions, those same categories may be valid if there is a real workload or channel opportunity. For example:

- sovereign AI / public-sector partner;
- enterprise GPU workload;
- research lab with compute budget;
- accelerator or VC platform partner.

## How To Use Mint For MQL Analysis

Mint can be used for MQL analysis by turning event attendees or uploaded lead lists into tiered leads.

Basic flow:

1. Pull a Lu.ma event or upload a CSV/XLSX list.
2. Mint normalizes attendee data.
3. Mint resolves each person and their likely company.
4. Mint gates on affiliation confidence.
5. Mint enriches people/companies with Apollo and Exa.
6. Mint classifies leads as `P0`, `P1`, `P2`, or `NR` today.
7. Review flagged or low-confidence leads.
8. Export P0/P1 leads for BD follow-up.

Useful Mint actions:

- list events;
- pull event;
- classify event;
- list leads by tier;
- inspect a lead;
- list flagged leads;
- list needs-review leads;
- reclassify leads manually;
- export leads to CSV/XLSX;
- view event summary and pipeline metrics.

For event MQL analysis, the practical report should include:

- total registered;
- total checked in;
- number of enriched leads;
- P0 count;
- P1 count;
- P2 count;
- NR count;
- legacy NP count, if present;
- MQL conversion rate;
- top companies;
- top verticals;
- product fit split: MaaS vs Dedicated vs GPU;
- review queue size;
- recommended BD actions.

## What Works Without `GMI_API_KEY`

Mint can boot without `GMI_API_KEY`.

Without it:

- the backend can run;
- Lu.ma ingestion can run if `LUMA_API_KEY` is present;
- Apollo/Exa enrichment can run if those keys are present;
- storage, review pages, exports, and dashboards can still be used.

But these are limited or disabled:

- chat agent;
- LLM-backed P0/P1/P2/NR classification quality;
- auto-review;
- model-generated reasoning.

The classifier has conservative fallback behavior when no LLM client is configured. It can mark leads as unavailable/low-confidence rather than producing the intended GMI-specific classification.

## What Can Replace `GMI_API_KEY`

There is no drop-in replacement unless the provider supports the same API shape currently used by the app:

- base URL like `GMI_BASE_URL`;
- bearer-token auth;
- `/v1/messages` endpoint;
- Anthropic-style request/response format.

Do not put an OpenAI, Anthropic, Gemini, or OpenRouter key into `GMI_API_KEY` unless the code is changed or the provider is made API-compatible with the current client.

Possible paths:

- **Best:** get a standard GMI MaaS API key.
- **Temporary:** run Mint without `GMI_API_KEY` and use manual review/enrichment only.
- **Code-change option:** add an alternate LLM provider client for OpenAI, Anthropic, OpenRouter, Gemini, etc.
- **Compatibility option:** point `GMI_BASE_URL` to a compatible Anthropic-style proxy if one exists and is approved.

## How To Ask For The GMI API Key

Use precise terms. The request is for a MaaS inference API key, not an admin key.

Suggested request:

> I am a GTM engineering intern setting up an internal event lead qualification tool for the GTM workflow. I need API access for:
>
> - Apollo
> - Exa
> - Lu.ma
> - GMI MaaS
>
> The tool pulls event attendees from Lu.ma, enriches people and companies through Apollo and Exa, and uses GMI MaaS model inference to classify MQL quality and review edge cases. The GMI key is for model calls to the `/v1/messages` endpoint using bearer-token authentication. It does not need admin access; a standard MaaS API key with access to the configured Anthropic models should be enough.
>
> The deployment is already set up. These keys are the remaining blockers to run the full MQL pipeline.

If they ask which GMI models, use:

- `GMI_CLASSIFIER_MODEL=anthropic/claude-haiku-4.5`
- `GMI_AGENT_MODEL=anthropic/claude-sonnet-4-6`
- `GMI_REVIEWER_MODEL=anthropic/claude-opus-4.5`

If those exact models are unavailable, ask for equivalent MaaS chat/message models that support JSON output and tool/agent reasoning.

## Near-Term Mint Improvements

Recommended product changes:

- keep `P2` and `NR` as first-class tiers;
- add `gtm_motion`;
- add `next_action`;
- update `config/icp.yaml` with expanded verticals;
- make gates motion-specific;
- add reporting by event: P0/P1/P2/NR, product fit, and MQL conversion;
- preserve manual reclassification and review reasons;
- eventually move long Lu.ma sync/classification work behind a durable worker queue.
