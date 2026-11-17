import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TraceView, traceSteps, type Trace, type TraceEvent } from "./AgentTrace";

const at = (s: number) => new Date(Date.UTC(2026, 8, 26, 7, 0, s)).toISOString();
const ev = (s: number, kind: TraceEvent["kind"], label: string, more: Partial<TraceEvent> = {}): TraceEvent => ({
  at: at(s),
  kind,
  label,
  ...more,
});

const search: TraceEvent[] = [
  ev(0, "stage", "Starting"),
  ev(1, "stage", "Searching the web for new postings"),
  ev(1, "search", "graduate data analyst Dublin"),
  ev(1, "search", "junior BI analyst Ireland"),
  ev(2, "ai_start", "Searching the web for new postings", { provider: "codex", model: "codex-runtime", web: true }),
  ev(40, "ai_call", "Searching the web for new postings", { provider: "codex", model: "codex-runtime", web: true, fresh: true, ok: true, seconds: 38 }),
  ev(40, "source", "Acme — Data Analyst", { url: "https://boards.greenhouse.io/acme/jobs/1", via: "ai" }),
  ev(40, "source", "Beta — BI Analyst", { url: "https://jobs.lever.co/beta/2", via: "ai" }),
  ev(41, "stage", "Checking each posting"),
  ev(41, "check", "Beta — BI Analyst", { outcome: "rejected", stage: "relevance", reason: "Asks for 5+ years" }),
  ev(42, "check", "Acme — Data Analyst", { outcome: "saved", stage: "save", score: 82, job_id: "job-1" }),
];

describe("The activity panel's steps", () => {
  it("files each event under its stage and merges runs of searches, sites and postings", () => {
    const { steps, end } = traceSteps(search, "running");
    expect(steps.map((s) => s.label)).toEqual(["Starting", "Searching the web for new postings", "Checking each posting"]);
    expect(steps.map((s) => s.state)).toEqual(["done", "done", "active"]);
    expect(steps[1].items.map((i) => i.type)).toEqual(["search", "call", "sources"]);
    expect(steps[2].items).toHaveLength(1);
    expect(steps[1].until).toBe(at(41));
    expect(end).toBeUndefined();
  });

  it("shows a call that has not answered yet as thinking, and the answer in its place once it has", () => {
    const waiting = traceSteps(search.slice(0, 5), "running").steps;
    expect(waiting[1].items.map((i) => i.type)).toEqual(["search", "thinking"]);
    const answered = traceSteps(search.slice(0, 6), "running").steps;
    expect(answered[1].items.map((i) => i.type)).toEqual(["search", "call"]);
  });

  it("marks the step a failed run stopped in", () => {
    const { steps, end } = traceSteps([...search, ev(50, "failed", "The app stopped")], "failed");
    expect(steps[steps.length - 1].state).toBe("failed");
    expect(end?.label).toBe("The app stopped");
  });
});

describe("The activity panel", () => {
  const trace: Trace = {
    run: {
      id: "r1", kind: "discovery", job_id: null, company: null, title: null, state: "running",
      stage: "Checking each posting", error: null, provider: "codex", model: "codex-runtime",
      created_at: at(0), updated_at: at(42), seconds: 42, outputs: {}, events: [],
    },
    events: search,
    truncated: 0,
    sources: [
      { label: "Acme — Data Analyst", url: "https://boards.greenhouse.io/acme/jobs/1", via: "ai" },
      { label: "Beta — BI Analyst", url: "https://jobs.lever.co/beta/2", via: "ai" },
    ],
    checks: { rejected: 1, saved: 1 },
    summary: "",
    added_job_ids: [],
    now: at(42),
  };

  it("says what the search looked for, what it found and how each posting was decided", () => {
    const html = renderToStaticMarkup(<TraceView trace={trace} now={Date.parse(at(45))} onJob={() => {}} />);
    expect(html).toContain("Job search");
    expect(html).toContain("Working · 45s");
    expect(html).toContain("3 steps · 2 sites · 2 postings checked · 1 saved · 1 AI call");
    expect(html).toContain("graduate data analyst Dublin");
    expect(html).toContain("Codex answered · searched the web · 38s");
    expect(html).toContain("Found 2 postings on the web");
    expect(html).toContain("Checked 2 postings");
    expect(html).toContain("1 saved");
    expect(html).toContain("1 turned down");
  });

  it("shows a call still in flight with how long it has been thinking", () => {
    const html = renderToStaticMarkup(
      <TraceView trace={{ ...trace, events: search.slice(0, 5) }} now={Date.parse(at(20))} onJob={() => {}} />,
    );
    expect(html).toContain("Codex is searching the web and reading pages");
    expect(html).toContain("18s");
  });
});
