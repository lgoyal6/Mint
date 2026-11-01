"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { LeadSummary } from "@/lib/types";

const LEAD_TABLE_PAGE_SIZE = 25;

export function DashboardCard({
  title,
  eyebrow,
  children,
  className,
  contentClassName,
}: {
  title?: string;
  eyebrow?: string;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <section className={cn("rounded-xl border border-stone-200 bg-canvas p-4 shadow-hairline", className)}>
      {eyebrow ? <p className="text-xs font-medium uppercase text-stone-400">{eyebrow}</p> : null}
      {title ? <h2 className="mt-1 text-base font-semibold text-stone-950">{title}</h2> : null}
      <div className={cn(title || eyebrow ? "mt-4" : undefined, contentClassName)}>{children}</div>
    </section>
  );
}

export function StatButton({
  label,
  value,
  sublabel,
  onClick,
}: {
  label: string;
  value: number | string;
  sublabel?: string;
  onClick?: () => void;
}) {
  const Element = onClick ? "button" : "div";
  return (
    <Element
      className={cn(
        "rounded-xl border border-stone-200 bg-stone-50 p-3 text-left shadow-hairline",
        onClick && "transition-colors hover:border-accent-line hover:bg-accent-soft/40",
      )}
      onClick={onClick}
    >
      <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-stone-950">{value}</p>
      {sublabel ? <p className="mt-1 text-xs text-stone-500">{sublabel}</p> : null}
    </Element>
  );
}

export function BreakdownList({
  values,
  emptyLabel = "No values yet",
}: {
  values: Record<string, number | undefined>;
  emptyLabel?: string;
}) {
  const entries = Object.entries(values)
    .filter(([, value]) => Number(value ?? 0) > 0)
    .sort((a, b) => Number(b[1] ?? 0) - Number(a[1] ?? 0));
  const max = Math.max(1, ...entries.map(([, value]) => Number(value ?? 0)));
  if (!entries.length) return <p className="text-sm text-stone-500">{emptyLabel}</p>;

  return (
    <div className="space-y-3">
      {entries.map(([key, value]) => (
        <div key={key}>
          <div className="mb-1 flex items-center justify-between gap-3 text-sm">
            <span className="truncate text-stone-700">{formatLabel(key)}</span>
            <span className="font-mono text-xs text-stone-500">{value}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-100">
            <div className="h-full rounded-full bg-accent" style={{ width: `${(Number(value ?? 0) / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function LeadTable({
  leads,
  emptyLabel = "No leads in this view",
  onOpenLead,
}: {
  leads: LeadSummary[];
  emptyLabel?: string;
  onOpenLead: (personId: string) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(LEAD_TABLE_PAGE_SIZE);

  if (!leads.length) {
    return (
      <div className="rounded-xl border border-dashed border-stone-300 p-5 text-sm text-stone-500">{emptyLabel}</div>
    );
  }

  const visibleLeads = leads.slice(0, visibleCount);
  const remaining = leads.length - visibleLeads.length;

  return (
    <div className="overflow-x-auto rounded-xl border border-stone-200">
      <table className="min-w-[900px] divide-y divide-stone-200 text-sm">
        <thead className="bg-stone-50 text-left text-xs uppercase text-stone-400">
          <tr>
            <th className="px-3 py-2 font-medium">Lead</th>
            <th className="px-3 py-2 font-medium">Company</th>
            <th className="px-3 py-2 font-medium">Region</th>
            <th className="px-3 py-2 font-medium">Location</th>
            <th className="px-3 py-2 font-medium">Tier</th>
            <th className="px-3 py-2 font-medium">Product</th>
            <th className="px-3 py-2 font-medium">Confidence</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-200 bg-canvas">
          {visibleLeads.map((lead) => (
            <tr
              key={lead.person_id}
              className="cursor-pointer transition-colors hover:bg-stone-50"
              onClick={() => onOpenLead(lead.person_id)}
            >
              <td className="px-3 py-3">
                <p className="font-medium text-stone-950">{lead.name || lead.email}</p>
                <p className="mt-0.5 max-w-[220px] truncate text-xs text-stone-500">{lead.title || lead.email}</p>
              </td>
              <td className="px-3 py-3">
                <p className="max-w-[200px] truncate text-stone-700">{lead.resolved_company || "Unknown"}</p>
                <p className="font-mono text-xs text-stone-400">{lead.resolved_domain || ""}</p>
              </td>
              <td className="px-3 py-3 text-stone-700">{lead.region || "Needs location review"}</td>
              <td className="max-w-[180px] truncate px-3 py-3 text-xs text-stone-500">{lead.location || "—"}</td>
              <td className="px-3 py-3">{lead.tier ? <Badge tone={tierTone(lead.tier)}>{lead.tier}</Badge> : null}</td>
              <td className="px-3 py-3">
                <div className="flex flex-wrap gap-1">
                  {lead.primary_product ? <Badge tone="accent">{lead.primary_product}</Badge> : null}
                  {(lead.product_fit ?? []).filter((fit) => fit !== lead.primary_product).map((fit) => (
                    <Badge key={fit}>{fit}</Badge>
                  ))}
                </div>
              </td>
              <td className="px-3 py-3 font-mono text-xs text-stone-500">{lead.confidence ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {remaining > 0 ? (
        <button
          className="w-full border-t border-stone-200 bg-stone-50 px-3 py-2.5 text-sm font-medium text-stone-600 transition-colors hover:bg-stone-100 hover:text-stone-900"
          onClick={() => setVisibleCount((count) => count + LEAD_TABLE_PAGE_SIZE)}
        >
          Show more ({remaining} remaining)
        </button>
      ) : null}
    </div>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="space-y-3">
      <div className="h-4 w-32 animate-pulse rounded bg-stone-200" aria-label={label} />
      <div className="h-28 animate-pulse rounded-xl border border-stone-200 bg-stone-100/70" />
    </div>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">
      <span>{message}</span>
      {onRetry ? (
        <button
          className="shrink-0 rounded-lg border border-orange-300 bg-white px-3 py-1.5 text-sm font-medium text-orange-900 transition-colors hover:bg-orange-100"
          onClick={onRetry}
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function formatNumber(value?: number | null) {
  return new Intl.NumberFormat().format(value ?? 0);
}

export function formatPercent(value?: number | null) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

export function formatLabel(value: string) {
  return value.replaceAll("_", " ");
}

export function tierTone(tier?: string | null) {
  if (tier === "P0") return "p0";
  if (tier === "P1") return "p1";
  if (tier === "P2") return "p2";
  if (tier === "NR") return "nr";
  return "np";
}
