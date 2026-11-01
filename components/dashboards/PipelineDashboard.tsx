"use client";

import { useEffect, useState } from "react";

import { getPipelineSummary } from "@/lib/api";
import type { PipelineSummary } from "@/lib/types";
import { BreakdownList, DashboardCard, ErrorBlock, LeadTable, LoadingBlock, StatButton } from "./shared";

type PipelineDashboardProps = {
  refreshVersion: number;
  onOpenLead: (personId: string) => void;
};

export function PipelineDashboard({ refreshVersion, onOpenLead }: PipelineDashboardProps) {
  const [summary, setSummary] = useState<PipelineSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getPipelineSummary()
      .then((value) => {
        if (!cancelled) setSummary(value);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load pipeline summary.");
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
          <p className="text-xs font-medium uppercase text-stone-400">Cross-event Rollup</p>
          <h1 className="mt-2 text-2xl font-semibold text-stone-950">Pipeline</h1>
          <p className="mt-1 text-sm text-stone-500">P0/P1 SQL-ready routing, repeat attendance, account concentration, and nurture watchlist.</p>
        </DashboardCard>

        {error ? <ErrorBlock message={error} onRetry={() => setRetryVersion((version) => version + 1)} /> : null}
        {isLoading ? (
          <LoadingBlock label="Loading pipeline" />
        ) : summary ? (
          <>
            <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
              <DashboardCard title="P0 / P1 by Primary Product">
                <div className="grid grid-cols-3 gap-2">
                  <StatButton label="MaaS" value={summary.p0_p1_by_primary_product.maas ?? 0} />
                  <StatButton label="Dedicated" value={summary.p0_p1_by_primary_product.dedicated ?? 0} />
                  <StatButton label="GPU" value={summary.p0_p1_by_primary_product.gpu ?? 0} />
                </div>
              </DashboardCard>
              <DashboardCard title="Nurture / Re-engage" eyebrow="Right profile, wrong time">
                <LeadTable
                  leads={summary.nurture_reengage.slice(0, 5)}
                  emptyLabel="No nurture-ready leads yet."
                  onOpenLead={onOpenLead}
                />
              </DashboardCard>
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <DashboardCard title="Repeat Attendees">
                {summary.repeat_attendees.length ? (
                  <div className="overflow-hidden rounded-xl border border-stone-200">
                    <table className="min-w-full divide-y divide-stone-200 text-sm">
                      <thead className="bg-stone-50 text-left text-xs uppercase text-stone-400">
                        <tr>
                          <th className="px-3 py-2 font-medium">Email</th>
                          <th className="px-3 py-2 font-medium">Registered</th>
                          <th className="px-3 py-2 font-medium">Attended</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-stone-200 bg-canvas">
                        {summary.repeat_attendees.map((row) => (
                          <tr
                            key={row.person_id}
                            className="cursor-pointer transition-colors hover:bg-stone-50"
                            onClick={() => onOpenLead(row.person_id)}
                          >
                            <td className="px-3 py-3 font-medium text-stone-950">{row.email}</td>
                            <td className="px-3 py-3 font-mono text-xs text-stone-500">{row.events_registered}</td>
                            <td className="px-3 py-3 font-mono text-xs text-stone-500">{row.events_attended}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-stone-500">No repeat attendees yet.</p>
                )}
              </DashboardCard>

              <DashboardCard title="Companies With Multiple People">
                {summary.companies_with_multiple_people.length ? (
                  <BreakdownList
                    values={Object.fromEntries(
                      summary.companies_with_multiple_people.map((company) => [
                        company.name || company.domain,
                        company.person_count,
                      ]),
                    )}
                  />
                ) : (
                  <p className="text-sm text-stone-500">No multi-person accounts yet.</p>
                )}
              </DashboardCard>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
