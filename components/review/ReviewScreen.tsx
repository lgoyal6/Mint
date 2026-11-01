"use client";

import { Check, Eye, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  listFlaggedReview,
  listNeedsReview,
  listEvents,
  clearReviewQueues,
  reclassifyLead,
  resolveFlaggedLead,
  resolveIdentityLead,
  waitForJob,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EventRecord, FlaggedLead, NeedsReviewLead } from "@/lib/types";
import { DashboardCard, ErrorBlock, LoadingBlock, tierTone } from "@/components/dashboards/shared";

type ReviewTab = "flagged" | "needs-review";
type NeedsReviewType = "classification" | "resolution" | "all";

type ReviewScreenProps = {
  activeTab: ReviewTab;
  initialEventId?: string;
  needsReviewType: NeedsReviewType;
  onTabChange: (tab: ReviewTab) => void;
  onNeedsReviewTypeChange: (type: NeedsReviewType) => void;
  onOpenLead: (personId: string) => void;
  onReviewChanged: () => void;
};

export function ReviewScreen({
  activeTab,
  initialEventId,
  needsReviewType,
  onTabChange,
  onNeedsReviewTypeChange,
  onOpenLead,
  onReviewChanged,
}: ReviewScreenProps) {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventId, setEventId] = useState(initialEventId ?? "");
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    void listEvents({ refreshLuma: false }).then(setEvents).catch(() => setEvents([]));
  }, []);

  useEffect(() => {
    setEventId(initialEventId ?? "");
  }, [initialEventId]);

  async function clearQueues() {
    const scope = eventId ? events.find((event) => event.id === eventId)?.name ?? "this event" : "all events";
    if (!window.confirm(`Clear every unresolved review item for ${scope}? This marks them handled and removes them from the queues.`)) return;
    setClearing(true);
    try {
      await clearReviewQueues(eventId || undefined);
      onReviewChanged();
      window.location.reload();
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        <DashboardCard>
          <p className="text-xs font-medium uppercase text-stone-400">Human Review</p>
          <h1 className="mt-2 text-2xl font-semibold text-stone-950">Review Queues</h1>
          <p className="mt-1 text-sm text-stone-500">Pick the right company when sources disagree, and double-check leads mint was not sure about.</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <TabButton active={activeTab === "flagged"} onClick={() => onTabChange("flagged")}>
              Flagged affiliations
            </TabButton>
            <TabButton active={activeTab === "needs-review"} onClick={() => onTabChange("needs-review")}>
              Needs review
            </TabButton>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-4">
            <select className="min-w-64 rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm" value={eventId} onChange={(event) => setEventId(event.target.value)}>
              <option value="">All events</option>
              {events.map((event) => <option key={event.id} value={event.id}>{event.name}</option>)}
            </select>
            <Button variant="ghost" className="text-rose-700" onClick={() => void clearQueues()} disabled={clearing}>
              <Trash2 size={15} /> {clearing ? "Clearing…" : `Clear ${eventId ? "this event" : "all"} queues`}
            </Button>
          </div>
        </DashboardCard>

        {activeTab === "flagged" ? (
          <FlaggedQueue eventId={eventId || undefined} onOpenLead={onOpenLead} onReviewChanged={onReviewChanged} />
        ) : (
          <NeedsReviewQueue
            queueType={needsReviewType}
            eventId={eventId || undefined}
            onQueueTypeChange={onNeedsReviewTypeChange}
            onOpenLead={onOpenLead}
            onReviewChanged={onReviewChanged}
          />
        )}
      </div>
    </div>
  );
}

const REVIEW_PAGE_SIZE = 50;

function FlaggedQueue({
  eventId,
  onOpenLead,
  onReviewChanged,
}: {
  eventId?: string;
  onOpenLead: (personId: string) => void;
  onReviewChanged: () => void;
}) {
  const [items, setItems] = useState<FlaggedLead[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function load(offset = 0) {
    if (offset === 0) setIsLoading(true);
    else setIsLoadingMore(true);
    setError(null);
    try {
      const page = await listFlaggedReview({ eventId, limit: REVIEW_PAGE_SIZE, offset });
      setItems((current) => (offset === 0 ? page.items : [...current, ...page.items]));
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load flagged leads.");
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }

  useEffect(() => {
    void load();
  }, [eventId]);

  async function handleResolved(personId: string, message: string) {
    setStatus(message);
    setItems((current) => current.filter((item) => item.person_id !== personId));
    setTotal((current) => Math.max(0, current - 1));
    onReviewChanged();
  }

  function handleBackgroundStatus(message: string) {
    setStatus(message);
    onReviewChanged();
  }

  return (
    <DashboardCard title={`Flagged affiliations${total ? ` (${total})` : ""}`} eyebrow="Company conflicts">
      <p className="-mt-2 mb-4 text-sm text-stone-500">
        Sources disagree about where these people work. Pick the right company so mint can finish researching them.
      </p>
      {status ? <p className="mb-4 rounded-lg bg-accent-soft px-3 py-2 text-sm text-accent">{status}</p> : null}
      {error ? <ErrorBlock message={error} /> : null}
      {isLoading ? (
        <LoadingBlock label="Loading flagged leads" />
      ) : items.length ? (
        <div className="space-y-3">
          {items.map((item) => (
            <FlaggedItem
              key={item.person_id}
              item={item}
              onOpenLead={onOpenLead}
              onResolved={handleResolved}
              onBackgroundStatus={handleBackgroundStatus}
            />
          ))}
          <LoadMoreButton
            shown={items.length}
            total={total}
            isLoading={isLoadingMore}
            onLoadMore={() => load(items.length)}
          />
        </div>
      ) : (
        <p className="text-sm text-stone-500">No flagged leads. You're all caught up.</p>
      )}
    </DashboardCard>
  );
}

function LoadMoreButton({
  shown,
  total,
  isLoading,
  onLoadMore,
}: {
  shown: number;
  total: number;
  isLoading: boolean;
  onLoadMore: () => void;
}) {
  if (shown >= total) return null;
  return (
    <button
      className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-600 transition-colors hover:bg-stone-100 hover:text-stone-900 disabled:opacity-60"
      onClick={onLoadMore}
      disabled={isLoading}
    >
      {isLoading ? "Loading..." : `Load more (${total - shown} remaining)`}
    </button>
  );
}

const DOMAIN_RE = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$/i;

function FlaggedItem({
  item,
  onOpenLead,
  onResolved,
  onBackgroundStatus,
}: {
  item: FlaggedLead;
  onOpenLead: (personId: string) => void;
  onResolved: (personId: string, message: string) => Promise<void>;
  onBackgroundStatus: (message: string) => void;
}) {
  const candidates = item.candidate_companies.filter((candidate) => candidate.domain);
  const recommendation = recommendCandidate(item, candidates);
  const firstCandidate = recommendation.candidate?.domain ?? candidates[0]?.domain ?? "";
  const [mode, setMode] = useState<"candidate" | "manual">(firstCandidate ? "candidate" : "manual");
  const [chosenDomain, setChosenDomain] = useState(firstCandidate);
  const [manualDomain, setManualDomain] = useState("");
  const [manualName, setManualName] = useState("");
  const [isResolving, setIsResolving] = useState(false);
  const [rememberAliases, setRememberAliases] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chain = Array.isArray(item.resolution_evidence?.chain) ? item.resolution_evidence.chain : [];

  const trimmedManual = manualDomain.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  const manualDomainValid = DOMAIN_RE.test(trimmedManual);
  const targetDomain = mode === "manual" ? trimmedManual : chosenDomain;
  const canResolve = mode === "manual" ? manualDomainValid : Boolean(chosenDomain);

  async function resolve() {
    setIsResolving(true);
    setError(null);
    try {
      const payload =
        mode === "manual"
          ? { company_domain: trimmedManual, company_name: manualName.trim() || undefined, remember_aliases: rememberAliases }
          : { chosen_domain: chosenDomain, remember_aliases: rememberAliases };
      const response = await resolveFlaggedLead(item.person_id, payload);
      const domain = response.resolution.domain || targetDomain;
      await onResolved(item.person_id, `Resolved ${item.name || item.email} -> ${domain}. Researching the company in the background...`);
      if (response.job_id) {
        void waitForJob(response.job_id)
          .then(() => onBackgroundStatus(`Finished researching ${domain} — tier updated.`))
          .catch(() => onBackgroundStatus(`Resolved ${domain}, but the background research hit a problem. Open the lead for details.`));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve flagged lead.");
    } finally {
      setIsResolving(false);
    }
  }

  return (
    <article className="rounded-xl border border-stone-200 bg-stone-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-stone-950">{item.name || item.email}</h3>
          <p className="mt-1 text-sm text-stone-600">{item.title || "Title unknown"}</p>
          <p className="mt-1 font-mono text-xs text-stone-400">{item.email}</p>
          {item.event_names?.length ? <p className="mt-2 text-xs font-medium text-accent">{item.event_names.join(" · ")}</p> : null}
          {item.self_reported_company ? (
            <p className="mt-2 text-sm text-stone-700">
              Says they work at <span className="font-medium">{item.self_reported_company}</span>
            </p>
          ) : null}
        </div>
        <Button variant="ghost" className="h-8 px-2" onClick={() => onOpenLead(item.person_id)}>
          <Eye size={15} />
          Detail
        </Button>
      </div>

      <p className="mt-4 text-sm font-medium text-stone-800">
        Which company does {item.name || "this person"} actually work for?
      </p>

      {recommendation.candidate ? (
        <div className="mt-2 rounded-lg border border-accent-line bg-accent-soft/50 p-3">
          <p className="text-xs font-semibold uppercase text-accent">Recommended</p>
          <p className="mt-1 text-sm font-medium text-stone-900">
            {recommendation.candidate.name || recommendation.candidate.domain}
          </p>
          <p className="mt-1 text-xs text-stone-600">{recommendation.reason}</p>
        </div>
      ) : null}

      {mode === "candidate" ? (
        <div className="mt-2 space-y-2">
          {candidates.map((candidate, index) => {
            const domain = candidate.domain || "";
            const selected = chosenDomain === domain;
            return (
              <div
                key={`${domain}-${index}`}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg border bg-canvas p-3 transition-colors",
                  selected ? "border-accent bg-accent-soft/50" : "border-stone-200 hover:border-stone-300",
                )}
              >
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => setChosenDomain(domain)}
                  aria-pressed={selected}
                >
                  <div className="min-w-0">
                    <p className="font-medium text-stone-950">{candidate.name || candidate.domain}</p>
                    <p className="font-mono text-xs text-stone-500">{candidate.domain}</p>
                    <p className="mt-1 text-xs text-stone-500">
                      {[candidate.source, candidate.note].filter(Boolean).join(" · ") || "No supporting note"}
                    </p>
                  </div>
                </button>
                <a
                  href={`https://${domain}`}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 text-xs font-medium text-accent underline-offset-2 hover:underline"
                >
                  Open site
                </a>
                {selected ? <Check size={16} className="shrink-0 text-accent" /> : null}
              </div>
            );
          })}
          <button
            type="button"
            className="text-sm font-medium text-stone-500 underline-offset-2 hover:text-stone-800 hover:underline"
            onClick={() => setMode("manual")}
          >
            None of these — enter the company manually
          </button>
        </div>
      ) : (
        <div className="mt-2 rounded-lg border border-stone-200 bg-canvas p-3">
          <input
            className="w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm outline-none focus:border-accent-line"
            placeholder="Company domain, like acme.com"
            value={manualDomain}
            onChange={(event) => setManualDomain(event.target.value)}
          />
          {manualDomain.trim() && !manualDomainValid ? (
            <p className="mt-1 text-xs text-orange-800">Enter a domain like acme.com (no spaces or paths).</p>
          ) : null}
          <input
            className="mt-2 w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm outline-none focus:border-accent-line"
            placeholder="Company name (optional)"
            value={manualName}
            onChange={(event) => setManualName(event.target.value)}
          />
          {candidates.length ? (
            <button
              type="button"
              className="mt-2 text-sm font-medium text-stone-500 underline-offset-2 hover:text-stone-800 hover:underline"
              onClick={() => setMode("candidate")}
            >
              Back to suggestions
            </button>
          ) : null}
        </div>
      )}

      {candidates.length > 1 ? (
        <label className="mt-3 flex items-start gap-2 text-xs text-stone-600">
          <input
            type="checkbox"
            checked={rememberAliases}
            onChange={(event) => setRememberAliases(event.target.checked)}
            className="mt-0.5"
          />
          These domains belong to the same company. Remember this choice for future leads.
        </label>
      ) : null}

      <div className="mt-3 flex items-center gap-3">
        <Button variant="primary" onClick={resolve} disabled={isResolving || !canResolve}>
          {isResolving ? <RefreshCw size={15} className="animate-spin" /> : <Check size={15} />}
          {targetDomain && canResolve ? `Resolve to ${targetDomain}` : "Resolve"}
        </Button>
        {error ? <p className="text-xs text-orange-800">{error}</p> : null}
      </div>

      {chain.length ? (
        <details className="mt-3 rounded-lg border border-stone-200 bg-canvas p-3">
          <summary className="cursor-pointer text-xs font-medium uppercase text-stone-400">
            How mint got here (evidence)
          </summary>
          <ul className="mt-2 space-y-1 text-sm text-stone-600">
            {chain.map((line, index) => (
              <li key={index}>{String(line)}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </article>
  );
}

function recommendCandidate(
  item: FlaggedLead,
  candidates: FlaggedLead["candidate_companies"],
): { candidate?: FlaggedLead["candidate_companies"][number]; reason: string } {
  const normalize = (value?: string | null) =>
    (value || "").toLowerCase().replace(/\b(inc|llc|ltd|limited|corp|corporation|company|co)\b/g, "").replace(/[^a-z0-9]/g, "");
  const reported = normalize(item.self_reported_company);
  if (reported) {
    const matches = candidates.filter((candidate) => {
      const name = normalize(candidate.name);
      const root = normalize(candidate.domain?.split(".", 1)[0]);
      return reported === name || reported === root || (name.length >= 4 && (reported.includes(name) || name.includes(reported)));
    });
    if (matches.length === 1) {
      return { candidate: matches[0], reason: "Matches the company the attendee reported for this event." };
    }
  }
  const apollo = candidates.find((candidate) => candidate.source === "apollo_org");
  if (apollo) return { candidate: apollo, reason: "Apollo matched this person directly to the employer; verify against LinkedIn before accepting." };
  const email = candidates.find((candidate) => candidate.source === "email_domain");
  if (email) return { candidate: email, reason: "Matches the attendee's work-email domain; confirm it is not a personal or secondary domain." };
  return { candidate: candidates[0], reason: "Best available candidate. Confirm using the evidence and current LinkedIn employment." };
}

function NeedsReviewQueue({
  queueType,
  eventId,
  onQueueTypeChange,
  onOpenLead,
  onReviewChanged,
}: {
  queueType: NeedsReviewType;
  eventId?: string;
  onQueueTypeChange: (type: NeedsReviewType) => void;
  onOpenLead: (personId: string) => void;
  onReviewChanged: () => void;
}) {
  const [items, setItems] = useState<NeedsReviewLead[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function load(offset = 0) {
    if (offset === 0) setIsLoading(true);
    else setIsLoadingMore(true);
    setError(null);
    try {
      const page = await listNeedsReview({ type: queueType, eventId, limit: REVIEW_PAGE_SIZE, offset });
      setItems((current) => (offset === 0 ? page.items : [...current, ...page.items]));
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load review leads.");
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }

  useEffect(() => {
    void load();
  }, [queueType, eventId]);

  async function handleReclassified(message: string) {
    setStatus(message);
    await load();
    onReviewChanged();
  }

  return (
    <DashboardCard title={`Needs review${total ? ` (${total})` : ""}`} eyebrow="Second look">
      <p className="-mt-2 mb-4 text-sm text-stone-500">
        mint wasn't confident about these calls. Confirm the tier if it looks right, or change it.
      </p>
      <div className="mb-4 flex flex-wrap gap-2">
        {NEEDS_REVIEW_FILTERS.map(({ value, label }) => (
          <TabButton key={value} active={queueType === value} onClick={() => onQueueTypeChange(value)}>
            {label}
          </TabButton>
        ))}
      </div>
      {status ? <p className="mb-4 rounded-lg bg-accent-soft px-3 py-2 text-sm text-accent">{status}</p> : null}
      {error ? <ErrorBlock message={error} /> : null}
      {isLoading ? (
        <LoadingBlock label="Loading needs-review leads" />
      ) : items.length ? (
        <div className="space-y-3">
          {items.map((item) => (
            <NeedsReviewItem
              key={`${item.source}-${item.person_id}`}
              item={item}
              onOpenLead={onOpenLead}
              onReclassified={handleReclassified}
            />
          ))}
          <LoadMoreButton
            shown={items.length}
            total={total}
            isLoading={isLoadingMore}
            onLoadMore={() => load(items.length)}
          />
        </div>
      ) : (
        <p className="text-sm text-stone-500">No leads need review for this filter. You're all caught up.</p>
      )}
    </DashboardCard>
  );
}

const NEEDS_REVIEW_FILTERS: Array<{ value: NeedsReviewType; label: string }> = [
  { value: "all", label: "All" },
  { value: "classification", label: "Tier calls" },
  { value: "resolution", label: "Identity calls" },
];

function NeedsReviewItem({
  item,
  onOpenLead,
  onReclassified,
}: {
  item: NeedsReviewLead;
  onOpenLead: (personId: string) => void;
  onReclassified: (message: string) => Promise<void>;
}) {
  const tierOptions = ["P0", "P1", "P2", "NR", "NP"] as const;
  const currentTier = (item.tier as (typeof tierOptions)[number]) || null;
  const [tier, setTier] = useState<(typeof tierOptions)[number]>(currentTier || "P1");
  const [reason, setReason] = useState("");
  const [companyDomain, setCompanyDomain] = useState(item.resolved_domain || "");
  const [companyName, setCompanyName] = useState(item.resolved_company || "");
  const [refreshPerson, setRefreshPerson] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isConfirming = currentTier !== null && tier === currentTier;

  async function reclassify() {
    setIsSaving(true);
    setError(null);
    try {
      const finalReason = reason.trim() || (isConfirming ? "Confirmed manually." : "Reviewed manually.");
      await reclassifyLead(item.person_id, { new_tier: tier, reason: finalReason, reviewed_by: "mint-ui" });
      await onReclassified(
        isConfirming
          ? `Confirmed ${item.name || item.email} as ${tier}.`
          : `Changed ${item.name || item.email} to ${tier}.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reclassify lead.");
    } finally {
      setIsSaving(false);
    }
  }

  async function resolveIdentity() {
    const domain = companyDomain.trim();
    if (!domain) {
      setError("Enter the person's current company domain.");
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const response = await resolveIdentityLead(item.person_id, {
        company_domain: domain,
        company_name: companyName.trim() || undefined,
        refresh_person: refreshPerson,
      });
      if (response.job_id) await waitForJob(response.job_id);
      await onReclassified(`Resolved ${item.name || item.email} to ${domain}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve identity.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <article className="rounded-xl border border-stone-200 bg-stone-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold text-stone-950">{item.name || item.email}</h3>
            {item.tier ? <Badge tone={tierTone(item.tier)}>{item.tier}</Badge> : null}
            {item.primary_product ? <Badge tone="accent">{item.primary_product}</Badge> : null}
            <Badge>{item.source}</Badge>
          </div>
          <p className="mt-1 text-sm text-stone-600">{item.title || "Title unknown"}</p>
          <p className="mt-1 font-mono text-xs text-stone-400">{item.email}</p>
          {item.event_names?.length ? <p className="mt-2 text-xs font-medium text-accent">{item.event_names.join(" · ")}</p> : null}
          <p className="mt-2 text-sm text-stone-700">{item.resolved_company || "Company unknown"}</p>
          <p className="font-mono text-xs text-stone-400">{item.resolved_domain || ""}</p>
        </div>
        <Button variant="ghost" className="h-8 px-2" onClick={() => onOpenLead(item.person_id)}>
          <Eye size={15} />
          Detail
        </Button>
      </div>

      <div className="mt-4 rounded-lg border border-stone-200 bg-canvas p-3">
        <p className="text-xs font-medium uppercase text-stone-400">Why it needs a second look</p>
        <p className="mt-1 text-sm leading-6 text-stone-700">{item.reason}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <ReviewFact label="Confidence" value={item.confidence ?? "Unknown"} />
          <ReviewFact label="Review status" value={item.review_status || "Unknown"} />
          <ReviewFact label="Resolution" value={item.resolution_confidence || "Unknown"} />
        </div>
      </div>

      {item.source === "classification" ? (
        <div className="mt-4 rounded-lg border border-stone-200 bg-canvas p-3">
          <p className="text-xs font-medium uppercase text-stone-400">Your call</p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <div className="flex rounded-lg border border-stone-200 bg-stone-50 p-1">
              {tierOptions.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    tier === option ? "bg-white text-stone-950 shadow-hairline" : "text-stone-500 hover:text-stone-900",
                  )}
                  onClick={() => setTier(option)}
                  aria-pressed={tier === option}
                >
                  {option}
                  {currentTier === option ? <span className="ml-1 text-xs text-stone-400">(current)</span> : null}
                </button>
              ))}
            </div>
            <input
              className="min-w-[200px] flex-1 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm outline-none focus:border-accent-line"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={isConfirming ? "Optional note" : "Why are you changing it? (optional)"}
            />
            <Button variant="primary" onClick={reclassify} disabled={isSaving}>
              {isSaving ? <RefreshCw size={15} className="animate-spin" /> : <Check size={15} />}
              {isConfirming ? `Confirm ${tier}` : `Change to ${tier}`}
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-lg border border-stone-200 bg-canvas p-3">
          <p className="text-xs font-medium uppercase text-stone-400">Resolve identity</p>
          <p className="mt-1 text-sm text-stone-600">
            Confirm the company this person currently works for. Mint will mark the affiliation verified, enrich the
            company, and reclassify the lead.
          </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <input
              className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm outline-none focus:border-accent-line"
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              placeholder="Company name (optional)"
            />
            <input
              className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 font-mono text-sm outline-none focus:border-accent-line"
              value={companyDomain}
              onChange={(event) => setCompanyDomain(event.target.value)}
              placeholder="company.com"
            />
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm text-stone-700">
            <input
              type="checkbox"
              checked={refreshPerson}
              onChange={(event) => setRefreshPerson(event.target.checked)}
            />
            Refresh person from Apollo to update title and LinkedIn URL (uses a credit when Apollo matches)
          </label>
          <div className="mt-3 flex justify-end">
            <Button variant="primary" onClick={resolveIdentity} disabled={isSaving || !companyDomain.trim()}>
              {isSaving ? <RefreshCw size={15} className="animate-spin" /> : <Check size={15} />}
              Confirm identity
            </Button>
          </div>
        </div>
      )}
      {error ? <p className="mt-2 text-xs text-orange-800">{error}</p> : null}
    </article>
  );
}

function ReviewFact({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
      <p className="mt-1 text-sm text-stone-800">{value}</p>
    </div>
  );
}

function TabButton({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      className={cn(
        "rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "border-accent-line bg-accent-soft text-accent"
          : "border-stone-200 bg-stone-50 text-stone-600 hover:bg-stone-100",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
