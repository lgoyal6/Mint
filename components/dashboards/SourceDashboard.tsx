"use client";

import { useState } from "react";

import { exportICPShortlist, parseICPFile, runICPSource } from "@/lib/api";
import type { AgentBoxCandidate, AgentBoxSourceSummary } from "@/lib/types";
import { Button } from "@/components/ui/Button";

function candidateKey(row: AgentBoxCandidate, index: number) {
  return row.apollo_person_id || `${row.name || "unknown"}-${row.company || "company"}-${index}`;
}

function fundingLabel(row: AgentBoxCandidate) {
  const parts = [row.funding_stage || null];
  if (row.total_funding) {
    parts.push(new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(row.total_funding));
  }
  if (row.headcount) parts.push(`${row.headcount.toLocaleString()} employees`);
  return parts.filter(Boolean).join(" · ") || "Not returned";
}

export function SourceDashboard() {
  const [summary, setSummary] = useState<AgentBoxSourceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [icpName, setIcpName] = useState("");
  const [icpContent, setIcpContent] = useState("");
  const [fileName, setFileName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [showRejected, setShowRejected] = useState(false);

  async function run(limit?: number) {
    setRunning(true);
    setError(null);
    setProgress("Reading your ICP and starting the source run…");
    try {
      const nextSummary = await runICPSource(
        icpName || fileName.replace(/\.[^.]+$/, "") || "Uploaded ICP",
        icpContent,
        (next) => setProgress(next?.message ?? "Sourcing leads for your ICP…"),
        limit,
      );
      setSummary(nextSummary);
      setSelected(new Set(
        nextSummary.candidates
          .map((row, index) => ({ row, key: candidateKey(row, index) }))
          .filter(({ row }) => row.band === "pursue" || row.band === "nurture")
          .map(({ key }) => key),
      ));
      setShowRejected(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not source leads for this ICP.");
    } finally {
      setRunning(false);
      setProgress(null);
    }
  }

  function toggleCandidate(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function exportSelected() {
    if (!summary) return;
    const rows = [...summary.candidates, ...summary.rejected].filter((row, index) => selected.has(candidateKey(row, index)));
    if (!rows.length) return;
    setExporting(true);
    setError(null);
    try {
      const artifact = await exportICPShortlist(rows);
      window.location.assign(`${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}${artifact.download_url}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not export the selected shortlist.");
    } finally {
      setExporting(false);
    }
  }

  const displayedRows = summary
    ? (showRejected ? [...summary.candidates, ...summary.rejected] : summary.candidates)
    : [];

  return (
    <div className="flex flex-1 flex-col gap-5 overflow-auto p-6">
      <div className="max-w-2xl rounded-2xl border border-stone-200 bg-white p-6 shadow-hairline">
        <h2 className="text-lg font-semibold">Source leads from any ICP</h2>
        <p className="mt-2 text-sm text-stone-600">Upload a PDF, Word document, JSON, or YAML ICP. Mint extracts buyer titles, company criteria, geographies, and fit signals, then returns a reviewable shortlist.</p>
        <label className="mt-5 block text-sm font-medium text-stone-700" htmlFor="icp-name">ICP name</label>
        <input id="icp-name" className="mt-2 w-full rounded-lg border border-stone-200 px-3 py-2 text-sm" placeholder="e.g. Fintech infrastructure leaders" value={icpName} onChange={(event) => setIcpName(event.target.value)} disabled={running} />
        <label className="mt-4 block cursor-pointer rounded-xl border border-dashed border-stone-300 bg-stone-50 p-5 text-center transition hover:border-stone-500 hover:bg-stone-100">
          <span className="block text-sm font-medium text-stone-800">{fileName || "Choose an ICP file"}</span>
          <span className="mt-1 block text-xs text-stone-500">PDF, DOCX, JSON, YAML, TXT or Markdown · up to 5 MB</span>
          <input className="sr-only" type="file" accept=".pdf,.docx,.json,.yaml,.yml,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" disabled={running} onChange={(event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            if (file.size > 5 * 1024 * 1024) {
              setError("ICP documents must be 5 MB or smaller.");
              return;
            }
            setFileName(file.name);
            setError(null);
            setProgress("Reading ICP document…");
            void parseICPFile(file)
              .then((parsed) => {
                setIcpContent(parsed.content);
                if (!icpName) setIcpName(parsed.name);
              })
              .catch((err) => setError(err instanceof Error ? err.message : "Could not read ICP document."))
              .finally(() => setProgress(null));
          }} />
        </label>
        <details className="mt-3 text-xs text-stone-500">
          <summary className="cursor-pointer font-medium">ICP file format</summary>
          <pre className="mt-2 overflow-auto rounded-lg bg-stone-950 p-3 text-stone-100">{`person_titles: [CTO, VP Engineering]
company_keywords: [developer tools, infrastructure]
locations: [United States]
headcount_ranges: ["51,200", "201,500"]
strong_fit_signals: [self-hosted, enterprise platform]
exclusion_terms: [agency, consultancy]`}</pre>
        </details>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => void run(20)} disabled={running || !icpContent}>
            {running ? "Sourcing…" : "Pilot 20 leads"}
          </Button>
          <Button onClick={() => void run()} disabled={running || !icpContent}>
            Source full ICP
          </Button>
        </div>
        <p className="mt-2 text-xs text-stone-500">Run the 20-lead pilot first to verify company data and score distribution before spending credits on the full campaign.</p>
      </div>
      {error ? <p className="text-sm text-red-700">{error}</p> : null}
      {progress ? <p className="text-sm text-stone-600">{progress}</p> : null}
      {summary ? (
        <div className="rounded-2xl border border-stone-200 bg-white p-6 shadow-hairline text-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p><span className="font-semibold">{summary.leads}</span> leads scored for <span className="font-semibold">{summary.icp_name || "your ICP"}</span> · <span className="font-semibold">{summary.candidates.length}</span> review candidates · {summary.mode}</p>
              <p className="mt-2 text-stone-600">{Object.entries(summary.bands).map(([band, count]) => `${band}: ${count}`).join(" · ")}</p>
            </div>
            <Button onClick={() => void exportSelected()} disabled={!selected.size || exporting}>
              {exporting ? "Preparing export…" : `Export selected (${selected.size})`}
            </Button>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-stone-500">
            <p>Best-fit leads are selected automatically and enriched through Apollo for LinkedIn URLs. Lower-confidence and rejected records remain available for review.</p>
            {summary.rejected.length ? (
              <Button onClick={() => setShowRejected((current) => !current)}>
                {showRejected ? "Hide rejected" : `Show rejected (${summary.rejected.length})`}
              </Button>
            ) : null}
          </div>
          {displayedRows.length ? (
            <div className="mt-4 overflow-x-auto rounded-lg border border-stone-200">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-stone-50 text-xs uppercase tracking-wide text-stone-500">
                  <tr>
                    <th className="px-3 py-3">Keep</th><th className="px-3 py-3">Person</th><th className="px-3 py-3">Company</th><th className="px-3 py-3">Band</th><th className="px-3 py-3">Apollo firmographics</th><th className="px-3 py-3">Fit evidence</th><th className="px-3 py-3">LinkedIn</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100">
                  {displayedRows.map((row, index) => {
                    const key = candidateKey(row, index);
                    return <tr key={key}>
                      <td className="px-3 py-3"><input aria-label={`Select ${row.name || "candidate"}`} type="checkbox" checked={selected.has(key)} onChange={() => toggleCandidate(key)} /></td>
                      <td className="px-3 py-3"><p className="font-medium text-stone-900">{row.name || "Unknown"}</p><p className="text-stone-500">{row.title || "Title unavailable"}</p></td>
                      <td className="px-3 py-3"><p>{row.company || "Company unavailable"}</p><p className="text-xs text-stone-500">{row.domain || "Domain not returned"}</p></td>
                      <td className="px-3 py-3"><span className="rounded-full bg-stone-100 px-2 py-1 text-xs">{row.band}</span><p className="mt-2 text-xs text-stone-500">Score {row.total}</p></td>
                      <td className="px-3 py-3 text-stone-600">{fundingLabel(row)}</td>
                      <td className="max-w-md px-3 py-3 text-stone-600">{row.runtime_fit?.note || "No fit evidence returned."}</td>
                      <td className="px-3 py-3">{row.linkedin_url ? <a className="text-accent underline" href={row.linkedin_url} target="_blank" rel="noreferrer">Open</a> : <span className="text-stone-400">Not returned</span>}</td>
                    </tr>;
                  })}
                </tbody>
              </table>
            </div>
          ) : <p className="mt-4 rounded-lg bg-stone-50 p-4 text-stone-600">No candidates cleared the initial fit screen. Broaden the ICP criteria and try another pilot.</p>}
        </div>
      ) : null}
    </div>
  );
}
