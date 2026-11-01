import { humanizeLeadErrors } from "./leadErrors";
import type {
  AgentChatResponse,
  AgentStreamEvent,
  BackendMessage,
  IngestSummary,
  ExportArtifact,
  LeadReference,
  Thread,
  ToolTraceItem,
} from "./types";

const UUID_RE = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

export function createThread(firstMessage?: string): Thread {
  const now = new Date().toISOString();
  return {
    id: newId(),
    title: firstMessage ? titleFromMessage(firstMessage) : "New conversation",
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

export function appendUserMessage(thread: Thread, content: string): Thread {
  const trimmed = content.trim();
  if (!trimmed) return thread;
  return appendMessage(thread, {
    id: newId(),
    role: "user",
    content: trimmed,
  });
}

export function appendAssistantTurn(thread: Thread, result: AgentChatResponse): Thread {
  return appendMessage(thread, {
    id: newId(),
    role: "assistant",
    content: result.reply,
    toolTrace: result.tool_trace ?? [],
  });
}

export function appendUploadSummaryMessage(thread: Thread, filename: string, summary: IngestSummary): Thread {
  return appendMessage(thread, {
    id: newId(),
    role: "assistant",
    content: uploadSummaryText(filename, summary),
  });
}

export function backendMessages(thread: Thread): BackendMessage[] {
  const messages = thread.messages.map(({ role, content }) => ({ role, content }));
  if (!thread.eventId) return messages;
  return [
    {
      role: "user",
      content: `[Mint event context: ${thread.eventName || thread.title}; event_id=${thread.eventId}. Default all event-specific queries and actions to this event.]`,
    },
    ...messages,
  ];
}

export function titleFromMessage(message: string): string {
  const compact = message.trim().replace(/\s+/g, " ");
  if (!compact) return "New conversation";
  return compact.length > 42 ? `${compact.slice(0, 39)}...` : compact;
}

export function summarizeToolTrace(item: ToolTraceItem): string {
  if (item.error) return item.error;
  return item.short_result || item.result_summary || "Completed";
}

const TOOL_ACTIVITY_LABELS: Record<string, string> = {
  list_leads: "Looking up leads",
  export_leads: "Creating the lead export",
  get_lead: "Opening the lead",
  list_events: "Listing events",
  event_summary: "Summarizing the event",
  pipeline_summary: "Rolling up the pipeline",
  data_quality_summary: "Checking data health",
  list_flagged: "Checking the flagged queue",
  list_needs_review: "Checking the review queue",
  resolve_flagged: "Saving the company resolution",
  reclassify_lead: "Updating the tier",
  pull_event: "Pulling the event from Luma",
  classify_event: "Classifying the event's leads",
};

export function agentActivityLabel(event: AgentStreamEvent): string {
  if (event.type === "tool") {
    return TOOL_ACTIVITY_LABELS[event.name] ?? `Running ${event.name.replaceAll("_", " ")}`;
  }
  return "Thinking through the workspace";
}

export function reduceLiveToolEvents(items: ToolTraceItem[], event: AgentStreamEvent): ToolTraceItem[] {
  if (event.type !== "tool") return items;
  if (event.status === "started") {
    return [
      ...items,
      {
        name: event.name,
        input: event.input ?? {},
        status: event.status,
      },
    ];
  }

  const index = findLastToolIndex(items, event.name);
  if (index === -1) {
    const item: ToolTraceItem = {
      name: event.name,
      input: event.input ?? {},
      status: event.status,
    };
    if (event.short_result !== undefined) item.short_result = event.short_result;
    if (event.error !== undefined) item.error = event.error;
    return [...items, item];
  }

  return items.map((item, itemIndex) => {
    if (itemIndex !== index) return item;
    const next: ToolTraceItem = {
      ...item,
      status: event.status,
    };
    if (event.short_result !== undefined) next.short_result = event.short_result;
    if (event.error !== undefined) next.error = event.error;
    return next;
  });
}

export function assistantDraftTitle(items: ToolTraceItem[]): string {
  if (!items.length) return "Working";
  return `Running ${items.length} ${items.length === 1 ? "tool" : "tools"}`;
}

export function uploadSummaryText(filename: string, summary: IngestSummary): string {
  const base = [
    `Uploaded \`${filename}\`: ingested ${summary.total_guests} rows`,
    `approved ${summary.going_count}`,
    `enriched ${summary.enriched_count}`,
    `skipped ${summary.skipped_fresh_count} fresh`,
    `flagged ${summary.flagged_count}`,
  ].join(", ");
  const classification = summary.classification
    ? ` Classified ${summary.classification.classified_count} leads.`
    : "";
  const eventText = summary.event_id
    ? `Event \`${summary.event_id}\` is ready to query.`
    : "Leads were ingested without an event.";
  const nextStep = summary.classification || !summary.event_id ? "" : " Want me to classify them?";
  return `${base}.${classification} ${eventText}${nextStep}${leadErrorsText(summary)}`;
}

function leadErrorsText(summary: IngestSummary): string {
  const leadErrors = summary.lead_errors ?? [];
  if (!leadErrors.length) return "";
  const shown = leadErrors
    .slice(0, 10)
    .map((lead) => `- ${lead.email}: ${humanizeLeadErrors(lead.errors).join("; ")}`);
  const more = leadErrors.length > 10 ? `\n- ...and ${leadErrors.length - 10} more` : "";
  const noun = leadErrors.length === 1 ? "1 lead had" : `${leadErrors.length} leads had`;
  return `\n\n${noun} enrichment problems:\n${shown.join("\n")}${more}`;
}

export function extractLeadReferences(text: string): LeadReference[] {
  const refs: LeadReference[] = [];
  const seen = new Set<string>();
  const markdownRe = new RegExp(`\\[([^\\]]+)\\]\\(lead:(${UUID_RE})\\)`, "g");
  const pathRe = new RegExp(`/leads/(${UUID_RE})`, "g");

  for (const match of text.matchAll(markdownRe)) {
    addRef(refs, seen, match[2], match[1]);
  }
  for (const match of text.matchAll(pathRe)) {
    addRef(refs, seen, match[1], match[1].slice(0, 8));
  }
  return refs;
}

export function extractExportArtifacts(message: { toolTrace?: ToolTraceItem[] }): ExportArtifact[] {
  const artifacts: ExportArtifact[] = [];
  const seen = new Set<string>();
  for (const item of message.toolTrace ?? []) {
    const artifact = item.artifact;
    if (!artifact?.download_url || !artifact.filename || seen.has(artifact.download_url)) continue;
    artifacts.push(artifact);
    seen.add(artifact.download_url);
  }
  return artifacts;
}

function appendMessage(thread: Thread, message: Thread["messages"][number]): Thread {
  const updatedAt = new Date().toISOString();
  return {
    ...thread,
    title: thread.messages.length === 0 && message.role === "user" ? titleFromMessage(message.content) : thread.title,
    updatedAt,
    messages: [...thread.messages, message],
  };
}

function addRef(refs: LeadReference[], seen: Set<string>, personId: string, label: string): void {
  const key = personId.toLowerCase();
  if (seen.has(key)) return;
  seen.add(key);
  refs.push({ personId, label });
}

function findLastToolIndex(items: ToolTraceItem[], name: string): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].name === name) return index;
  }
  return -1;
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
