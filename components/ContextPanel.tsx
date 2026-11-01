"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { LeadDetailCard } from "@/components/LeadDetailCard";
import { Button } from "@/components/ui/Button";
import { getLeadDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LeadDetail } from "@/lib/types";

type ContextPanelProps = {
  open: boolean;
  personId: string | null;
  refreshVersion: number;
  onClose: () => void;
};

export function ContextPanel({ open, personId, refreshVersion, onClose }: ContextPanelProps) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !personId) return;
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    getLeadDetail(personId)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load lead.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, personId, refreshVersion]);

  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col overflow-hidden border-l border-stone-200/80 bg-stone-50/95 transition-[width]",
        open ? "w-[420px]" : "w-0 border-l-0",
      )}
      aria-label="Context panel"
    >
      {open ? (
        <>
          <div className="flex h-14 shrink-0 items-center justify-between border-b border-stone-200/80 px-4">
            <div>
              <p className="text-sm font-semibold text-stone-950">Context</p>
              <p className="text-xs text-stone-500">{personId ? "Lead profile" : "Select a lead reference"}</p>
            </div>
            <Button variant="ghost" className="h-8 w-8 px-0" onClick={onClose} aria-label="Close context panel">
              <X size={16} />
            </Button>
          </div>

          <div className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto p-4">
            {!personId ? (
              <div className="rounded-xl border border-dashed border-stone-300 p-5 text-sm leading-6 text-stone-500">
                Lead and event detail will live here. Click a lead reference in an assistant answer to inspect the full
                profile.
              </div>
            ) : isLoading ? (
              <div className="rounded-xl border border-stone-200 bg-canvas p-5 text-sm text-stone-500">Loading lead...</div>
            ) : error ? (
              <div className="rounded-xl border border-orange-200 bg-orange-50 p-5 text-sm text-orange-900">{error}</div>
            ) : detail ? (
              <LeadDetailCard detail={detail} />
            ) : null}
          </div>
        </>
      ) : null}
    </aside>
  );
}
