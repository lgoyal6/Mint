"use client";

import { useEffect, useRef } from "react";

import { Message } from "@/components/Message";
import { ToolTrace } from "@/components/ToolTrace";
import { assistantDraftTitle } from "@/lib/chat";
import type { Thread, ToolTraceItem } from "@/lib/types";

type ChatThreadProps = {
  thread: Thread;
  isLoading: boolean;
  activity?: string | null;
  liveToolTrace?: ToolTraceItem[];
  error: string | null;
  onOpenLead: (personId: string) => void;
  onSuggestion: (text: string) => void;
  onRetry: () => void;
};

export function ChatThread({
  thread,
  isLoading,
  activity,
  liveToolTrace = [],
  error,
  onOpenLead,
  onSuggestion,
  onRetry,
}: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.messages.length, isLoading, error, activity]);

  return (
    <div className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-[860px] flex-col px-6 py-8">
        {thread.messages.length === 0 ? (
          <EmptyState onSuggestion={onSuggestion} />
        ) : (
          <div className="space-y-7">
            {thread.messages.map((message, index) => (
              <Message key={message.id} message={message} index={index} onOpenLead={onOpenLead} />
            ))}
          </div>
        )}

        {isLoading ? <AssistantDraft activity={activity} liveToolTrace={liveToolTrace} /> : null}

        {error ? (
          <div className="mt-5 flex items-center justify-between gap-4 rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900">
            <span>{error}</span>
            <button
              className="shrink-0 rounded-lg border border-orange-300 bg-white px-3 py-1.5 text-sm font-medium text-orange-900 transition-colors hover:bg-orange-100"
              onClick={onRetry}
            >
              Retry
            </button>
          </div>
        ) : null}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function AssistantDraft({
  activity,
  liveToolTrace,
}: {
  activity?: string | null;
  liveToolTrace: ToolTraceItem[];
}) {
  return (
    <article className="mr-auto mt-7 w-full max-w-[760px] animate-soft-in">
      <div className="rounded-lg border border-border bg-card px-4 py-3 text-[15px] leading-7 text-card-foreground shadow-sm">
        <div className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <span className="agent-loader shrink-0" aria-hidden="true" />
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase text-primary">{assistantDraftTitle(liveToolTrace)}</p>
              <p className="mt-0.5 truncate text-sm text-muted-foreground">
                {activity ?? "Thinking through the workspace"}
              </p>
            </div>
          </div>
        </div>
      </div>
      <ToolTrace items={liveToolTrace} />
    </article>
  );
}

const SUGGESTIONS = [
  "Show me the top P0 leads",
  "Which leads came from the last event?",
  "Who needs review right now?",
  "Summarize pipeline health",
  "Pull the latest Luma event and classify it",
];

function EmptyState({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  return (
    <div className="flex flex-1 items-center">
      <div className="max-w-[640px]">
        <p className="text-xs font-semibold uppercase text-accent">mint console</p>
        <h1 className="mt-4 text-4xl font-semibold leading-tight text-stone-950">
          Find the leads worth pursuing.
        </h1>
        <p className="mt-4 max-w-[560px] text-lg leading-8 text-stone-600">
          mint turns event attendee lists into verified, prioritized sales leads. Ask about an event, a lead, or the
          pipeline — or try one of these:
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700 transition-colors hover:border-accent-line hover:bg-accent-soft hover:text-accent"
              onClick={() => onSuggestion(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
        <p className="mt-5 text-sm text-stone-500">
          Have a guest list? Upload CSV/XLSX with employer, company website, LinkedIn, role, and what each attendee is building for the strongest classification.
        </p>
      </div>
    </div>
  );
}
