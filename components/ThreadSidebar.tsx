"use client";

import { CalendarDays, Menu, MessageSquarePlus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import type { Thread } from "@/lib/types";

type ThreadSidebarProps = {
  threads: Thread[];
  activeThreadId: string;
  open: boolean;
  onToggle: () => void;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
};

export function ThreadSidebar({
  threads,
  activeThreadId,
  open,
  onToggle,
  onNewThread,
  onSelectThread,
  onDeleteThread,
}: ThreadSidebarProps) {
  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col border-r border-stone-200/80 bg-stone-100/70 transition-[width]",
        open ? "w-[260px]" : "w-[56px]",
      )}
    >
      <div className="flex h-14 items-center justify-between px-3">
        {open ? <div className="text-sm font-semibold">Threads</div> : null}
        <Button variant="ghost" className="h-8 w-8 px-0" onClick={onToggle} aria-label="Toggle thread sidebar">
          <Menu size={16} />
        </Button>
      </div>

      <div className="px-3 pb-3">
        <Button className={cn("w-full", !open && "w-8 px-0")} variant="primary" onClick={onNewThread}>
          <MessageSquarePlus size={16} />
          {open ? <span>Misc chat</span> : null}
        </Button>
      </div>

      {open ? (
        <nav className="scrollbar-subtle flex-1 space-y-1 overflow-y-auto px-2 pb-4" aria-label="Conversation threads">
          {threads.map((thread) => (
            <div
              key={thread.id}
              className={cn(
                "group flex w-full items-center rounded-lg transition-colors",
                thread.id === activeThreadId
                  ? "bg-canvas text-stone-950 shadow-hairline"
                  : "text-stone-600 hover:bg-stone-200/60 hover:text-stone-950",
              )}
            >
              <button className="min-w-0 flex-1 px-3 py-2 text-left" onClick={() => onSelectThread(thread.id)}>
                <span className="flex items-center gap-1.5 truncate text-sm font-medium">
                  {thread.eventId ? <CalendarDays size={13} className="shrink-0 text-accent" /> : null}
                  <span className="truncate">{thread.title}</span>
                </span>
                <span className="mt-0.5 block text-xs text-stone-400">{formatDate(thread.updatedAt)}</span>
              </button>
              <button
                className="mr-1 hidden shrink-0 rounded-md p-1.5 text-stone-400 transition-colors hover:bg-stone-200 hover:text-stone-700 group-hover:block"
                onClick={() => onDeleteThread(thread.id)}
                aria-label={`Delete conversation ${thread.title}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </nav>
      ) : null}
    </aside>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}
