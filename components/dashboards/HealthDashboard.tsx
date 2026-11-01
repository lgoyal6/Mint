"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { getDataQualitySummary, getIntegrationHealth } from "@/lib/api";
import type { DataQualitySummary, IntegrationHealth, ProviderHealth } from "@/lib/types";
import { BreakdownList, DashboardCard, ErrorBlock, formatPercent, LoadingBlock, StatButton } from "./shared";

type HealthDashboardProps = {
  refreshVersion: number;
  onOpenReview: (tab: "flagged" | "needs-review") => void;
};

export function HealthDashboard({ refreshVersion, onOpenReview }: HealthDashboardProps) {
  const [summary, setSummary] = useState<DataQualitySummary | null>(null);
  const [integrations, setIntegrations] = useState<IntegrationHealth | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    Promise.all([getDataQualitySummary(), getIntegrationHealth()])
      .then(([dataQuality, providerHealth]) => {
        if (!cancelled) {
          setSummary(dataQuality);
          setIntegrations(providerHealth);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load data quality summary.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshVersion, retryVersion]);

  return (
    <div className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        <DashboardCard>
          <p className="text-xs font-medium uppercase text-stone-400">Data health</p>
          <h1 className="mt-2 text-2xl font-semibold text-stone-950">Data Quality</h1>
          <p className="mt-1 text-sm text-stone-500">How reliably mint identified people, matched companies, and gathered enough evidence to classify them.</p>
          <button type="button" className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-3 py-2 text-sm font-medium text-stone-700 hover:border-stone-300" onClick={() => setRetryVersion((version) => version + 1)}>
            <RefreshCw size={14} /> Refresh checks
          </button>
          {summary ? <LumaSyncStatus syncedAt={summary.last_luma_sync_at} /> : null}
        </DashboardCard>

        {integrations ? <ProviderChecks providers={integrations} /> : null}

        {error ? <ErrorBlock message={error} onRetry={() => setRetryVersion((version) => version + 1)} /> : null}
        {isLoading ? (
          <LoadingBlock label="Loading data quality" />
        ) : summary ? (
          <>
            <div className="grid gap-3 md:grid-cols-5">
              <StatButton label="People" value={summary.resolution.total_people} />
              <StatButton label="Resolution rate" value={formatPercent(summary.resolution.resolution_rate)} />
              <StatButton label="Apollo match" value={formatPercent(summary.apollo_match_rate)} />
              <StatButton label="Low confidence" value={summary.resolution.low} onClick={() => onOpenReview("needs-review")} />
              <StatButton label="Flagged" value={summary.resolution.flagged} onClick={() => onOpenReview("flagged")} />
            </div>

            <div className="grid gap-5 lg:grid-cols-3">
              <DashboardCard title="Resolution">
                <BreakdownList
                  values={{
                    high_medium: summary.resolution.high_medium,
                    low: summary.resolution.low,
                    flagged: summary.resolution.flagged,
                  }}
                />
              </DashboardCard>
              <DashboardCard title="Enrichment Richness">
                <BreakdownList values={summary.data_quality_distribution} />
              </DashboardCard>
              <DashboardCard title="NR / NP Trigger Breakdown">
                <BreakdownList values={summary.np_trigger_breakdown} emptyLabel="No nurture triggers captured yet." />
              </DashboardCard>
            </div>

            <DashboardCard title="Promising Low Confidence">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-3xl font-semibold text-stone-950">{summary.promising_low_confidence}</p>
                  <p className="mt-1 text-sm text-stone-500">Parked leads with enough signal to deserve a human look.</p>
                </div>
                <button
                  className="rounded-lg border border-accent-line bg-accent-soft px-3 py-2 text-sm font-medium text-accent transition-colors hover:bg-[#D6E9E5]"
                  onClick={() => onOpenReview("needs-review")}
                >
                  Open queue
                </button>
              </div>
            </DashboardCard>
          </>
        ) : null}
      </div>
    </div>
  );
}

function ProviderChecks({ providers }: { providers: IntegrationHealth }) {
  return (
    <DashboardCard title="Provider checks">
      <p className="mb-3 text-sm text-stone-500">Luma and Apollo credentials are checked live without fetching lead data. Exa is never queried here, so this page cannot spend your balance.</p>
      <div className="grid gap-3 md:grid-cols-5">
        <ProviderCheck name="Luma" provider={providers.luma} />
        <ProviderCheck name="Apollo" provider={providers.apollo} />
        <ProviderCheck name="Exa" provider={providers.exa} />
        <ProviderCheck name="HubSpot" provider={providers.hubspot} />
        <ConfiguredCheck name="Clay" configured={providers.clay.configured} />
      </div>
    </DashboardCard>
  );
}

function ConfiguredCheck({ name, configured }: { name: string; configured: boolean }) {
  return (
    <div className="rounded-lg border border-stone-200 bg-stone-50 p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="font-medium text-stone-900">{name}</p>
        <p className={`text-sm font-medium ${configured ? "text-emerald-700" : "text-rose-700"}`}>{configured ? "Configured" : "Not configured"}</p>
      </div>
      <p className="mt-1 text-xs text-stone-500">{configured ? "Credential is available for review actions." : "Add the integration key before using this action."}</p>
    </div>
  );
}

function ProviderCheck({ name, provider }: { name: string; provider: ProviderHealth }) {
  const live = provider.verified && provider.status === "ok";
  const configured = provider.configured && provider.status === "configured_unverified";
  const tone = live ? "text-emerald-700" : configured ? "text-amber-700" : "text-rose-700";
  const label = live ? "Live verified" : configured ? "Configured — not queried" : provider.status.replaceAll("_", " ");
  return (
    <div className="rounded-lg border border-stone-200 bg-stone-50 p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="font-medium text-stone-900">{name}</p>
        <p className={`text-sm font-medium capitalize ${tone}`}>{label}</p>
      </div>
      <p className="mt-1 text-xs text-stone-500">
        {provider.detail ?? (live ? "API key accepted; no credits used." : provider.configured ? "Check configuration before running a paid request." : "No API key is configured.")}
      </p>
    </div>
  );
}

const SYNC_STALE_AFTER_MS = 2 * 60 * 60 * 1000;

function LumaSyncStatus({ syncedAt }: { syncedAt?: string | null }) {
  if (!syncedAt) {
    return <p className="mt-2 text-sm text-stone-400">Luma has not synced yet.</p>;
  }
  const ageMs = Date.now() - new Date(syncedAt).getTime();
  const stale = ageMs > SYNC_STALE_AFTER_MS;
  return (
    <p className={stale ? "mt-2 text-sm font-medium text-orange-700" : "mt-2 text-sm text-stone-400"}>
      Luma last synced {relativeTime(ageMs)}.
      {stale ? " That's longer than expected — the scheduled sync may be stuck." : ""}
    </p>
  );
}

function relativeTime(ageMs: number): string {
  const minutes = Math.max(0, Math.round(ageMs / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}
