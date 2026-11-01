"use client";

import { ArrowRight, CalendarDays, Download, MapPin, MessageSquare, RefreshCw, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { apiBase, backfillEvent, backfillEventRegions, exportEventLeads, getEventSummary, listEvents, retryUnresolvedEvent, syncEventToHubSpot, updateEventEnrichment } from "@/lib/api";
import { humanizeLeadErrors } from "@/lib/leadErrors";
import { cn } from "@/lib/utils";
import type { EventRecord, EventSource, EventSummary, IngestSummary } from "@/lib/types";
import { EventScorecardCard } from "./EventScorecardCard";
import {
  BreakdownList,
  DashboardCard,
  ErrorBlock,
  formatNumber,
  LeadTable,
  LoadingBlock,
  StatButton,
} from "./shared";

type EventDashboardProps = {
  refreshVersion: number;
  onOpenLead: (personId: string) => void;
  onOpenReview: (tab: "flagged" | "needs-review", eventId?: string) => void;
  onOpenEventChat: (eventId: string, eventName: string) => void;
};

// Module-level stale-while-revalidate caches: the dashboard unmounts whenever the
// user switches screens, so remember the last response and paint it immediately
// while a background refresh runs.
const eventsCache = new Map<EventSource, EventRecord[]>();
const summaryCache = new Map<string, EventSummary>();

export function EventDashboard({ refreshVersion, onOpenLead, onOpenReview, onOpenEventChat }: EventDashboardProps) {
  const [events, setEvents] = useState<EventRecord[]>(() => eventsCache.get("all") ?? []);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(
    () => eventsCache.get("all")?.[0]?.id ?? null,
  );
  const [sourceFilter, setSourceFilter] = useState<EventSource>("all");
  const [summary, setSummary] = useState<EventSummary | null>(null);
  const [isLoadingEvents, setIsLoadingEvents] = useState(true);
  const [isLoadingSummary, setIsLoadingSummary] = useState(false);
  const [updatingEventIds, setUpdatingEventIds] = useState<Set<string>>(() => new Set());
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [backfillResult, setBackfillResult] = useState<IngestSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let loadedStoredEvents = false;
    const cached = eventsCache.get(sourceFilter);
    const shouldRefreshLuma = sourceFilter !== "csv";
    if (cached) {
      setEvents(cached);
      setSelectedEventId((current) => {
        if (current && cached.some((event) => event.id === current)) return current;
        return cached[0]?.id ?? null;
      });
    }
    setIsLoadingEvents(!cached);
    listEvents({ source: sourceFilter, refreshLuma: false })
      .then((value) => {
        loadedStoredEvents = true;
        eventsCache.set(sourceFilter, value);
        if (cancelled) return;
        setEvents(value);
        setSelectedEventId((current) => {
          if (current && value.some((event) => event.id === current)) return current;
          return value[0]?.id ?? null;
        });
        setIsLoadingEvents(false);
        if (!shouldRefreshLuma) return null;
        return listEvents({ source: sourceFilter, refreshLuma: true });
      })
      .then((value) => {
        if (!value) return;
        eventsCache.set(sourceFilter, value);
        if (cancelled) return;
        setEvents(value);
        setSelectedEventId((current) => {
          if (current && value.some((event) => event.id === current)) return current;
          return value[0]?.id ?? null;
        });
      })
      .catch((err) => {
        if (!cancelled && !loadedStoredEvents) {
          setError(err instanceof Error ? err.message : "Could not load events.");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingEvents(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceFilter, refreshVersion, retryVersion]);

  useEffect(() => {
    if (!selectedEventId) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    const cached = summaryCache.get(selectedEventId);
    if (cached) setSummary(cached);
    setIsLoadingSummary(!cached);
    setError(null);
    getEventSummary(selectedEventId)
      .then((value) => {
        summaryCache.set(selectedEventId, value);
        if (!cancelled) setSummary(value);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load event summary.");
      })
      .finally(() => {
        if (!cancelled) setIsLoadingSummary(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedEventId, refreshVersion, retryVersion]);

  const selectedEvent = useMemo(
    () => events.find((event) => event.id === selectedEventId) ?? null,
    [events, selectedEventId],
  );

  async function toggleEventEnrichment(event: EventRecord, enabled: boolean) {
    setError(null);
    setUpdatingEventIds((current) => new Set(current).add(event.id));
    try {
      const updated = await updateEventEnrichment(event.id, enabled);
      for (const [source, cachedEvents] of eventsCache) {
        eventsCache.set(
          source,
          cachedEvents.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
        );
      }
      setEvents((current) =>
        current.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
      );
      if (enabled) {
        setJobStatus(`Backfilling enrichment for ${event.name}...`);
        setBackfillResult(null);
        const result = await backfillEvent(event.id, true, (progress) =>
          setJobStatus(progress?.message ?? `Backfilling enrichment for ${event.name}...`),
        );
        setBackfillResult(result);
        setJobStatus(`Backfill finished for ${event.name}.`);
        setSelectedEventId(event.id);
        const nextSummary = await getEventSummary(event.id);
        summaryCache.set(event.id, nextSummary);
        setSummary(nextSummary);
      } else {
        setJobStatus(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update event enrichment.");
    } finally {
      setUpdatingEventIds((current) => {
        const next = new Set(current);
        next.delete(event.id);
        return next;
      });
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-hidden p-5">
      <div className="mx-auto grid h-full max-w-6xl min-h-0 gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <DashboardCard title="Events" eyebrow="Source" className="flex min-h-0 flex-col overflow-hidden" contentClassName="flex min-h-0 flex-1 flex-col">
          <div className="mb-3 flex rounded-lg border border-stone-200 bg-stone-50 p-1">
            {EVENT_SOURCE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={cn(
                  "flex-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
                  sourceFilter === option.value
                    ? "bg-white text-stone-950 shadow-hairline"
                    : "text-stone-500 hover:text-stone-900",
                )}
                onClick={() => setSourceFilter(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          {isLoadingEvents ? (
            <LoadingBlock label="Loading events" />
          ) : events.length ? (
            <div className="scrollbar-subtle min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
              {events.map((event) => (
                <div
                  key={event.id}
                  className={cn(
                    "overflow-hidden rounded-xl border transition-colors",
                    selectedEventId === event.id
                      ? "border-accent-line bg-accent-soft/70"
                      : "border-stone-200 bg-stone-50 hover:border-stone-300",
                  )}
                >
                  <button className="w-full p-3 text-left" onClick={() => setSelectedEventId(event.id)}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-stone-950">{event.name}</p>
                        <p className="mt-1 text-xs text-stone-500">{formatDate(event.event_date)}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        {event.status ? <Badge>{event.status}</Badge> : null}
                        {event.source ? <Badge tone="accent">{event.source}</Badge> : null}
                      </div>
                    </div>
                  </button>
                  <div className="flex items-center justify-between border-t border-stone-200/70 px-3 py-2">
                    <span className="text-xs font-medium text-stone-500">Enrichment</span>
                    <EventEnrichmentSwitch
                      enabled={event.enrichment_enabled !== false}
                      isLoading={updatingEventIds.has(event.id)}
                      onToggle={(enabled) => toggleEventEnrichment(event, enabled)}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-stone-500">No events match this source.</p>
          )}
        </DashboardCard>

        <div className="scrollbar-subtle min-h-0 space-y-5 overflow-y-auto pr-1">
          {error ? (
            <ErrorBlock
              message={error}
              onRetry={() => {
                setError(null);
                setRetryVersion((version) => version + 1);
              }}
            />
          ) : null}
          {jobStatus ? (
            <DashboardCard>
              <p className="text-sm text-stone-600">{jobStatus}</p>
            </DashboardCard>
          ) : null}
          {backfillResult ? <BackfillIssuesCard result={backfillResult} /> : null}
          {isLoadingSummary ? (
            <LoadingBlock label="Loading event summary" />
          ) : summary ? (
            <EventSummaryView
              summary={summary}
              onOpenLead={onOpenLead}
              onOpenReview={onOpenReview}
              onOpenEventChat={onOpenEventChat}
              onSummaryUpdated={(nextSummary) => {
                summaryCache.set(nextSummary.event.id, nextSummary);
                setSummary(nextSummary);
              }}
            />
          ) : selectedEvent ? (
            <DashboardCard>
              <p className="text-sm text-stone-500">Select an event to inspect its funnel and lead list.</p>
            </DashboardCard>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function BackfillIssuesCard({ result }: { result: IngestSummary }) {
  const leadErrors = result.lead_errors ?? [];
  const classificationErrors = result.classification?.errors ?? [];
  if (!leadErrors.length && !classificationErrors.length) return null;

  return (
    <DashboardCard title="Leads we couldn't fully enrich">
      <details>
        <summary className="cursor-pointer text-sm text-stone-600">
          {leadErrors.length + classificationErrors.length} problem
          {leadErrors.length + classificationErrors.length === 1 ? "" : "s"} during the last backfill. Open for details.
        </summary>
        <ul className="mt-3 space-y-2 text-sm text-stone-700">
          {leadErrors.map((lead) => (
            <li key={lead.email} className="flex flex-wrap items-center gap-2">
              <Badge tone="warn">enrichment</Badge>
              <span className="font-mono text-xs text-stone-500">{lead.email}</span>
              <span>{humanizeLeadErrors(lead.errors).join("; ")}</span>
            </li>
          ))}
          {classificationErrors.map((message, index) => (
            <li key={`classification-${index}`} className="flex flex-wrap items-center gap-2">
              <Badge tone="warn">classification</Badge>
              <span>{message}</span>
            </li>
          ))}
        </ul>
      </details>
    </DashboardCard>
  );
}

function EventEnrichmentSwitch({
  enabled,
  isLoading,
  onToggle,
}: {
  enabled: boolean;
  isLoading: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={isLoading}
      className={cn(
        "inline-flex h-6 w-11 items-center rounded-full border p-0.5 transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-canvas",
        enabled ? "border-accent bg-accent" : "border-stone-300 bg-stone-200",
        isLoading && "opacity-60",
      )}
      onClick={() => onToggle(!enabled)}
    >
      <span
        className={cn(
          "block h-4 w-4 rounded-full bg-white shadow-hairline transition-transform",
          enabled ? "translate-x-5" : "translate-x-0",
        )}
      />
      <span className="sr-only">{enabled ? "Disable enrichment" : "Enable enrichment"}</span>
    </button>
  );
}

const EVENT_SOURCE_OPTIONS: Array<{ value: EventSource; label: string }> = [
  { value: "all", label: "All" },
  { value: "luma", label: "Luma" },
  { value: "csv", label: "CSV" },
];

function EventSummaryView({
  summary,
  onOpenLead,
  onOpenReview,
  onOpenEventChat,
  onSummaryUpdated,
}: {
  summary: EventSummary;
  onOpenLead: (personId: string) => void;
  onOpenReview: (tab: "flagged" | "needs-review", eventId?: string) => void;
  onOpenEventChat: (eventId: string, eventName: string) => void;
  onSummaryUpdated: (summary: EventSummary) => void;
}) {
  const [action, setAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [hasMissingRegions, setHasMissingRegions] = useState(false);

  async function handleExport(format: "xlsx" | "csv") {
    setActionError(null);
    setAction(`Preparing ${format.toUpperCase()} export…`);
    try {
      const artifact = await exportEventLeads(summary.event.id, { format, profile: "mql" });
      window.location.assign(`${apiBase()}${artifact.download_url}`);
      setAction(`Export ready: ${artifact.row_count ?? 0} rows`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not export leads.");
      setAction(null);
    }
  }

  async function handleHubSpotSync() {
    if (!window.confirm("Sync approved P0/P1 leads to HubSpot? This creates or updates contacts and a list.")) return;
    setActionError(null);
    setAction("Syncing approved P0/P1 leads to HubSpot…");
    try {
      const result = await syncEventToHubSpot(summary.event.id);
      setAction(`HubSpot sync complete${result.imported != null ? `: ${result.imported} contacts` : ""}.`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not sync to HubSpot.");
      setAction(null);
    }
  }

  async function handleRegionBackfill() {
    setActionError(null);
    setAction("Finding attendees missing location data…");
    try {
      const result = await backfillEventRegions(
        summary.event.id,
        (progress) => setAction(progress?.message ?? "Backfilling missing regions…"),
        hasMissingRegions,
      );
      setHasMissingRegions(result.still_missing > 0);
      setAction(
        result.candidates
          ? `Region backfill complete: ${result.regions_found} found, ${result.still_missing} still missing. Export MQLs again for the updated regional tabs.`
          : "Everyone already has a region, or unresolved rows were already checked.",
      );
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not backfill regions.");
      setAction(null);
    }
  }

  async function handleRetryUnresolved() {
    if (!window.confirm("Retry only unresolved, flagged, and temporarily failed leads? This uses Apollo/Exa for identity gaps, then reclassifies that subset.")) return;
    setActionError(null);
    setAction("Finding unresolved leads…");
    try {
      const result = await retryUnresolvedEvent(
        summary.event.id,
        (progress) => setAction(progress?.message ?? "Retrying unresolved leads…"),
      );
      const nextSummary = await getEventSummary(summary.event.id);
      setAction(
        result.retry_candidates
          ? `Retry complete: ${result.reenrichment_candidates ?? 0} re-enriched and ${result.reclassified_count ?? 0} reclassified.`
          : "No sparse or temporarily failed leads remain.",
      );
      onSummaryUpdated(nextSummary);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not retry unresolved leads.");
      setAction(null);
    }
  }

  return (
    <>
      <DashboardCard>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium uppercase text-stone-400">
              <CalendarDays size={14} />
              Event Dashboard
            </div>
            <h1 className="mt-2 text-2xl font-semibold text-stone-950">{summary.event.name}</h1>
            <p className="mt-1 text-sm text-stone-500">{summary.event.luma_event_id || summary.event.status || "Manual event"}</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-2 text-xs font-medium text-stone-700 hover:border-stone-300" onClick={() => onOpenEventChat(summary.event.id, summary.event.name)}>
              <MessageSquare size={14} /> Event chat
            </button>
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-2 text-xs font-medium text-stone-700 hover:border-stone-300" onClick={() => void handleExport("xlsx")}>
              <Download size={14} /> Export MQLs
            </button>
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-2 text-xs font-medium text-stone-700 hover:border-stone-300" onClick={() => void handleRegionBackfill()}>
              <MapPin size={14} /> {hasMissingRegions ? "Retry missing regions" : "Backfill regions"}
            </button>
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-2 text-xs font-medium text-stone-700 hover:border-stone-300" onClick={() => void handleRetryUnresolved()}>
              <RefreshCw size={14} /> Retry unresolved leads
            </button>
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-2.5 py-2 text-xs font-medium text-stone-700 hover:border-stone-300" onClick={handleHubSpotSync}>
              <Upload size={14} /> Sync HubSpot
            </button>
            {summary.event.status ? <Badge>{summary.event.status}</Badge> : null}
          </div>
        </div>
        {action ? <p className="mt-3 text-xs text-stone-500">{action}</p> : null}
        {actionError ? <p className="mt-3 text-xs text-rose-700">{actionError}</p> : null}
      </DashboardCard>

      <DashboardCard title="Resolution Funnel" eyebrow="Actionability">
        <div className="grid gap-3 md:grid-cols-5">
          <FunnelStep label="Guests" value={summary.counts.total_guests} />
          <FunnelStep label="Going" value={summary.counts.going_count} />
          <FunnelStep label="Resolved" value={summary.counts.resolved} />
          <FunnelStep label="Classified" value={summary.counts.classified} />
          <div className="grid grid-cols-2 gap-2 md:grid-cols-1">
            <StatButton
              label="Low confidence"
              value={summary.counts.parked_low}
              sublabel="Needs a human look"
              onClick={() => onOpenReview("needs-review", summary.event.id)}
            />
            <StatButton
              label="Flagged"
              value={summary.counts.flagged}
              sublabel="Sources disagree"
              onClick={() => onOpenReview("flagged", summary.event.id)}
            />
          </div>
        </div>
      </DashboardCard>

      <EventScorecardCard eventId={summary.event.id} />

      <div className="grid gap-5 lg:grid-cols-2">
        <DashboardCard title="Tier Breakdown">
          <div className="grid grid-cols-5 gap-2">
            <StatButton label="P0" value={formatNumber(summary.tier_breakdown.P0)} />
            <StatButton label="P1" value={formatNumber(summary.tier_breakdown.P1)} />
            <StatButton label="P2" value={formatNumber(summary.tier_breakdown.P2)} />
            <StatButton label="NR" value={formatNumber(summary.tier_breakdown.NR)} />
            <StatButton label="NP" value={formatNumber(summary.tier_breakdown.NP)} />
          </div>
          <p className="mt-3 text-xs text-stone-500">
            P0 = sales-ready · P1 = strong MQL · P2 = potential · NR = nurture
          </p>
        </DashboardCard>
        <DashboardCard title="Product Fit">
          <BreakdownList values={summary.product_fit_breakdown} />
        </DashboardCard>
        <DashboardCard title="Qualification outcomes">
          <BreakdownList
            values={summary.qualification_outcome_breakdown ?? summary.np_trigger_breakdown}
            emptyLabel="No ICP rejection reasons for this event."
          />
        </DashboardCard>
        <DashboardCard title="Insufficient evidence">
          <BreakdownList
            values={summary.evidence_gap_breakdown ?? {}}
            emptyLabel="No leads are waiting on better identity or company evidence."
          />
        </DashboardCard>
        <DashboardCard title="Processing failures">
          <BreakdownList
            values={summary.processing_failure_breakdown ?? {}}
            emptyLabel="No provider or classifier failures remain."
          />
          <p className="mt-3 text-xs text-stone-500">These are not ICP rejections. Retry them before exporting.</p>
        </DashboardCard>
      </div>

      <DashboardCard title="P0 / P1 Leads" eyebrow="Sales-ready / strong MQL">
        <LeadTable leads={summary.p0_p1_leads} onOpenLead={onOpenLead} />
      </DashboardCard>
    </>
  );
}

function FunnelStep({ label, value }: { label: string; value: number }) {
  return (
    <div className="relative rounded-xl border border-stone-200 bg-stone-50 p-3 shadow-hairline">
      <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-stone-950">{formatNumber(value)}</p>
      <ArrowRight className="absolute -right-4 top-1/2 hidden -translate-y-1/2 text-stone-300 md:block" size={18} />
    </div>
  );
}

function formatDate(value?: string | null) {
  if (!value) return "Date unknown";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}
