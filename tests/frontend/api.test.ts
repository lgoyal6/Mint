import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import {
  appendChatMessages,
  chatWithAgent,
  chatWithAgentStream,
  deleteChatThread,
  getChatThread,
  listChatThreads,
  saveChatThread,
  getDataQualitySummary,
  getEventAnalytics,
  getEventSummary,
  getPipelineSummary,
  getAuthStatus,
  getReviewCounts,
  getLeadDetail,
  listEvents,
  listFlaggedReview,
  listLeads,
  listNeedsReview,
  reclassifyLead,
  resolveFlaggedLead,
  runAgentBoxSource,
  login,
  logout,
  saveEventRatings,
  uploadLeadsFile,
  updateEventEnrichment,
  waitForJob,
} from "../../lib/api";
import type { AgentStreamEvent } from "../../lib/types";

let baseUrl = "";
let progressJobPolls = 0;
const requests: Array<{ method?: string; url?: string; body: unknown; headers: Record<string, string | string[] | undefined> }> = [];

const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.from(chunk));
  }
  const rawBody = Buffer.concat(chunks).toString("utf8");
  const contentType = req.headers["content-type"]?.toString() ?? "";
  const body = rawBody && contentType.includes("application/json") ? JSON.parse(rawBody) : rawBody;
  requests.push({ method: req.method, url: req.url, body, headers: req.headers });

  if (req.method === "POST" && req.url === "/agent/chat") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        reply: "Found [Ada Lovelace](lead:11111111-1111-1111-1111-111111111111).",
        tool_trace: [
          {
            name: "list_leads",
            input: { tier: "P0" },
            short_result: "total=1, items=1 items",
          },
        ],
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/auth/me") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ authenticated: true }));
    return;
  }

  if (req.method === "POST" && req.url === "/auth/login") {
    res.setHeader("content-type", "application/json");
    res.setHeader("set-cookie", "mint_session=test; HttpOnly");
    res.end(JSON.stringify({ authenticated: true }));
    return;
  }

  if (req.method === "POST" && req.url === "/auth/logout") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ authenticated: false }));
    return;
  }

  if (req.method === "POST" && req.url === "/source/agentbox") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ job_id: "12121212-1212-1212-1212-121212121212", status: "queued" }));
    return;
  }

  if (req.method === "GET" && req.url === "/jobs/12121212-1212-1212-1212-121212121212") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({
      id: "12121212-1212-1212-1212-121212121212",
      name: "source_agentbox",
      status: "succeeded",
      result: {
        leads: 20,
        bands: { pursue: 4, nurture: 16 },
        needs_review: 16,
        candidates: [],
        rejected: [],
        mode: "live",
        campaign: "product_runtime_us",
      },
      error: null,
    }));
    return;
  }

  if (req.method === "GET" && req.url === "/leads/11111111-1111-1111-1111-111111111111") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        person: {
          id: "11111111-1111-1111-1111-111111111111",
          email: "ada@example.com",
          name: "Ada Lovelace",
          title: "Founder",
          affiliation_state: "verified",
          resolution_confidence: "high",
          events_registered: 1,
          events_attended: 1,
        },
        company: {
          id: "22222222-2222-2222-2222-222222222222",
          name: "Analytical Engines",
          domain: "engines.ai",
          exa_target_vertical: "agentic",
          exa_data_quality: "rich",
          exa_enrichment: { inference_signals: "Production inference workload." },
        },
        current_classification: {
          id: "33333333-3333-3333-3333-333333333333",
          tier: "P0",
          product_fit: ["maas"],
          primary_product: "maas",
          confidence: 4,
          reasoning: "Founder at a target-vertical inference company.",
          key_signal: "Production inference",
          key_risk: "Early team",
          flags: [],
          review_status: "CLASSIFIED",
        },
        classification_history: [],
        registrations: [],
      }),
    );
    return;
  }

  if (req.method === "POST" && req.url === "/events/upload") {
    res.setHeader("content-type", "application/json");
    if (rawBody.includes("Async Dinner")) {
      res.statusCode = 202;
      res.end(
        JSON.stringify({
          id: "77777777-7777-7777-7777-777777777777",
          job_id: "77777777-7777-7777-7777-777777777777",
          name: "csv_upload",
          status: "queued",
          result: null,
          error: null,
        }),
      );
      return;
    }
    res.end(
      JSON.stringify({
        event_id: "44444444-4444-4444-4444-444444444444",
        total_guests: 2,
        going_count: 2,
        enriched_count: 2,
        skipped_fresh_count: 0,
        already_known_count: 0,
        flagged_count: 1,
        errors: [],
        classification: {
          total_count: 2,
          classified_count: 2,
          skipped_count: 0,
          errors: [],
        },
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/jobs/77777777-7777-7777-7777-777777777777") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        id: "77777777-7777-7777-7777-777777777777",
        name: "csv_upload",
        status: "succeeded",
        result: {
          event_id: "88888888-8888-8888-8888-888888888888",
          total_guests: 3,
          going_count: 3,
          enriched_count: 3,
          skipped_fresh_count: 0,
          already_known_count: 0,
          flagged_count: 0,
          errors: [],
        },
        error: null,
      }),
    );
    return;
  }

  if (req.url?.startsWith("/chat/threads")) {
    res.setHeader("content-type", "application/json");
    if (req.method === "GET" && req.url === "/chat/threads") {
      res.end(
        JSON.stringify([
          { id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title: "Show me the P0s", updated_at: "2026-06-09T10:00:00Z" },
        ]),
      );
      return;
    }
    if (req.method === "GET") {
      res.end(
        JSON.stringify({
          id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          title: "Show me the P0s",
          messages: [
            { id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", role: "user", content: "Show me the P0s", tool_trace: null },
          ],
        }),
      );
      return;
    }
    if (req.method === "PUT") {
      res.end(JSON.stringify({ id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", title: "Show me the P0s" }));
      return;
    }
    if (req.method === "POST") {
      res.end(JSON.stringify({ appended: 1 }));
      return;
    }
    if (req.method === "DELETE") {
      res.end(JSON.stringify({ deleted: true }));
      return;
    }
  }

  if (req.method === "GET" && req.url === "/jobs/99999999-9999-9999-9999-999999999999") {
    progressJobPolls += 1;
    res.setHeader("content-type", "application/json");
    if (progressJobPolls === 1) {
      res.end(
        JSON.stringify({
          id: "99999999-9999-9999-9999-999999999999",
          name: "csv_upload",
          status: "running",
          progress: { stage: "enriching", done: 45, total: 200, message: "Enriching 45 of 200 leads" },
          result: null,
          error: null,
        }),
      );
      return;
    }
    res.end(
      JSON.stringify({
        id: "99999999-9999-9999-9999-999999999999",
        name: "csv_upload",
        status: "succeeded",
        progress: { stage: "classifying", done: 200, total: 200, message: "Classifying 200 of 200 leads" },
        result: {
          event_id: "88888888-8888-8888-8888-888888888888",
          total_guests: 200,
          going_count: 200,
          enriched_count: 200,
          skipped_fresh_count: 0,
          already_known_count: 0,
          flagged_count: 0,
          errors: [],
        },
        error: null,
      }),
    );
    return;
  }

  if (
    req.method === "GET" &&
    (
      req.url === "/events?status=all" ||
      req.url === "/events?status=all&source=luma" ||
      req.url === "/events?status=all&refresh_luma=false" ||
      req.url === "/events?status=all&source=luma&refresh_luma=false" ||
      req.url === "/events?status=all&refresh_luma=true" ||
      req.url === "/events?status=all&source=luma&refresh_luma=true"
    )
  ) {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify([
        {
          id: "44444444-4444-4444-4444-444444444444",
          luma_event_id: "evt-hard",
          name: "Hard Problems Night",
          status: "past",
          event_date: "2026-05-27T02:00:00Z",
          source: "luma",
        },
      ]),
    );
    return;
  }

  if (req.method === "PATCH" && req.url === "/events/44444444-4444-4444-4444-444444444444/enrichment") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        id: "44444444-4444-4444-4444-444444444444",
        luma_event_id: "evt-hard",
        name: "Hard Problems Night",
        status: "past",
        event_date: "2026-05-27T02:00:00Z",
        source: "luma",
        enrichment_enabled: body.enrichment_enabled,
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/events/44444444-4444-4444-4444-444444444444/summary") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        event: {
          id: "44444444-4444-4444-4444-444444444444",
          luma_event_id: "evt-hard",
          name: "Hard Problems Night",
          status: "past",
        },
        counts: { total_guests: 10, going_count: 8, resolved: 6, parked_low: 1, flagged: 1, classified: 5 },
        tier_breakdown: { P0: 2, P1: 3, NP: 0 },
        product_fit_breakdown: { maas: 3, dedicated: 1, gpu: 1 },
        np_trigger_breakdown: { no_product_fit: 2 },
        p0_p1_leads: [
          {
            person_id: "11111111-1111-1111-1111-111111111111",
            email: "ada@example.com",
            name: "Ada Lovelace",
            title: "Founder",
            resolved_company: "Analytical Engines",
            resolved_domain: "engines.ai",
            tier: "P0",
            primary_product: "maas",
            product_fit: ["maas"],
            confidence: 4,
            target_vertical: "agentic",
            review_status: "CLASSIFIED",
            key_signal: "Production inference",
          },
        ],
        resolution_funnel: { high_medium: 6, low: 1, flagged: 1 },
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/events/44444444-4444-4444-4444-444444444444/analytics") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        event: {
          id: "44444444-4444-4444-4444-444444444444",
          name: "Hard Problems Night",
          status: "past",
        },
        auto: [
          { key: "turnout", label: "Turnout", value: 75, detail: "6/8 checked in" },
          { key: "audience_fit", label: "Audience Fit", value: 50, detail: "4/8 P0/P1 of attendees" },
        ],
        manual: {
          scores: { engagement: 4, content: null, follow_up: null },
          notes: null,
          dimensions: [
            { key: "engagement", label: "Engagement" },
            { key: "content", label: "Content" },
            { key: "follow_up", label: "Follow-up" },
          ],
        },
        radar: [
          { axis: "Turnout", score: 75, kind: "auto" },
          { axis: "Audience Fit", score: 50, kind: "auto" },
          { axis: "Engagement", score: 80, kind: "manual" },
        ],
        overall_score: 75,
      }),
    );
    return;
  }

  if (req.method === "PUT" && req.url === "/events/44444444-4444-4444-4444-444444444444/ratings") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        event: {
          id: "44444444-4444-4444-4444-444444444444",
          name: "Hard Problems Night",
          status: "past",
        },
        auto: [
          { key: "turnout", label: "Turnout", value: 75, detail: "6/8 checked in" },
          { key: "audience_fit", label: "Audience Fit", value: 50, detail: "4/8 P0/P1 of attendees" },
        ],
        manual: {
          scores: body.scores,
          notes: body.notes,
          dimensions: [
            { key: "engagement", label: "Engagement" },
            { key: "content", label: "Content" },
            { key: "follow_up", label: "Follow-up" },
          ],
        },
        radar: [
          { axis: "Turnout", score: 75, kind: "auto" },
          { axis: "Audience Fit", score: 50, kind: "auto" },
          { axis: "Engagement", score: 80, kind: "manual" },
          { axis: "Content", score: 100, kind: "manual" },
        ],
        overall_score: 76,
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/pipeline/summary") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        p0_p1_by_primary_product: { maas: 3, dedicated: 1, gpu: 1 },
        repeat_attendees: [
          {
            person_id: "11111111-1111-1111-1111-111111111111",
            email: "ada@example.com",
            events_registered: 2,
            events_attended: 1,
          },
        ],
        companies_with_multiple_people: [
          {
            company_id: "22222222-2222-2222-2222-222222222222",
            name: "Analytical Engines",
            domain: "engines.ai",
            person_count: 3,
          },
        ],
        nurture_reengage: [],
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/health/data-quality") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        resolution: { total_people: 10, high_medium: 6, low: 3, flagged: 1, resolution_rate: 0.6 },
        apollo_match_rate: 0.4,
        data_quality_distribution: { rich: 2, moderate: 3, sparse: 4, unknown: 1 },
        np_trigger_breakdown: { no_product_fit: 2 },
        promising_low_confidence: 3,
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/review/flagged?limit=10&offset=0") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        total: 1,
        items: [
          {
            person_id: "55555555-5555-5555-5555-555555555555",
            email: "casey@example.com",
            name: "Casey Conflict",
            title: "Founder",
            self_reported_company: "AgentCo",
            candidate_companies: [{ domain: "agentco.ai", name: "AgentCo", source: "exa_company_search" }],
            resolution_evidence: { chain: ["conflict found"] },
          },
        ],
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/review/needs-review?type=classification&limit=5&offset=0") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        total: 1,
        items: [
          {
            person_id: "66666666-6666-6666-6666-666666666666",
            email: "riley@example.com",
            name: "Riley Review",
            title: "CEO",
            resolved_company: "ReviewCo",
            resolved_domain: "reviewco.ai",
            tier: "P1",
            confidence: 2,
            primary_product: "maas",
            review_status: "NEEDS_REVIEW",
            resolution_confidence: "medium",
            reason: "Low classification confidence",
            source: "classification",
          },
        ],
      }),
    );
    return;
  }

  if (req.method === "GET" && req.url === "/review/flagged?limit=1&offset=0") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ total: 1, items: [] }));
    return;
  }

  if (req.method === "GET" && req.url === "/review/needs-review?type=all&limit=1&offset=0") {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ total: 2, items: [] }));
    return;
  }

  if (req.method === "POST" && req.url === "/review/flagged/55555555-5555-5555-5555-555555555555/resolve") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        resolution: { person_id: "55555555-5555-5555-5555-555555555555", domain: "agentco.ai" },
        classification: { classified_count: 1, skipped_count: 0, total_count: 1, errors: [] },
        updated_lead: {
          person: {
            id: "55555555-5555-5555-5555-555555555555",
            email: "casey@example.com",
            name: "Casey Conflict",
          },
        },
      }),
    );
    return;
  }

  if (req.method === "POST" && req.url === "/review/66666666-6666-6666-6666-666666666666/reclassify") {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        person: { id: "66666666-6666-6666-6666-666666666666", email: "riley@example.com", name: "Riley Review" },
        current_classification: { tier: "P0", confidence: 5, reasoning: "Manual review." },
        classification_history: [],
        registrations: [],
      }),
    );
    return;
  }

  if (
    req.method === "GET" &&
    req.url === "/leads?tier=P0&primary_product=maas&event_id=44444444-4444-4444-4444-444444444444&limit=25&offset=0"
  ) {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        total: 1,
        items: [
          {
            person_id: "11111111-1111-1111-1111-111111111111",
            email: "ada@example.com",
            name: "Ada Lovelace",
            tier: "P0",
            primary_product: "maas",
          },
        ],
      }),
    );
    return;
  }

  if (req.method === "POST" && req.url === "/agent/chat/stream") {
    res.setHeader("content-type", "text/event-stream");
    const wantsError =
      Array.isArray((body as { messages?: Array<{ content?: string }> })?.messages) &&
      (body as { messages: Array<{ content?: string }> }).messages[0]?.content === "explode";
    if (wantsError) {
      res.write('data: {"type":"thinking"}\n\n');
      res.write('data: {"type":"error","message":"agent blew up"}\n\n');
      res.end();
      return;
    }
    const frames = [
      'data: {"type":"thinking"}\n\n',
      'data: {"type":"heartbeat"}\n\n',
      'data: {"type":"tool","status":"started","name":"list_leads","input":{"tier":"P0"}}\n\nda',
      'ta: {"type":"tool","status":"finished","name":"list_leads","short_result":"total=1"}\n\n',
      'data: {"type":"result","reply":"Found one lead.","tool_trace":[{"name":"list_leads","input":{"tier":"P0"}}]}\n\n',
    ];
    for (const frame of frames) {
      res.write(frame);
    }
    res.end();
    return;
  }

  res.statusCode = 404;
  res.end(JSON.stringify({ error: "not found" }));
});

before(async () => {
  await new Promise<void>((resolve) => server.listen(0, resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("test server did not bind to a TCP port");
  }
  baseUrl = `http://127.0.0.1:${address.port}`;
  process.env.NEXT_PUBLIC_API_BASE = baseUrl;
});

after(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
});

test("chatWithAgent posts the full message history to the backend", async () => {
  const result = await chatWithAgent([
    { role: "user", content: "Who are the P0s?" },
    { role: "assistant", content: "I can look that up." },
    { role: "user", content: "Do it." },
  ]);

  assert.equal(result.reply.includes("Ada Lovelace"), true);
  assert.equal(result.tool_trace[0].name, "list_leads");

  const request = requests.find((item) => item.url === "/agent/chat");
  assert.equal(request?.method, "POST");
  assert.deepEqual(request?.body, {
    messages: [
      { role: "user", content: "Who are the P0s?" },
      { role: "assistant", content: "I can look that up." },
      { role: "user", content: "Do it." },
    ],
  });
});

test("chatWithAgentStream reports each event and returns the final reply", async () => {
  const events: AgentStreamEvent[] = [];
  const result = await chatWithAgentStream(
    [{ role: "user", content: "Show P0 leads" }],
    (event) => events.push(event),
  );

  assert.equal(result.reply, "Found one lead.");
  assert.equal(result.tool_trace[0].name, "list_leads");
  assert.deepEqual(
    events.map((event) => event.type),
    ["thinking", "tool", "tool", "result"],
  );

  const request = requests.find((item) => item.url === "/agent/chat/stream");
  assert.equal(request?.method, "POST");
});

test("chatWithAgentStream throws when the stream reports an error", async () => {
  await assert.rejects(
    chatWithAgentStream([{ role: "user", content: "explode" }]),
    /agent blew up/,
  );
});

test("auth helpers call the backend session endpoints", async () => {
  const status = await getAuthStatus();
  const loggedIn = await login("test-password");
  const loggedOut = await logout();

  assert.deepEqual(status, { authenticated: true });
  assert.deepEqual(loggedIn, { authenticated: true });
  assert.deepEqual(loggedOut, { authenticated: false });

  const loginRequest = requests.find((item) => item.url === "/auth/login");
  assert.deepEqual(loginRequest?.body, { password: "test-password" });
});

test("getLeadDetail fetches the backend lead detail DTO", async () => {
  const detail = await getLeadDetail("11111111-1111-1111-1111-111111111111");

  assert.equal(detail.person.name, "Ada Lovelace");
  assert.equal(detail.company?.domain, "engines.ai");
  assert.equal(detail.current_classification?.tier, "P0");
});

test("uploadLeadsFile posts multipart form data to the upload endpoint", async () => {
  const file = new File(["Email\nalex@example.com\n"], "leads.csv", { type: "text/csv" });
  const result = await uploadLeadsFile({
    file,
    eventName: "Partner Dinner",
    classify: true,
  });

  assert.equal(result.event_id, "44444444-4444-4444-4444-444444444444");
  assert.equal(result.classification?.classified_count, 2);

  const request = requests.find((item) => item.url === "/events/upload");
  assert.equal(request?.method, "POST");
  assert.match(String(request?.headers["content-type"]), /multipart\/form-data/);
  assert.match(String(request?.body), /name="event_name"/);
  assert.match(String(request?.body), /Partner Dinner/);
  assert.match(String(request?.body), /name="work_emails_only"/);
  assert.match(String(request?.body), /name="classify"/);
  assert.match(String(request?.body), /leads.csv/);
});

test("uploadLeadsFile polls background upload jobs", async () => {
  const file = new File(["Email\nasync@example.com\n"], "async.csv", { type: "text/csv" });
  const result = await uploadLeadsFile({
    file,
    eventName: "Async Dinner",
    classify: true,
  });

  assert.equal(result.event_id, "88888888-8888-8888-8888-888888888888");
  assert.equal(result.enriched_count, 3);
  assert.ok(requests.some((item) => item.url === "/jobs/77777777-7777-7777-7777-777777777777"));
});

test("chat thread API functions hit the chat endpoints", async () => {
  const threads = await listChatThreads();
  assert.equal(threads[0].title, "Show me the P0s");

  const thread = await getChatThread("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
  assert.equal(thread.messages.length, 1);

  await saveChatThread("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "Show me the P0s");
  await appendChatMessages("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", [
    { id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", role: "user", content: "Show me the P0s", tool_trace: null },
  ]);
  await deleteChatThread("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");

  const putRequest = requests.find(
    (item) => item.method === "PUT" && item.url === "/chat/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  );
  assert.deepEqual(putRequest?.body, { title: "Show me the P0s" });

  const postRequest = requests.find(
    (item) => item.method === "POST" && item.url === "/chat/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/messages",
  );
  assert.equal((postRequest?.body as { messages: unknown[] }).messages.length, 1);

  assert.ok(
    requests.some(
      (item) => item.method === "DELETE" && item.url === "/chat/threads/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    ),
  );
});

test("waitForJob reports progress to the callback while the job runs", async () => {
  const updates: Array<{ done: number; total: number } | null> = [];
  const result = await waitForJob(
    "99999999-9999-9999-9999-999999999999",
    (progress) => updates.push(progress),
    10,
  );

  assert.equal(result.enriched_count, 200);
  assert.equal(updates.length, 1);
  assert.equal(updates[0]?.done, 45);
  assert.equal(updates[0]?.total, 200);
});

test("AgentBox source can request a 20-lead pilot", async () => {
  const result = await runAgentBoxSource("product_runtime_us", undefined, 20);

  assert.equal(result.leads, 20);
  assert.equal(result.bands.pursue, 4);
  const sourceRequest = requests.find(
    (item) => item.method === "POST" && item.url === "/source/agentbox",
  );
  assert.deepEqual(sourceRequest?.body, { campaign: "product_runtime_us", limit: 20 });
});

test("dashboard fetchers call the read API endpoints", async () => {
  const [events, lumaEvents, localEvents, toggledEvent, eventSummary, pipeline, dataQuality] = await Promise.all([
    listEvents(),
    listEvents({ source: "luma" }),
    listEvents({ refreshLuma: false }),
    updateEventEnrichment("44444444-4444-4444-4444-444444444444", false),
    getEventSummary("44444444-4444-4444-4444-444444444444"),
    getPipelineSummary(),
    getDataQualitySummary(),
  ]);

  assert.equal(events[0].name, "Hard Problems Night");
  assert.equal(lumaEvents[0].source, "luma");
  assert.equal(localEvents[0].name, "Hard Problems Night");
  assert.equal(toggledEvent.enrichment_enabled, false);
  assert.ok(requests.some((item) => item.url === "/events?status=all&source=luma"));
  assert.ok(requests.some((item) => item.url === "/events?status=all&refresh_luma=false"));
  assert.deepEqual(
    requests.find((item) => item.url === "/events/44444444-4444-4444-4444-444444444444/enrichment")?.body,
    { enrichment_enabled: false },
  );
  assert.equal(eventSummary.counts.resolved, 6);
  assert.equal(eventSummary.p0_p1_leads[0].person_id, "11111111-1111-1111-1111-111111111111");
  assert.equal(pipeline.p0_p1_by_primary_product.maas, 3);
  assert.equal(dataQuality.resolution.resolution_rate, 0.6);
});

test("event analytics helpers call the scorecard endpoints", async () => {
  const analytics = await getEventAnalytics("44444444-4444-4444-4444-444444444444");
  const updated = await saveEventRatings("44444444-4444-4444-4444-444444444444", {
    scores: { engagement: 4, content: 5 },
    notes: "Strong follow-up list.",
  });

  assert.equal(analytics.overall_score, 75);
  assert.equal(analytics.manual.scores?.engagement, 4);
  assert.equal(updated.manual.notes, "Strong follow-up list.");
  assert.deepEqual(
    requests.find((item) => item.url === "/events/44444444-4444-4444-4444-444444444444/ratings")?.body,
    { scores: { engagement: 4, content: 5 }, notes: "Strong follow-up list." },
  );
});

test("review fetchers and actions call the review API endpoints", async () => {
  const flagged = await listFlaggedReview({ limit: 10, offset: 0 });
  const needsReview = await listNeedsReview({ type: "classification", limit: 5, offset: 0 });
  const counts = await getReviewCounts();
  const resolved = await resolveFlaggedLead("55555555-5555-5555-5555-555555555555", {
    chosen_domain: "agentco.ai",
    remember_aliases: true,
  });
  const reclassified = await reclassifyLead("66666666-6666-6666-6666-666666666666", {
    new_tier: "P0",
    reason: "Buyer at target account.",
    reviewed_by: "pynay",
  });

  assert.equal(flagged.total, 1);
  assert.equal(needsReview.items[0].source, "classification");
  assert.deepEqual(counts, { flagged: 1, needsReview: 2 });
  assert.equal(resolved.resolution.domain, "agentco.ai");
  assert.equal(reclassified.current_classification?.tier, "P0");

  const resolveRequest = requests.find((item) =>
    item.url === "/review/flagged/55555555-5555-5555-5555-555555555555/resolve"
  );
  assert.deepEqual(resolveRequest?.body, { chosen_domain: "agentco.ai", remember_aliases: true });

  const reclassifyRequest = requests.find((item) =>
    item.url === "/review/66666666-6666-6666-6666-666666666666/reclassify"
  );
  assert.deepEqual(reclassifyRequest?.body, {
    new_tier: "P0",
    reason: "Buyer at target account.",
    reviewed_by: "pynay",
  });
});

test("listLeads builds structured filter query params", async () => {
  const page = await listLeads({
    tier: "P0",
    primary_product: "maas",
    event_id: "44444444-4444-4444-4444-444444444444",
    limit: 25,
    offset: 0,
  });

  assert.equal(page.total, 1);
  assert.equal(page.items[0].email, "ada@example.com");
});
