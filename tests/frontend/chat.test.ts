import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  agentActivityLabel,
  assistantDraftTitle,
  appendAssistantTurn,
  appendUserMessage,
  appendUploadSummaryMessage,
  createThread,
  extractExportArtifacts,
  extractLeadReferences,
  reduceLiveToolEvents,
  summarizeToolTrace,
} from "../../lib/chat";

test("chat helpers maintain stateless backend history in order", () => {
  const thread = createThread("What happened at the event?");
  const withUser = appendUserMessage(thread, "Show me the P0s.");
  const withAssistant = appendAssistantTurn(withUser, {
    reply: "Two P0s.",
    tool_trace: [{ name: "list_leads", input: { tier: "P0" }, short_result: "total=2" }],
  });

  assert.equal(withAssistant.messages.length, 2);
  assert.deepEqual(
    withAssistant.messages.map((message) => message.role),
    ["user", "assistant"],
  );
  assert.deepEqual(withAssistant.messages.map(({ role, content }) => ({ role, content })), [
    { role: "user", content: "Show me the P0s." },
    { role: "assistant", content: "Two P0s." },
  ]);
});

test("extractLeadReferences finds supported lead link formats", () => {
  const refs = extractLeadReferences(
    "Review [Ada](lead:11111111-1111-1111-1111-111111111111) and /leads/22222222-2222-2222-2222-222222222222.",
  );

  assert.deepEqual(refs, [
    { personId: "11111111-1111-1111-1111-111111111111", label: "Ada" },
    { personId: "22222222-2222-2222-2222-222222222222", label: "22222222" },
  ]);
});

test("extractExportArtifacts returns unique export downloads from tool traces", () => {
  const artifacts = extractExportArtifacts({
    toolTrace: [
      {
        name: "export_leads",
        input: { tiers: ["P0", "P1"] },
        artifact: {
          filename: "mint-leads-test.xlsx",
          download_url: "/exports/mint-leads-test.xlsx",
          row_count: 48,
          format: "xlsx",
        },
      },
      {
        name: "export_leads",
        input: { tiers: ["P0", "P1"] },
        artifact: {
          filename: "mint-leads-test.xlsx",
          download_url: "/exports/mint-leads-test.xlsx",
          row_count: 48,
          format: "xlsx",
        },
      },
    ],
  });

  assert.deepEqual(artifacts, [
    {
      filename: "mint-leads-test.xlsx",
      download_url: "/exports/mint-leads-test.xlsx",
      row_count: 48,
      format: "xlsx",
    },
  ]);
});

test("summarizeToolTrace accepts short_result and result_summary", () => {
  assert.equal(
    summarizeToolTrace({ name: "list_leads", input: {}, short_result: "total=12" }),
    "total=12",
  );
  assert.equal(
    summarizeToolTrace({ name: "pull_event", input: {}, result_summary: "enriched 80 guests" }),
    "enriched 80 guests",
  );
});

test("appendUploadSummaryMessage summarizes CSV ingestion in the thread", () => {
  const thread = createThread();
  const updated = appendUploadSummaryMessage(thread, "partner-list.csv", {
    event_id: "44444444-4444-4444-4444-444444444444",
    total_guests: 120,
    going_count: 118,
    enriched_count: 78,
    skipped_fresh_count: 30,
    already_known_count: 12,
    flagged_count: 5,
    errors: [],
    classification: {
      total_count: 78,
      classified_count: 70,
      skipped_count: 8,
      errors: [],
    },
  });

  assert.equal(updated.messages.length, 1);
  assert.equal(updated.messages[0].role, "assistant");
  assert.equal(
    updated.messages[0].content,
    "Uploaded `partner-list.csv`: ingested 120 rows, approved 118, enriched 78, skipped 30 fresh, flagged 5. Classified 70 leads. Event `44444444-4444-4444-4444-444444444444` is ready to query.",
  );
});

test("appendUploadSummaryMessage lists per-lead enrichment problems in plain language", () => {
  const thread = createThread();
  const updated = appendUploadSummaryMessage(thread, "leads.csv", {
    event_id: "44444444-4444-4444-4444-444444444444",
    total_guests: 10,
    going_count: 10,
    enriched_count: 8,
    skipped_fresh_count: 0,
    already_known_count: 0,
    flagged_count: 0,
    errors: ["apollo_person: timeout", "exa_deep: rate limited"],
    lead_errors: [
      { email: "alex@example.com", errors: ["apollo_person: timeout"] },
      { email: "blair@example.com", errors: ["exa_deep: rate limited", "apollo_org: 502"] },
    ],
    classification: {
      total_count: 8,
      classified_count: 8,
      skipped_count: 0,
      errors: [],
    },
  });

  const content = updated.messages[0].content;
  assert.match(content, /2 leads had enrichment problems/);
  assert.match(content, /alex@example.com: couldn't verify the person/);
  assert.match(content, /blair@example.com: couldn't research the company/);
});

test("agentActivityLabel turns stream events into friendly status lines", () => {
  assert.equal(agentActivityLabel({ type: "thinking" }), "Thinking through the workspace");
  assert.equal(
    agentActivityLabel({ type: "tool", status: "started", name: "list_leads", input: {} }),
    "Looking up leads",
  );
  assert.equal(
    agentActivityLabel({ type: "tool", status: "started", name: "pull_event", input: {} }),
    "Pulling the event from Luma",
  );
  assert.equal(
    agentActivityLabel({ type: "tool", status: "started", name: "future_tool", input: {} }),
    "Running future tool",
  );
  assert.equal(
    agentActivityLabel({ type: "tool", status: "finished", name: "list_leads" }),
    "Looking up leads",
  );
});

test("reduceLiveToolEvents keeps live tool progress in stream order", () => {
  let items = reduceLiveToolEvents([], { type: "thinking" });
  items = reduceLiveToolEvents(items, {
    type: "tool",
    status: "started",
    name: "list_leads",
    input: { tier: "P0" },
  });
  items = reduceLiveToolEvents(items, {
    type: "tool",
    status: "finished",
    name: "list_leads",
    short_result: "total=1",
  });
  items = reduceLiveToolEvents(items, {
    type: "tool",
    status: "started",
    name: "future_tool",
    input: {},
  });

  assert.deepEqual(items, [
    { name: "list_leads", input: { tier: "P0" }, status: "finished", short_result: "total=1" },
    { name: "future_tool", input: {}, status: "started" },
  ]);
});

test("assistantDraftTitle reflects live tool progress", () => {
  assert.equal(assistantDraftTitle([]), "Working");
  assert.equal(
    assistantDraftTitle([{ name: "list_leads", input: {}, status: "started" }]),
    "Running 1 tool",
  );
  assert.equal(
    assistantDraftTitle([
      { name: "list_leads", input: {}, status: "finished" },
      { name: "event_summary", input: {}, status: "started" },
    ]),
    "Running 2 tools",
  );
});

test("assistant draft does not render fake progress affordances", () => {
  const component = readFileSync("components/ChatThread.tsx", "utf8");
  const styles = readFileSync("app/globals.css", "utf8");

  assert.equal(component.includes("animate-draft-scan"), false);
  assert.equal(styles.includes("draft-scan"), false);
  assert.equal(component.includes("animate-typing-dot"), false);
  assert.equal(styles.includes("typing-dot"), false);
  assert.equal(component.includes("agent-loader"), true);
  assert.equal(styles.includes("agent-loader-spin"), true);
});

test("flagged review UI recommends a company and can remember domain aliases", () => {
  const component = readFileSync("components/review/ReviewScreen.tsx", "utf8");

  assert.equal(component.includes("Recommended"), true);
  assert.equal(component.includes("recommendCandidate"), true);
  assert.equal(component.includes("remember_aliases: rememberAliases"), true);
  assert.equal(component.includes("Remember this choice for future leads"), true);
  assert.equal(component.includes("Open site"), true);
});

test("flagged review resolution updates the queue in place without a full reload", () => {
  const component = readFileSync("components/review/ReviewScreen.tsx", "utf8");

  assert.equal(component.includes("current.filter((item) => item.person_id !== personId)"), true);
  assert.equal(component.includes("window.location.reload()"), true, "bulk clear still intentionally reloads");
  const resolutionHandler = component.slice(component.indexOf("async function handleResolved"), component.indexOf("function handleBackgroundStatus"));
  assert.equal(resolutionHandler.includes("await load()"), false);
});

test("event review navigation carries the selected event into the queue", () => {
  const consoleComponent = readFileSync("components/AgentConsole.tsx", "utf8");
  const eventDashboard = readFileSync("components/dashboards/EventDashboard.tsx", "utf8");

  assert.equal(consoleComponent.includes("initialEventId={reviewEventId}"), true);
  assert.equal(eventDashboard.includes('onOpenReview("flagged", summary.event.id)'), true);
  assert.equal(eventDashboard.includes('onOpenReview("needs-review", summary.event.id)'), true);
});
