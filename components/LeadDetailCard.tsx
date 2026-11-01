import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import type { LeadDetail } from "@/lib/types";

type LeadDetailCardProps = {
  detail: LeadDetail;
};

export function LeadDetailCard({ detail }: LeadDetailCardProps) {
  const { person, company, current_classification: classification } = detail;
  const name = person.name || [person.first_name, person.last_name].filter(Boolean).join(" ") || person.email;
  const exa = company?.exa_enrichment ?? {};
  const rawSignals: Array<[string, unknown]> = [
    ["Inference", exa.inference_signals],
    ["Spend", exa.spend_signals],
    ["Funding", exa.funding_signals],
    ["Revenue", exa.revenue_signals],
    ["Customers", exa.customer_signals],
    ["Infra", exa.competitors_or_current_infra],
    ["Hiring", exa.hiring_signals],
    ["Recent", exa.recent_activity],
  ];
  const signals: Array<[string, string]> = rawSignals.flatMap(([label, value]) =>
    typeof value === "string" && value.trim() ? [[label, value]] : [],
  );

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-stone-200 bg-canvas p-4 shadow-hairline">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-xl font-semibold text-stone-950">{name}</h2>
            <p className="mt-1 text-sm leading-5 text-stone-600">{person.title || person.self_reported_title || "Title unknown"}</p>
            <p className="mt-1 font-mono text-xs text-stone-400">{person.email}</p>
          </div>
          {classification?.tier ? <Badge tone={tierTone(classification.tier)}>{classification.tier}</Badge> : null}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {classification?.primary_product ? <Badge tone="accent">{classification.primary_product}</Badge> : null}
          {(classification?.product_fit ?? []).map((fit) => (
            <Badge key={fit}>{fit}</Badge>
          ))}
          {classification?.confidence ? <Badge>confidence {classification.confidence}</Badge> : null}
        </div>

        {company ? (
          <div className="mt-4 border-t border-stone-200 pt-4">
            <p className="text-xs font-medium uppercase text-stone-400">Company</p>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-sm font-semibold text-stone-950">{company.name || company.domain}</span>
              <a
                className="text-stone-400 transition-colors hover:text-accent"
                href={`https://${company.domain}`}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${company.domain}`}
              >
                <ExternalLink size={14} />
              </a>
            </div>
            <p className="mt-1 font-mono text-xs text-stone-500">{company.domain}</p>
          </div>
        ) : null}
      </section>

      {classification ? (
        <Section title="Why This Tier">
          <p className="text-sm leading-6 text-stone-700">{classification.reasoning || "No reasoning captured."}</p>
          <Definition label="Key signal" value={classification.key_signal} />
          <Definition label="Key risk" value={classification.key_risk} />
          {classification.flags?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {classification.flags.map((flag) => (
                <Badge key={flag} tone="warn">
                  {flag}
                </Badge>
              ))}
            </div>
          ) : null}
        </Section>
      ) : null}

      {company ? (
        <Section title="Company Intelligence">
          <p className="text-sm leading-6 text-stone-700">{company.exa_what_they_do || "No Exa company summary captured."}</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Definition label="Vertical" value={company.exa_target_vertical} />
            <Definition label="Stage" value={company.exa_stage} />
            <Definition label="Employees" value={formatNumber(company.apollo_org_employee_count)} />
            <Definition label="Funding" value={company.apollo_org_total_funding || company.apollo_org_funding_stage} />
            <Definition label="Revenue" value={company.apollo_org_revenue} />
            <Definition label="Data quality" value={company.exa_data_quality} />
          </div>
          {signals.length ? (
            <div className="mt-4 space-y-3">
              {signals.map(([label, value]) => (
                <div key={label}>
                  <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
                  <p className="mt-1 text-sm leading-6 text-stone-700">{String(value)}</p>
                </div>
              ))}
            </div>
          ) : null}
        </Section>
      ) : null}

      <Section title="Resolution">
        <div className="grid grid-cols-2 gap-3">
          <Definition label="Affiliation" value={person.affiliation_state} />
          <Definition label="Confidence" value={person.resolution_confidence} />
        </div>
        {Array.isArray(person.resolution_evidence?.chain) && person.resolution_evidence.chain.length ? (
          <div className="mt-4">
            <p className="text-xs font-medium uppercase text-stone-400">Evidence chain</p>
            <ul className="mt-2 space-y-1 text-sm leading-5 text-stone-700">
              {person.resolution_evidence.chain.map((line, index) => (
                <li key={index}>{String(line)}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {person.candidate_companies?.length ? (
          <div className="mt-4 space-y-2">
            <p className="text-xs font-medium uppercase text-stone-400">Candidate companies</p>
            {person.candidate_companies.map((candidate, index) => (
              <div key={index} className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm text-stone-700">
                <p className="font-medium text-stone-900">{String(candidate.name || candidate.domain || "Unknown")}</p>
                {candidate.domain ? <p className="font-mono text-xs text-stone-500">{String(candidate.domain)}</p> : null}
              </div>
            ))}
          </div>
        ) : null}
      </Section>

      {detail.classification_history?.length ? (
        <Section title="Classification History">
          <div className="space-y-2">
            {detail.classification_history.map((entry) => (
              <div key={entry.id} className="rounded-lg bg-stone-50 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={tierTone(entry.tier)}>{entry.tier}</Badge>
                    {entry.primary_product ? <Badge tone="accent">{entry.primary_product}</Badge> : null}
                    {entry.review_status ? <Badge>{entry.review_status}</Badge> : null}
                  </div>
                  <span className="font-mono text-xs text-stone-400">{formatDate(entry.classified_at)}</span>
                </div>
                <p className="mt-2 text-sm leading-5 text-stone-700">{entry.reasoning || "No reasoning captured."}</p>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section title="Footprint">
        <div className="grid grid-cols-2 gap-3">
          <Definition label="Registered" value={formatNumber(person.events_registered)} />
          <Definition label="Attended" value={formatNumber(person.events_attended)} />
        </div>
        {detail.registrations?.length ? (
          <div className="mt-4 space-y-2">
            {detail.registrations.map((registration) => (
              <div key={registration.id} className="rounded-lg bg-stone-50 p-3">
                <p className="text-sm font-medium text-stone-900">{registration.event_name || registration.event_id}</p>
                <p className="text-xs text-stone-500">{registration.approval_status || "unknown status"}</p>
              </div>
            ))}
          </div>
        ) : null}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-stone-200 bg-canvas p-4 shadow-hairline">
      <h3 className="text-sm font-semibold text-stone-950">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Definition({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-stone-400">{label}</p>
      <p className="mt-1 text-sm leading-5 text-stone-800">{value || "Unknown"}</p>
    </div>
  );
}

function tierTone(tier: string) {
  if (tier === "P0") return "p0";
  if (tier === "P1") return "p1";
  if (tier === "P2") return "p2";
  if (tier === "NR") return "nr";
  return "np";
}

function formatNumber(value?: number | null) {
  if (value === null || value === undefined) return null;
  return new Intl.NumberFormat().format(value);
}

function formatDate(value?: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}
