"use client";

import { useEffect, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";

import { getEventAnalytics, saveEventRatings } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EventAnalytics } from "@/lib/types";

import { DashboardCard, ErrorBlock, LoadingBlock } from "./shared";

// Stale-while-revalidate cache, mirroring the events/summary caches in EventDashboard.
const analyticsCache = new Map<string, EventAnalytics>();

const RATING_SCALE = [1, 2, 3, 4, 5];

export function EventScorecardCard({ eventId }: { eventId: string }) {
  const [analytics, setAnalytics] = useState<EventAnalytics | null>(() => analyticsCache.get(eventId) ?? null);
  const [isLoading, setIsLoading] = useState(!analyticsCache.has(eventId));
  const [error, setError] = useState<string | null>(null);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const cached = analyticsCache.get(eventId);
    if (cached) setAnalytics(cached);
    setIsLoading(!cached);
    setError(null);
    getEventAnalytics(eventId)
      .then((value) => {
        analyticsCache.set(eventId, value);
        if (!cancelled) setAnalytics(value);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load the scorecard.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  // Seed the editable form from the latest analytics (initial load and after a save).
  useEffect(() => {
    if (!analytics) return;
    const seeded: Record<string, number> = {};
    for (const dimension of analytics.manual.dimensions) {
      const value = analytics.manual.scores?.[dimension.key];
      if (typeof value === "number") seeded[dimension.key] = value;
    }
    setScores(seeded);
    setNotes(analytics.manual.notes ?? "");
  }, [analytics]);

  async function handleSave() {
    setIsSaving(true);
    setError(null);
    try {
      const updated = await saveEventRatings(eventId, { scores, notes: notes.trim() || null });
      analyticsCache.set(eventId, updated);
      setAnalytics(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save ratings.");
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading && !analytics) {
    return (
      <DashboardCard title="Event Scorecard" eyebrow="How did we do">
        <LoadingBlock label="Loading scorecard" />
      </DashboardCard>
    );
  }

  if (!analytics) {
    return (
      <DashboardCard title="Event Scorecard" eyebrow="How did we do">
        {error ? <ErrorBlock message={error} /> : <p className="text-sm text-stone-500">No scorecard available.</p>}
      </DashboardCard>
    );
  }

  const radarData = analytics.radar.map((point) => ({ axis: point.axis, score: point.score }));
  const hasManual = analytics.radar.some((point) => point.kind === "manual");

  return (
    <DashboardCard title="Event Scorecard" eyebrow="How did we do">
      {error ? (
        <div className="mb-4">
          <ErrorBlock message={error} />
        </div>
      ) : null}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div>
          <div className="flex items-baseline justify-between">
            <p className="text-xs font-medium uppercase text-stone-400">Performance radar</p>
            <p className="text-sm text-stone-500">
              Overall{" "}
              <span className="font-semibold text-stone-950">
                {analytics.overall_score === null ? "—" : analytics.overall_score}
              </span>
            </p>
          </div>
          <div className="mt-2 h-[260px]">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} outerRadius="72%">
                <PolarGrid stroke="#E7E5E4" />
                <PolarAngleAxis dataKey="axis" tick={{ fill: "#57534E", fontSize: 12 }} />
                <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
                <Radar dataKey="score" stroke="#2F766F" fill="#2F766F" fillOpacity={0.25} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {analytics.auto.map((axis) => (
              <div key={axis.key} className="rounded-lg border border-stone-200 bg-stone-50 p-2.5">
                <p className="text-xs font-medium uppercase text-stone-400">{axis.label}</p>
                <p className="mt-0.5 text-lg font-semibold text-stone-950">{axis.value}</p>
                <p className="text-xs text-stone-500">{axis.detail}</p>
              </div>
            ))}
          </div>
          {!hasManual ? (
            <p className="mt-2 text-xs text-stone-500">Add ratings to plot Engagement, Content, and Follow-up.</p>
          ) : null}
        </div>

        <div>
          <p className="text-xs font-medium uppercase text-stone-400">Your ratings</p>
          <div className="mt-3 space-y-3">
            {analytics.manual.dimensions.map((dimension) => (
              <div key={dimension.key}>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-sm text-stone-700">{dimension.label}</span>
                  <button
                    type="button"
                    className={cn(
                      "text-xs text-stone-400 transition-colors hover:text-stone-600",
                      scores[dimension.key] === undefined && "invisible",
                    )}
                    onClick={() =>
                      setScores((current) => {
                        const next = { ...current };
                        delete next[dimension.key];
                        return next;
                      })
                    }
                  >
                    Clear
                  </button>
                </div>
                <div className="flex gap-1">
                  {RATING_SCALE.map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={cn(
                        "h-8 flex-1 rounded-md border text-sm font-medium transition-colors",
                        scores[dimension.key] === value
                          ? "border-accent bg-accent text-white"
                          : "border-stone-200 bg-stone-50 text-stone-600 hover:border-accent-line hover:bg-accent-soft/40",
                      )}
                      onClick={() => setScores((current) => ({ ...current, [dimension.key]: value }))}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <textarea
            className="mt-3 w-full resize-none rounded-lg border border-stone-200 bg-stone-50 p-2.5 text-sm text-stone-700 placeholder:text-stone-400 focus:border-accent-line focus:outline-none"
            rows={3}
            placeholder="Notes for next time…"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
          <button
            type="button"
            disabled={isSaving}
            className={cn(
              "mt-3 w-full rounded-lg border border-accent bg-accent px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/90",
              isSaving && "opacity-60",
            )}
            onClick={handleSave}
          >
            {isSaving ? "Saving…" : "Save ratings"}
          </button>
        </div>
      </div>
    </DashboardCard>
  );
}
