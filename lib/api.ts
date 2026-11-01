import type {
  AgentChatResponse,
  ApiUsageSummary,
  AgentStreamEvent,
  AuthStatus,
  BackendMessage,
  DataQualitySummary,
  IntegrationHealth,
  EventAnalytics,
  EventRecord,
  EventSource,
  EventSummary,
  FlaggedLead,
  IngestSummary,
  JobProgress,
  EnrichmentIntegrationTest,
  JobStatus,
  LeadDetail,
  LeadFilters,
  LeadPage,
  NeedsReviewLead,
  PipelineSummary,
  ReclassifyInput,
  ResolveFlaggedInput,
  ResolveFlaggedResponse,
  ReviewPage,
  StoredChatMessage,
  StoredChatThread,
  ThreadSummary,
  UploadLeadsInput,
  AgentBoxCandidate,
  AgentBoxSourceSummary,
  ExportArtifact,
  RegionBackfillResult,
} from "./types";

export function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase()}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`mint API ${response.status}: ${detail || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function chatWithAgent(messages: BackendMessage[]): Promise<AgentChatResponse> {
  return requestJson<AgentChatResponse>("/agent/chat", {
    method: "POST",
    body: JSON.stringify({ messages }),
  });
}

export async function chatWithAgentStream(
  messages: BackendMessage[],
  onEvent?: (event: AgentStreamEvent) => void,
): Promise<AgentChatResponse> {
  const response = await fetch(`${apiBase()}/agent/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(`mint API ${response.status}: ${detail || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AgentChatResponse | null = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const boundary = buffer.indexOf("\n\n");
      if (boundary < 0) break;
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice("data:".length).trim())
        .join("");
      if (!data) continue;
      const event = JSON.parse(data) as AgentStreamEvent;
      if (event.type === "heartbeat") {
        continue;
      }
      if (event.type === "error") {
        throw new Error(event.message);
      }
      if (event.type === "result") {
        result = { reply: event.reply, tool_trace: event.tool_trace };
      }
      onEvent?.(event);
    }
  }

  if (!result) {
    throw new Error("The agent stream ended without a reply.");
  }
  return result;
}

export async function getAuthStatus(): Promise<AuthStatus> {
  return requestJson<AuthStatus>("/auth/me");
}

export async function login(password: string): Promise<AuthStatus> {
  return requestJson<AuthStatus>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export async function logout(): Promise<AuthStatus> {
  return requestJson<AuthStatus>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function listChatThreads(): Promise<ThreadSummary[]> {
  return requestJson<ThreadSummary[]>("/chat/threads");
}

export async function getChatThread(threadId: string): Promise<StoredChatThread> {
  return requestJson<StoredChatThread>(`/chat/threads/${encodeURIComponent(threadId)}`);
}

export async function saveChatThread(threadId: string, title: string, eventId?: string | null): Promise<ThreadSummary> {
  return requestJson<ThreadSummary>(`/chat/threads/${encodeURIComponent(threadId)}`, {
    method: "PUT",
    body: JSON.stringify({ title, ...(eventId ? { event_id: eventId } : {}) }),
  });
}

export async function appendChatMessages(threadId: string, messages: StoredChatMessage[]): Promise<void> {
  await requestJson<{ appended: number }>(`/chat/threads/${encodeURIComponent(threadId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ messages }),
  });
}

export async function deleteChatThread(threadId: string): Promise<void> {
  await requestJson<{ deleted: boolean }>(`/chat/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}

export async function getLeadDetail(personId: string): Promise<LeadDetail> {
  return requestJson<LeadDetail>(`/leads/${encodeURIComponent(personId)}`);
}

export async function listLeads(filters: LeadFilters = {}): Promise<LeadPage> {
  return requestJson<LeadPage>(`/leads${queryString(filters)}`);
}

type ListEventsParams = {
  status?: string;
  source?: EventSource;
  refreshLuma?: boolean;
};

export async function listEvents(params: string | ListEventsParams = {}): Promise<EventRecord[]> {
  const filters = typeof params === "string" ? { status: params } : params;
  return requestJson<EventRecord[]>(
    `/events${queryString({
      status: filters.status ?? "all",
      source: filters.source,
      refresh_luma: filters.refreshLuma,
    })}`,
  );
}

export async function updateEventEnrichment(eventId: string, enrichmentEnabled: boolean): Promise<EventRecord> {
  return requestJson<EventRecord>(`/events/${encodeURIComponent(eventId)}/enrichment`, {
    method: "PATCH",
    body: JSON.stringify({ enrichment_enabled: enrichmentEnabled }),
  });
}

export async function backfillEvent(
  eventId: string,
  classify = true,
  onProgress?: (progress: JobProgress | null) => void,
): Promise<IngestSummary> {
  const job = await requestJson<JobStatus>(`/events/${encodeURIComponent(eventId)}/backfill`, {
    method: "POST",
    body: JSON.stringify({ classify }),
  });
  return waitForJob(job.id, onProgress);
}

export async function retryUnresolvedEvent(
  eventId: string,
  onProgress?: (progress: JobProgress | null) => void,
): Promise<IngestSummary & {
  retry_candidates?: number;
  reenrichment_candidates?: number;
  reclassified_count?: number;
}> {
  const job = await requestJson<JobStatus>(`/events/${encodeURIComponent(eventId)}/retry-unresolved`, {
    method: "POST",
    body: JSON.stringify({ classify: true }),
  });
  return waitForJob(job.id, onProgress);
}

export async function backfillEventRegions(
  eventId: string,
  onProgress?: (progress: JobProgress | null) => void,
  retryUnresolved = false,
): Promise<RegionBackfillResult> {
  const job = await requestJson<JobStatus>(`/events/${encodeURIComponent(eventId)}/region-backfill`, {
    method: "POST",
    body: JSON.stringify({ retry_unresolved: retryUnresolved }),
  });
  return waitForJob<RegionBackfillResult>(job.id, onProgress);
}

export async function getEventSummary(eventId: string): Promise<EventSummary> {
  return requestJson<EventSummary>(`/events/${encodeURIComponent(eventId)}/summary`);
}

export async function exportEventLeads(
  eventId: string,
  input: { tiers?: string[]; profile?: "standard" | "icp" | "mql"; format?: "xlsx" | "csv" } = {},
): Promise<ExportArtifact> {
  return requestJson<ExportArtifact>(`/events/${encodeURIComponent(eventId)}/export`, {
    method: "POST",
    body: JSON.stringify({ tiers: input.tiers ?? ["P0", "P1"], profile: input.profile ?? "mql", format: input.format ?? "xlsx" }),
  });
}

export type HubSpotSyncResult = {
  list_name?: string;
  imported?: number;
  created?: number;
  updated?: number;
  [key: string]: unknown;
};

export async function syncEventToHubSpot(
  eventId: string,
  input: { tiers?: string[]; marketable?: boolean; create_list?: boolean } = {},
): Promise<HubSpotSyncResult> {
  return requestJson<HubSpotSyncResult>(`/events/${encodeURIComponent(eventId)}/hubspot-sync`, {
    method: "POST",
    body: JSON.stringify({ tiers: input.tiers ?? ["P0", "P1"], marketable: input.marketable ?? false, create_list: input.create_list ?? true }),
  });
}

export async function getEventAnalytics(eventId: string): Promise<EventAnalytics> {
  return requestJson<EventAnalytics>(`/events/${encodeURIComponent(eventId)}/analytics`);
}

export async function saveEventRatings(
  eventId: string,
  input: { scores: Record<string, number>; notes: string | null },
): Promise<EventAnalytics> {
  return requestJson<EventAnalytics>(`/events/${encodeURIComponent(eventId)}/ratings`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function getPipelineSummary(): Promise<PipelineSummary> {
  return requestJson<PipelineSummary>("/pipeline/summary");
}

export async function runAgentBoxSource(
  campaign?: string,
  onProgress?: (progress: JobProgress | null) => void,
  limit?: number,
): Promise<AgentBoxSourceSummary> {
  const job = await requestJson<{ job_id: string; status: string }>("/source/agentbox", {
    method: "POST",
    body: JSON.stringify({
      ...(campaign ? { campaign } : {}),
      ...(limit ? { limit } : {}),
    }),
  });
  return waitForJob<AgentBoxSourceSummary>(job.job_id, onProgress);
}

export async function runICPSource(
  icpName: string,
  icpContent: string,
  onProgress?: (progress: JobProgress | null) => void,
  limit?: number,
): Promise<AgentBoxSourceSummary> {
  const job = await requestJson<{ job_id: string; status: string }>("/source/icp", {
    method: "POST",
    body: JSON.stringify({
      icp_name: icpName,
      icp_content: icpContent,
      ...(limit ? { limit } : {}),
    }),
  });
  return waitForJob<AgentBoxSourceSummary>(job.job_id, onProgress);
}

export async function parseICPFile(file: File): Promise<{ name: string; content: string }> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${apiBase()}/source/icp/parse`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`mint API ${response.status}: ${detail || response.statusText}`);
  }
  return response.json() as Promise<{ name: string; content: string }>;
}

export async function exportICPShortlist(rows: AgentBoxCandidate[]): Promise<{ download_url: string }> {
  return requestJson<{ download_url: string }>("/source/icp/export", {
    method: "POST",
    body: JSON.stringify({ rows }),
  });
}

export async function exportAgentBoxShortlist(rows: AgentBoxCandidate[]): Promise<{ download_url: string }> {
  return requestJson<{ download_url: string }>("/source/agentbox/export", {
    method: "POST",
    body: JSON.stringify({ rows }),
  });
}

export async function getDataQualitySummary(): Promise<DataQualitySummary> {
  return requestJson<DataQualitySummary>("/health/data-quality");
}

export async function getIntegrationHealth(): Promise<IntegrationHealth> {
  return requestJson<IntegrationHealth>("/health/integrations");
}

export async function getApiUsageSummary(): Promise<ApiUsageSummary> {
  return requestJson<ApiUsageSummary>("/usage/events");
}

export async function listFlaggedReview(params: { eventId?: string; limit?: number; offset?: number } = {}): Promise<ReviewPage<FlaggedLead>> {
  return requestJson<ReviewPage<FlaggedLead>>(
    `/review/flagged${queryString({ event_id: params.eventId, limit: params.limit ?? 50, offset: params.offset ?? 0 })}`,
  );
}

export async function listNeedsReview(
  params: { type?: "classification" | "resolution" | "all"; eventId?: string; limit?: number; offset?: number } = {},
): Promise<ReviewPage<NeedsReviewLead>> {
  return requestJson<ReviewPage<NeedsReviewLead>>(
    `/review/needs-review${queryString({
      type: params.type ?? "all",
      event_id: params.eventId,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    })}`,
  );
}

export async function clearReviewQueues(eventId?: string): Promise<{ people_dismissed: number; classifications_reviewed: number }> {
  return requestJson(`/review/clear${queryString({ event_id: eventId })}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function getReviewCounts(): Promise<{ flagged: number; needsReview: number }> {
  const [flagged, needsReview] = await Promise.all([
    listFlaggedReview({ limit: 1, offset: 0 }),
    listNeedsReview({ type: "all", limit: 1, offset: 0 }),
  ]);
  return { flagged: flagged.total, needsReview: needsReview.total };
}

export async function resolveFlaggedLead(personId: string, input: ResolveFlaggedInput): Promise<ResolveFlaggedResponse> {
  return requestJson<ResolveFlaggedResponse>(`/review/flagged/${encodeURIComponent(personId)}/resolve`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function resolveIdentityLead(personId: string, input: ResolveFlaggedInput): Promise<ResolveFlaggedResponse> {
  return requestJson<ResolveFlaggedResponse>(`/review/${encodeURIComponent(personId)}/resolve-identity`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function reclassifyLead(personId: string, input: ReclassifyInput): Promise<LeadDetail> {
  return requestJson<LeadDetail>(`/review/${encodeURIComponent(personId)}/reclassify`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function uploadLeadsFile(
  input: UploadLeadsInput,
  onProgress?: (progress: JobProgress | null) => void,
): Promise<IngestSummary> {
  const form = new FormData();
  form.append("file", input.file);
  appendIfPresent(form, "event_name", input.eventName);
  appendIfPresent(form, "event_date", input.eventDate);
  appendIfPresent(form, "event_location", input.eventLocation);
  appendIfPresent(form, "event_url", input.eventUrl);
  form.append("classify", input.classify ? "true" : "false");
  form.append("background", input.background === false ? "false" : "true");
  form.append("work_emails_only", input.workEmailsOnly ? "true" : "false");

  const response = await fetch(`${apiBase()}/events/upload`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`mint API ${response.status}: ${detail || response.statusText}`);
  }
  const payload = await response.json();
  if (payload?.id && payload?.status && payload?.name) {
    return waitForJob(payload.id, onProgress);
  }
  return payload as IngestSummary;
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return requestJson<JobStatus>(`/jobs/${encodeURIComponent(jobId)}`);
}

export async function testEnrichmentIntegrations(): Promise<EnrichmentIntegrationTest> {
  return requestJson<EnrichmentIntegrationTest>("/health/integrations/test-enrichment", {
    method: "POST",
  });
}

export async function waitForJob<T = IngestSummary>(
  jobId: string,
  onProgress?: (progress: JobProgress | null) => void,
  intervalMs = 3000,
): Promise<T> {
  for (;;) {
    await sleep(intervalMs);
    const job = await getJobStatus(jobId);
    if (job.status === "succeeded") {
      if (!job.result) throw new Error("Background job finished without a result.");
      return job.result as unknown as T;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Background job failed.");
    }
    onProgress?.(job.progress ?? null);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function appendIfPresent(form: FormData, key: string, value?: string): void {
  const trimmed = value?.trim();
  if (trimmed) form.append(key, trimmed);
}

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
