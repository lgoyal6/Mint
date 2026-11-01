export type ChatRole = "user" | "assistant";

export type BackendMessage = {
  role: ChatRole;
  content: string;
};

export type ToolTraceItem = {
  name: string;
  input: Record<string, unknown>;
  status?: "started" | "finished" | "failed";
  short_result?: string | null;
  result_summary?: string | null;
  error?: string | null;
  artifact?: ExportArtifact | null;
};

export type ExportArtifact = {
  filename: string;
  download_url: string;
  row_count?: number | null;
  format?: string | null;
};

export type AgentChatResponse = {
  reply: string;
  tool_trace: ToolTraceItem[];
};

export type AgentStreamEvent =
  | { type: "heartbeat" }
  | { type: "thinking" }
  | {
      type: "tool";
      status: "started" | "finished" | "failed";
      name: string;
      input?: Record<string, unknown>;
      short_result?: string | null;
      error?: string | null;
    }
  | ({ type: "result" } & AgentChatResponse)
  | { type: "error"; message: string };

export type AuthStatus = {
  authenticated: boolean;
};

export type ThreadMessage = BackendMessage & {
  id: string;
  toolTrace?: ToolTraceItem[];
};

export type Thread = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ThreadMessage[];
  eventId?: string | null;
  eventName?: string | null;
};

export type ThreadSummary = {
  id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
  event_id?: string | null;
  event_name?: string | null;
};

export type StoredChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  tool_trace?: ToolTraceItem[] | null;
};

export type StoredChatThread = ThreadSummary & {
  messages: StoredChatMessage[];
};

export type LeadReference = {
  personId: string;
  label: string;
};

export type PersonProfile = {
  id: string;
  email: string;
  first_name?: string | null;
  last_name?: string | null;
  name?: string | null;
  title?: string | null;
  seniority?: string | null;
  linkedin_url?: string | null;
  self_reported_title?: string | null;
  self_reported_company?: string | null;
  self_reported_building?: string | null;
  affiliation_state?: string | null;
  resolution_confidence?: string | null;
  resolution_evidence?: Record<string, unknown>;
  event_names?: string[];
  candidate_companies?: Array<Record<string, unknown>>;
  apollo_match_status?: string | null;
  apollo_person_data?: Record<string, unknown> | null;
  exa_person_role_signals?: string | null;
  exa_people_search_results?: Array<Record<string, unknown>> | null;
  clay_person_data?: Record<string, unknown> | null;
  events_registered?: number;
  events_attended?: number;
  last_event_at?: string | null;
  enrichment_data_quality?: string | null;
};

export type CompanyProfile = {
  id: string;
  name?: string | null;
  domain: string;
  apollo_org_industry?: string | null;
  apollo_org_employee_count?: number | null;
  apollo_org_founded_year?: number | null;
  apollo_org_funding_stage?: string | null;
  apollo_org_total_funding?: string | null;
  apollo_org_revenue?: string | null;
  apollo_org_technologies?: string[];
  apollo_org_data?: Record<string, unknown> | null;
  exa_what_they_do?: string | null;
  exa_target_vertical?: string | null;
  exa_stage?: string | null;
  exa_data_quality?: string | null;
  exa_enrichment?: Record<string, unknown> | null;
  exa_grounding?: Array<Record<string, unknown>> | null;
};

export type ClassificationProfile = {
  id: string;
  tier: "P0" | "P1" | "P2" | "NR" | "NP" | string;
  np_trigger?: string | null;
  product_fit?: string[];
  primary_product?: string | null;
  confidence?: number | null;
  reasoning?: string | null;
  key_signal?: string | null;
  key_risk?: string | null;
  flags?: string[];
  review_status?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  reclassified_to?: string | null;
  pipeline_version?: string | null;
  classified_at?: string | null;
};

export type RegistrationProfile = {
  id: string;
  event_id: string;
  event_name?: string | null;
  luma_event_id?: string | null;
  approval_status?: string | null;
  checked_in_at?: string | null;
  survey_responses?: Record<string, unknown> | null;
};

export type LeadDetail = {
  person: PersonProfile;
  company?: CompanyProfile | null;
  current_classification?: ClassificationProfile | null;
  classification_history?: ClassificationProfile[];
  registrations?: RegistrationProfile[];
};

export type LeadSummary = {
  email: string;
  name?: string | null;
  title?: string | null;
  resolved_company?: string | null;
  resolved_domain?: string | null;
  tier?: "P0" | "P1" | "P2" | "NR" | "NP" | string | null;
  primary_product?: "maas" | "dedicated" | "gpu" | string | null;
  product_fit?: string[];
  confidence?: number | null;
  target_vertical?: string | null;
  location?: string | null;
  region?: string | null;
  review_status?: string | null;
  key_signal?: string | null;
  current_classification_id?: string | null;
  person_id: string;
};

export type LeadPage = {
  total: number;
  items: LeadSummary[];
};

export type LeadFilters = {
  tier?: "P0" | "P1" | "P2" | "NR" | "NP";
  primary_product?: "maas" | "dedicated" | "gpu";
  event_id?: string;
  vertical?: string;
  min_confidence?: number;
  review_status?: string;
  resolution_confidence?: "high" | "medium" | "low" | "flagged";
  search?: string;
  sort?: string;
  limit?: number;
  offset?: number;
};

export type EventSource = "all" | "luma" | "csv";

export type AgentBoxSourceSummary = {
  leads: number;
  bands: Record<string, number>;
  needs_review: number;
  candidates: AgentBoxCandidate[];
  rejected: AgentBoxCandidate[];
  mode: "live" | "dry-run" | string;
  campaign: string;
  icp_name?: string;
};

export type AgentBoxCandidate = {
  apollo_person_id?: string | null;
  name?: string | null;
  title?: string | null;
  company?: string | null;
  domain?: string | null;
  headcount?: number | null;
  funding_stage?: string | null;
  total_funding?: number | null;
  days_since_funding?: number | null;
  linkedin_url?: string | null;
  email?: string | null;
  band: string;
  total: number;
  needs_review: boolean;
  suggested_sender: string;
  runtime_fit: { note?: string };
  workload_review: string;
  [key: string]: unknown;
};

export type EventRecord = {
  id: string;
  luma_event_id?: string | null;
  name: string;
  event_date?: string | null;
  location?: string | null;
  url?: string | null;
  status?: string | null;
  guest_count?: number | null;
  synced_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  source?: EventSource | string | null;
  enrichment_enabled?: boolean | null;
};

export type EventSummary = {
  event: Pick<EventRecord, "id" | "luma_event_id" | "name" | "status">;
  counts: {
    total_guests: number;
    going_count: number;
    resolved: number;
    parked_low: number;
    flagged: number;
    classified: number;
  };
  tier_breakdown: Record<string, number>;
  product_fit_breakdown: Record<string, number>;
  np_trigger_breakdown: Record<string, number>;
  processing_failure_breakdown?: Record<string, number>;
  evidence_gap_breakdown?: Record<string, number>;
  qualification_outcome_breakdown?: Record<string, number>;
  p0_p1_leads: LeadSummary[];
  resolution_funnel: {
    high_medium: number;
    low: number;
    flagged: number;
  };
};

export type PipelineSummary = {
  p0_p1_by_primary_product: Record<string, number>;
  repeat_attendees: Array<{
    person_id: string;
    email: string;
    events_registered: number;
    events_attended: number;
  }>;
  companies_with_multiple_people: Array<{
    company_id: string;
    name?: string | null;
    domain: string;
    person_count: number;
  }>;
  nurture_reengage: LeadSummary[];
};

export type DataQualitySummary = {
  resolution: {
    total_people: number;
    high_medium: number;
    low: number;
    flagged: number;
    resolution_rate: number;
  };
  apollo_match_rate: number;
  data_quality_distribution: Record<string, number>;
  np_trigger_breakdown: Record<string, number>;
  promising_low_confidence: number;
  last_luma_sync_at?: string | null;
};

export type ProviderHealth = {
  configured: boolean;
  verified: boolean;
  status: string;
  credit_cost?: "none" | "not_checked" | string;
  http_status?: number | null;
  detail?: string;
};

export type IntegrationHealth = {
  status: "ok" | "degraded";
  luma: ProviderHealth;
  apollo: ProviderHealth;
  exa: ProviderHealth;
  hubspot: ProviderHealth;
  clay: { configured: boolean };
};

export type ProviderUsageTotals = {
  requests: number;
  credits: number;
  estimated_cost_usd: number;
};

export type EventApiUsage = {
  event: Pick<EventRecord, "id" | "name" | "event_date" | "status">;
  apollo: ProviderUsageTotals;
  exa: ProviderUsageTotals;
  total_estimated_cost_usd: number;
};

export type ApiUsageSummary = {
  events: EventApiUsage[];
  apollo_dollar_rate_configured: boolean;
  pricing: { apollo: string; exa: string };
};

export type FlaggedLead = {
  person_id: string;
  email: string;
  name?: string | null;
  title?: string | null;
  self_reported_company?: string | null;
  candidate_companies: Array<{
    domain?: string | null;
    name?: string | null;
    source?: string | null;
    note?: string | null;
  }>;
  resolution_evidence?: Record<string, unknown>;
  event_names?: string[];
};

export type NeedsReviewLead = {
  person_id: string;
  email: string;
  name?: string | null;
  title?: string | null;
  resolved_company?: string | null;
  resolved_domain?: string | null;
  tier?: string | null;
  confidence?: number | null;
  primary_product?: string | null;
  review_status?: string | null;
  resolution_confidence?: string | null;
  reason: string;
  source: "classification" | "resolution";
  event_names?: string[];
};

export type ReviewPage<T> = {
  total: number;
  items: T[];
};

export type ResolveFlaggedInput =
  | { chosen_domain: string; company_domain?: never; company_name?: never; chosen_company_id?: never; refresh_person?: boolean; remember_aliases?: boolean }
  | { chosen_domain?: never; company_domain: string; company_name?: string; chosen_company_id?: never; refresh_person?: boolean; remember_aliases?: boolean }
  | { chosen_domain?: never; company_domain?: never; company_name?: never; chosen_company_id: string; refresh_person?: boolean; remember_aliases?: boolean };

export type ResolveFlaggedResponse = {
  resolution: {
    person_id?: string;
    company_id?: string;
    domain?: string;
    company_name?: string | null;
    affiliation_state?: string;
    resolution_confidence?: string;
  };
  job_id?: string | null;
  updated_lead?: LeadDetail | null;
};

export type ReclassifyInput = {
  new_tier: "P0" | "P1" | "P2" | "NR" | "NP";
  reason: string;
  reviewed_by?: string;
};

export type ClassifySummary = {
  total_count: number;
  classified_count: number;
  skipped_count: number;
  errors: string[];
};

export type LeadError = {
  email: string;
  errors: string[];
};

export type IngestSummary = {
  event_id?: string | null;
  total_guests: number;
  going_count: number;
  enriched_count: number;
  skipped_fresh_count: number;
  already_known_count: number;
  flagged_count: number;
  errors: string[];
  lead_errors?: LeadError[];
  classification?: ClassifySummary;
};

export type RegionBackfillResult = {
  event_id: string;
  candidates: number;
  regions_found: number;
  still_missing: number;
  errors: Array<{ email: string; error: string }>;
};

export type EventAnalyticsAutoAxis = {
  key: string;
  label: string;
  value: number;
  detail: string;
};

export type EventAnalyticsRadarPoint = {
  axis: string;
  score: number;
  kind: "auto" | "manual";
};

export type EventRatingDimension = {
  key: string;
  label: string;
};

export type EventAnalytics = {
  event: { id: string; name: string; event_date?: string | null; status?: string | null };
  auto: EventAnalyticsAutoAxis[];
  manual: {
    scores: Record<string, number | null> | null;
    notes: string | null;
    dimensions: EventRatingDimension[];
  };
  radar: EventAnalyticsRadarPoint[];
  overall_score: number | null;
};

export type JobProgress = {
  stage: string;
  done: number;
  total: number;
  message?: string | null;
  eta_seconds?: number | null;
  elapsed_seconds?: number | null;
  rate_per_minute?: number | null;
};

export type EnrichmentIntegrationTest = {
  status: "ok" | "error";
  apollo: { verified?: boolean; status: string; detail?: string; http_status?: number | null };
  exa: { verified?: boolean; status: string; detail?: string; http_status?: number | null };
  estimated_cost_usd: number;
};

export type JobStatus = {
  id: string;
  job_id?: string;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress?: JobProgress | null;
  result?: IngestSummary | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type UploadLeadsInput = {
  file: File;
  eventName?: string;
  eventDate?: string;
  eventLocation?: string;
  eventUrl?: string;
  classify?: boolean;
  background?: boolean;
  workEmailsOnly?: boolean;
};
