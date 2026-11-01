"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { getApiUsageSummary } from "@/lib/api";
import type { ApiUsageSummary } from "@/lib/types";
import { DashboardCard, ErrorBlock, LoadingBlock } from "./shared";

export function UsageDashboard({ refreshVersion }: { refreshVersion: number }) {
  const [data, setData] = useState<ApiUsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getApiUsageSummary()
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load API usage.");
      });
    return () => {
      cancelled = true;
    };
  }, [refreshVersion, retryVersion]);

  const totals = useMemo(
    () =>
      (data?.events ?? []).reduce(
        (sum, row) => ({
          apolloCredits: sum.apolloCredits + row.apollo.credits,
          apolloRequests: sum.apolloRequests + row.apollo.requests,
          apolloCost: sum.apolloCost + row.apollo.estimated_cost_usd,
          exaRequests: sum.exaRequests + row.exa.requests,
          exaCost: sum.exaCost + row.exa.estimated_cost_usd,
        }),
        { apolloCredits: 0, apolloRequests: 0, apolloCost: 0, exaRequests: 0, exaCost: 0 },
      ),
    [data],
  );

  if (error && !data) return <div className="p-5"><ErrorBlock message={error} /></div>;
  if (!data) return <div className="p-5"><LoadingBlock label="Loading API usage" /></div>;

  return (
    <div className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <UsageStat label="Apollo credits" value={formatCredits(totals.apolloCredits)} detail={`${totals.apolloRequests} requests`} />
          <UsageStat
            label="Apollo estimated cost"
            value={data.apollo_dollar_rate_configured ? formatDollars(totals.apolloCost) : "Set rate"}
            detail={data.apollo_dollar_rate_configured ? "Configured cost per credit" : "Add APOLLO_CREDIT_COST_USD"}
          />
          <UsageStat label="Exa calls" value={String(totals.exaRequests)} detail="Search and deep-search requests" />
          <UsageStat label="Exa estimated cost" value={formatDollars(totals.exaCost)} detail="Published API rates" />
        </div>

        <DashboardCard title="Usage by event" eyebrow="API monitor">
          <button type="button" className="mb-3 inline-flex items-center gap-1.5 rounded-lg border border-stone-200 px-3 py-2 text-sm font-medium text-stone-700 hover:border-stone-300" onClick={() => setRetryVersion((version) => version + 1)}>
            <RefreshCw size={14} /> Refresh usage
          </button>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-stone-200 text-xs uppercase text-stone-400">
                  <th className="px-3 py-2 font-medium">Event</th>
                  <th className="px-3 py-2 font-medium">Apollo calls</th>
                  <th className="px-3 py-2 font-medium">Apollo credits</th>
                  <th className="px-3 py-2 font-medium">Apollo $</th>
                  <th className="px-3 py-2 font-medium">Exa calls</th>
                  <th className="px-3 py-2 font-medium">Exa $ est.</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((row) => (
                  <tr key={row.event.id} className="border-b border-stone-100 last:border-0">
                    <td className="px-3 py-3">
                      <p className="font-medium text-stone-900">{row.event.name}</p>
                      <p className="mt-0.5 text-xs text-stone-400">{formatDate(row.event.event_date)}</p>
                    </td>
                    <td className="px-3 py-3 font-mono text-stone-700">{row.apollo.requests}</td>
                    <td className="px-3 py-3 font-mono text-stone-700">{formatCredits(row.apollo.credits)}</td>
                    <td className="px-3 py-3 font-mono text-stone-700">
                      {data.apollo_dollar_rate_configured ? formatDollars(row.apollo.estimated_cost_usd) : "—"}
                    </td>
                    <td className="px-3 py-3 font-mono text-stone-700">{row.exa.requests}</td>
                    <td className="px-3 py-3 font-mono font-medium text-stone-900">{formatDollars(row.exa.estimated_cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 grid gap-2 text-xs text-stone-500 sm:grid-cols-2">
            <p>{data.pricing.apollo}</p>
            <p>{data.pricing.exa}</p>
          </div>
        </DashboardCard>
      </div>
    </div>
  );
}

function UsageStat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-xl border border-stone-200 bg-canvas p-4 shadow-hairline">
      <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-stone-950">{value}</p>
      <p className="mt-1 text-xs text-stone-500">{detail}</p>
    </div>
  );
}

function formatCredits(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function formatDollars(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatDate(value?: string | null): string {
  if (!value) return "Date unavailable";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}
