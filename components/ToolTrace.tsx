"use client";

import { CheckCircle2, Download, FileSearch, Search, Tag, Wrench } from "lucide-react";
import { useState } from "react";

import { summarizeToolTrace } from "@/lib/chat";
import { cn } from "@/lib/utils";
import type { ToolTraceItem } from "@/lib/types";

type ToolTraceProps = {
  items: ToolTraceItem[];
};

export function ToolTrace({ items }: ToolTraceProps) {
  const [open, setOpen] = useState(false);

  if (!items.length) return null;

  return (
    <div className="mt-3 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground shadow-sm">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-4 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {items.map((item, index) => {
            const Icon = iconForTool(item.name);
            return (
              <span key={`${item.name}-${index}`} className="inline-flex min-w-0 items-center gap-1.5">
                <Icon
                  size={14}
                  className={cn(
                    item.error ? "text-destructive" : item.status === "started" ? "text-muted-foreground" : "text-primary",
                  )}
                />
                <span className="truncate">{friendlyToolName(item.name)}</span>
              </span>
            );
          })}
        </span>
        <span className="shrink-0 rounded-md border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
          {open ? "Hide" : "Activity"}
        </span>
      </button>

      {open ? (
        <div className="mt-3 space-y-2 border-t border-border pt-3 font-mono text-xs leading-5">
          {items.map((item, index) => (
            <div key={`${item.name}-detail-${index}`} className="rounded-md bg-muted p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-foreground">{item.name}</span>
                <span className={cn(item.error ? "text-destructive" : "text-muted-foreground")}>
                  {toolStatusText(item)}
                </span>
              </div>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-muted-foreground">
                {JSON.stringify(item.input ?? {}, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function iconForTool(name: string) {
  if (name.includes("pull") || name.includes("event")) return Download;
  if (name.includes("classify") || name.includes("reclassify")) return Tag;
  if (name.includes("resolve")) return CheckCircle2;
  if (name.includes("lead") || name.includes("summary")) return Search;
  if (name.includes("review")) return FileSearch;
  return Wrench;
}

function friendlyToolName(name: string): string {
  return name.replaceAll("_", " ");
}

function toolStatusText(item: ToolTraceItem): string {
  if (item.status === "started") return "Running";
  if (item.status === "failed") return item.error || "Failed";
  return summarizeToolTrace(item);
}
