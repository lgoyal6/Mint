"use client";

import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ChatThread } from "@/components/ChatThread";
import { Composer } from "@/components/Composer";
import { ContextPanel } from "@/components/ContextPanel";
import { EventDashboard } from "@/components/dashboards/EventDashboard";
import { HealthDashboard } from "@/components/dashboards/HealthDashboard";
import { NavRail, type AppScreen } from "@/components/dashboards/NavRail";
import { LoginGate } from "@/components/LoginGate";
import { PipelineDashboard } from "@/components/dashboards/PipelineDashboard";
import { UsageDashboard } from "@/components/dashboards/UsageDashboard";
import { SourceDashboard } from "@/components/dashboards/SourceDashboard";
import { ReviewScreen } from "@/components/review/ReviewScreen";
import { ThreadSidebar } from "@/components/ThreadSidebar";
import { Button } from "@/components/ui/Button";
import {
  appendChatMessages,
  chatWithAgentStream,
  deleteChatThread,
  getAuthStatus,
  getChatThread,
  getReviewCounts,
  listChatThreads,
  logout,
  saveChatThread,
  uploadLeadsFile,
} from "@/lib/api";
import {
  agentActivityLabel,
  appendAssistantTurn,
  appendUploadSummaryMessage,
  appendUserMessage,
  backendMessages,
  createThread,
  reduceLiveToolEvents,
} from "@/lib/chat";
import type { JobProgress, Thread, ThreadMessage } from "@/lib/types";

export function AgentConsole() {
  const [authState, setAuthState] = useState<"checking" | "authenticated" | "unauthenticated">("checking");
  const [threads, setThreads] = useState<Thread[]>(() => [{ ...createThread(), title: "Miscellaneous" }]);
  const [activeThreadId, setActiveThreadId] = useState(() => threads[0].id);
  const [activeScreen, setActiveScreen] = useState<AppScreen>("chat");
  const [reviewTab, setReviewTab] = useState<"flagged" | "needs-review">("flagged");
  const [reviewEventId, setReviewEventId] = useState("");
  const [needsReviewType, setNeedsReviewType] = useState<"classification" | "resolution" | "all">("all");
  const [reviewCount, setReviewCount] = useState(0);
  const [dataRefreshVersion, setDataRefreshVersion] = useState(0);
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [panelOpen, setPanelOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [agentActivity, setAgentActivity] = useState<string | null>(null);
  const [liveToolTrace, setLiveToolTrace] = useState<ThreadMessage["toolTrace"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ thread: Thread; index: number } | null>(null);
  const pendingDeleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistedThreadIds = useRef<Set<string>>(new Set());
  const loadedThreadIds = useRef<Set<string>>(new Set());

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId) ?? threads[0],
    [activeThreadId, threads],
  );

  useEffect(() => {
    void checkAuth();
  }, []);

  useEffect(() => {
    if (authState !== "authenticated") return;
    void refreshReviewCounts();
    void loadStoredThreads();
  }, [authState]);

  async function loadStoredThreads() {
    try {
      const summaries = await listChatThreads();
      if (!summaries.length) return;
      for (const summary of summaries) persistedThreadIds.current.add(summary.id);
      const now = new Date().toISOString();
      const stored: Thread[] = summaries.map((summary) => ({
        id: summary.id,
        title: summary.title,
        createdAt: summary.created_at ?? now,
        updatedAt: summary.updated_at ?? now,
        messages: [],
        eventId: summary.event_id ?? null,
        eventName: summary.event_name ?? (summary.event_id ? summary.title : null),
      }));
      setThreads((current) => {
        const currentIds = new Set(current.map((thread) => thread.id));
        return [...current, ...stored.filter((thread) => !currentIds.has(thread.id))];
      });
    } catch (err) {
      console.warn("Could not load saved conversations", err);
    }
  }

  async function checkAuth() {
    try {
      const status = await getAuthStatus();
      setAuthState(status.authenticated ? "authenticated" : "unauthenticated");
    } catch {
      setAuthState("unauthenticated");
    }
  }

  async function refreshReviewCounts() {
    try {
      const counts = await getReviewCounts();
      setReviewCount(counts.flagged + counts.needsReview);
    } catch {
      setReviewCount(0);
    }
  }

  async function handleReviewChanged() {
    setDataRefreshVersion((version) => version + 1);
    await refreshReviewCounts();
  }

  async function handleLogout() {
    try {
      await logout();
    } finally {
      setAuthState("unauthenticated");
    }
  }

  async function persistMessages(thread: Thread, messages: ThreadMessage[]) {
    try {
      if (!persistedThreadIds.current.has(thread.id)) {
        await saveChatThread(thread.id, thread.title, thread.eventId);
        persistedThreadIds.current.add(thread.id);
        loadedThreadIds.current.add(thread.id);
      }
      await appendChatMessages(
        thread.id,
        messages.map((message) => ({
          id: message.id,
          role: message.role,
          content: message.content,
          tool_trace: message.toolTrace ?? null,
        })),
      );
    } catch (err) {
      console.warn("Could not save chat history", err);
    }
  }

  async function handleSend(content: string) {
    if (!content.trim() || isLoading) return;
    const threadWithUser = appendUserMessage(activeThread, content);
    setThreads((current) => replaceThread(current, threadWithUser));
    void persistMessages(threadWithUser, threadWithUser.messages.slice(-1));
    await runAgentTurn(threadWithUser);
  }

  async function handleRetry() {
    if (isLoading || activeThread.messages.length === 0) return;
    await runAgentTurn(activeThread);
  }

  async function runAgentTurn(thread: Thread) {
    setError(null);
    setIsLoading(true);
    setLiveToolTrace([]);
    try {
      const response = await chatWithAgentStream(backendMessages(thread), (event) => {
        setAgentActivity(agentActivityLabel(event));
        setLiveToolTrace((items) => reduceLiveToolEvents(items ?? [], event));
      });
      const threadWithAssistant = appendAssistantTurn(thread, response);
      setThreads((current) => replaceThread(current, threadWithAssistant));
      void persistMessages(threadWithAssistant, threadWithAssistant.messages.slice(-1));
    } catch (err) {
      const message = err instanceof Error ? err.message : "The agent request failed.";
      setError(message);
    } finally {
      setIsLoading(false);
      setAgentActivity(null);
      setLiveToolTrace([]);
    }
  }

  async function handleUploadFile(
    input: { file: File; eventName?: string; classify?: boolean; workEmailsOnly?: boolean },
    onProgress?: (progress: JobProgress | null) => void,
  ) {
    setError(null);
    const result = await uploadLeadsFile(input, onProgress);
    const eventName = input.eventName?.trim() || input.file.name.replace(/\.(csv|tsv|xlsx)$/i, "");
    const linkedThread = result.event_id
      ? { ...activeThread, title: eventName, eventId: result.event_id, eventName }
      : activeThread;
    const threadWithSummary = appendUploadSummaryMessage(linkedThread, input.file.name, result);
    setThreads((current) => replaceThread(current, threadWithSummary));
    if (result.event_id) {
      await saveChatThread(threadWithSummary.id, threadWithSummary.title, result.event_id);
      persistedThreadIds.current.add(threadWithSummary.id);
    }
    void persistMessages(threadWithSummary, threadWithSummary.messages.slice(-1));
  }

  function handleNewThread() {
    const thread = { ...createThread(), title: "Miscellaneous" };
    setThreads((current) => [thread, ...current]);
    setActiveThreadId(thread.id);
    setActiveScreen("chat");
    setSelectedLeadId(null);
    setPanelOpen(false);
  }

  async function handleSelectThread(threadId: string) {
    setActiveThreadId(threadId);
    if (!persistedThreadIds.current.has(threadId) || loadedThreadIds.current.has(threadId)) return;
    try {
      const stored = await getChatThread(threadId);
      loadedThreadIds.current.add(threadId);
      setThreads((current) =>
        current.map((thread) =>
          thread.id === threadId
            ? {
                ...thread,
                messages: stored.messages.map((message) => ({
                  id: message.id,
                  role: message.role,
                  content: message.content,
                  toolTrace: message.tool_trace ?? undefined,
                })),
              }
            : thread,
        ),
      );
    } catch (err) {
      console.warn("Could not load conversation history", err);
    }
  }

  function commitThreadDelete(threadId: string) {
    if (!persistedThreadIds.current.has(threadId)) return;
    persistedThreadIds.current.delete(threadId);
    loadedThreadIds.current.delete(threadId);
    void deleteChatThread(threadId).catch((err) => console.warn("Could not delete the saved conversation", err));
  }

  function handleDeleteThread(threadId: string) {
    const index = threads.findIndex((thread) => thread.id === threadId);
    if (index === -1) return;
    if (pendingDelete) {
      if (pendingDeleteTimer.current) clearTimeout(pendingDeleteTimer.current);
      commitThreadDelete(pendingDelete.thread.id);
    }
    const thread = threads[index];
    const remaining = threads.filter((item) => item.id !== threadId);
    const next = remaining.length ? remaining : [{ ...createThread(), title: "Miscellaneous" }];
    setThreads(next);
    if (activeThreadId === threadId) setActiveThreadId(next[0].id);
    setPendingDelete({ thread, index });
    pendingDeleteTimer.current = setTimeout(() => {
      commitThreadDelete(threadId);
      setPendingDelete(null);
    }, 6000);
  }

  function handleUndoDelete() {
    if (!pendingDelete) return;
    if (pendingDeleteTimer.current) clearTimeout(pendingDeleteTimer.current);
    const { thread, index } = pendingDelete;
    setThreads((current) => {
      const next = [...current];
      next.splice(Math.min(index, next.length), 0, thread);
      return next;
    });
    setPendingDelete(null);
  }

  function handleOpenLead(personId: string) {
    setSelectedLeadId(personId);
    setPanelOpen(true);
  }

  function handleOpenReview(tab: "flagged" | "needs-review", eventId?: string) {
    setReviewTab(tab);
    setReviewEventId(eventId ?? "");
    if (tab === "needs-review") setNeedsReviewType("all");
    setActiveScreen("review");
  }

  function handleOpenEventChat(eventId: string, eventName: string) {
    const existing = threads.find((thread) => thread.eventId === eventId);
    if (existing) {
      setActiveThreadId(existing.id);
      void handleSelectThread(existing.id);
    } else {
      const thread = { ...createThread(), title: eventName, eventId, eventName };
      setThreads((current) => [thread, ...current]);
      setActiveThreadId(thread.id);
    }
    setActiveScreen("chat");
    setSelectedLeadId(null);
    setPanelOpen(false);
  }

  const meta = screenMeta(activeScreen);

  if (authState === "checking") {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-canvas text-sm text-stone-500">
        Checking access...
      </main>
    );
  }

  if (authState === "unauthenticated") {
    return <LoginGate onAuthenticated={() => setAuthState("authenticated")} />;
  }

  return (
    <main className="flex h-dvh min-h-[720px] overflow-hidden text-ink">
      <NavRail
        activeScreen={activeScreen}
        reviewCount={reviewCount}
        onSelect={setActiveScreen}
        onLogout={handleLogout}
      />

      {activeScreen === "chat" ? (
        <ThreadSidebar
          threads={threads}
          activeThreadId={activeThreadId}
          open={sidebarOpen}
          onToggle={() => setSidebarOpen((value) => !value)}
          onNewThread={handleNewThread}
          onSelectThread={handleSelectThread}
          onDeleteThread={handleDeleteThread}
        />
      ) : null}

      <section className="flex min-w-0 flex-1 flex-col border-x border-stone-200/80 bg-canvas/95">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-stone-200/80 px-5">
          <div>
            <p className="text-sm font-semibold text-stone-950">{meta.title}</p>
            <p className="text-xs text-stone-500">{meta.subtitle}</p>
          </div>
          <Button
            variant="ghost"
            className="h-8 px-2"
            onClick={() => setPanelOpen((value) => !value)}
            aria-label={panelOpen ? "Close context panel" : "Open context panel"}
          >
            {panelOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
          </Button>
        </header>

        <div className={activeScreen === "chat" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <>
            <ChatThread
              thread={activeThread}
              isLoading={isLoading}
              activity={agentActivity}
              liveToolTrace={liveToolTrace}
              error={error}
              onOpenLead={handleOpenLead}
              onSuggestion={handleSend}
              onRetry={handleRetry}
            />
            <Composer onSend={handleSend} onUploadFile={handleUploadFile} disabled={isLoading} />
          </>
        </div>
        <div className={activeScreen === "events" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <EventDashboard
            refreshVersion={dataRefreshVersion}
            onOpenLead={handleOpenLead}
            onOpenReview={handleOpenReview}
            onOpenEventChat={handleOpenEventChat}
          />
        </div>
        <div className={activeScreen === "pipeline" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <PipelineDashboard refreshVersion={dataRefreshVersion} onOpenLead={handleOpenLead} />
        </div>
        <div className={activeScreen === "review" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <ReviewScreen
            activeTab={reviewTab}
            initialEventId={reviewEventId}
            needsReviewType={needsReviewType}
            onTabChange={setReviewTab}
            onNeedsReviewTypeChange={setNeedsReviewType}
            onOpenLead={handleOpenLead}
            onReviewChanged={handleReviewChanged}
          />
        </div>
        <div className={activeScreen === "usage" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <UsageDashboard refreshVersion={dataRefreshVersion} />
        </div>
        <div className={activeScreen === "source" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <SourceDashboard />
        </div>
        <div className={activeScreen === "health" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <HealthDashboard refreshVersion={dataRefreshVersion} onOpenReview={handleOpenReview} />
        </div>
      </section>

      <ContextPanel
        open={panelOpen}
        personId={selectedLeadId}
        refreshVersion={dataRefreshVersion}
        onClose={() => setPanelOpen(false)}
      />

      {pendingDelete ? (
        <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-xl border border-stone-700 bg-stone-900 px-4 py-2.5 text-sm text-stone-50 shadow-lg animate-soft-in">
          <span>Conversation deleted.</span>
          <button
            className="rounded-md px-2 py-1 font-semibold text-accent-soft transition-colors hover:bg-stone-700"
            onClick={handleUndoDelete}
          >
            Undo
          </button>
        </div>
      ) : null}
    </main>
  );
}

function replaceThread(threads: Thread[], nextThread: Thread): Thread[] {
  return threads.map((thread) => (thread.id === nextThread.id ? nextThread : thread));
}

function screenMeta(screen: AppScreen) {
  switch (screen) {
    case "events":
      return { title: "Events", subtitle: "Results and top leads for each event" };
    case "pipeline":
      return { title: "Pipeline", subtitle: "MQLs and sales-ready leads across all events" };
    case "review":
      return { title: "Review", subtitle: "Leads that need a human decision" };
    case "health":
      return { title: "Health", subtitle: "How trustworthy the data is" };
    case "usage":
      return { title: "API Usage", subtitle: "Credits and estimated provider cost by event" };
    case "source":
      return { title: "Source", subtitle: "Upload any ICP and source a reviewable lead shortlist" };
    case "chat":
    default:
      return { title: "mint agent", subtitle: "Ask anything about your event leads" };
  }
}
