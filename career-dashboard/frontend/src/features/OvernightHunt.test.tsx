import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { HuntProgress, HuntSetup } from "./OvernightHunt";
import type { HuntChoice, HuntInfo, HuntRun } from "../types";

const choice = (over: Partial<HuntChoice> = {}): HuntChoice => ({
  target: 10,
  hours: 8,
  min_fit: 70,
  sources: "all",
  allow_paid: false,
  require_ai_fit: true,
  steps: { research: true, tailor: true, study_plan: true, pdf: true },
  ...over,
});

const info = (over: Partial<HuntInfo> = {}): HuntInfo => ({
  defaults: choice(),
  limits: { max_target: 40, max_hours: 12 },
  plan: {
    roles: ["Data Analyst"],
    related_titles: ["insights analyst", "mi analyst"],
    excluded_titles: [],
    markets: ["ie"],
    board_keywords: ["data analyst"],
    early_career: true,
    strategies: [
      { id: "feeds:directory", kind: "feeds", label: "Employer directory" },
      { id: "ai:ie:1:boards", kind: "ai", label: "Data Analyst — Irish job boards", queries: ['"data analyst" site:irishjobs.ie'] },
    ],
  },
  memory: { outcomes: { rejected: 12, saved: 3 }, rejected_by_stage: { fit: 9 }, held: 2 },
  ai: { ready: false, wakes_at: "2026-09-27T02:40:00Z", wakes_text: "3:40 AM", note: "Kimi Code rests until 3:40 AM" },
  current: null,
  last: null,
  ...over,
});

const run = (over: Partial<HuntRun> = {}): HuntRun => ({
  id: "h1",
  state: "waiting",
  config: choice({ target: 4 }),
  progress: {
    stage: "Waiting for an AI plan: Kimi Code rests until 3:40 AM",
    target: 4,
    cycle: 1,
    saved: [{ id: "j1", company: "Acme", title: "Data Analyst", fit: 84, location: "Dublin", pass: "Employer directory" }],
    passes: [
      { id: "feeds:directory", label: "Employer directory", kind: "feeds", state: "done", looked: 40, saved: 1, held: 3 },
      { id: "ai:ie:1:boards", label: "Data Analyst — Irish job boards", kind: "ai", state: "skipped", note: "No AI plan was free" },
    ],
    waiting: { until: "2026-09-27T02:40:00Z", until_text: "3:40 AM", why: "Kimi Code rests until 3:40 AM" },
    deadline_epoch: Date.now() / 1000 + 3 * 3600,
  },
  error: null,
  stop_requested: false,
  created_at: "2026-09-26T22:00:00Z",
  finished_at: null,
  ...over,
});
const noop = () => {};

describe("The overnight hunt", () => {
  it("shows the goal, the plan and what happens while plans rest", () => {
    const html = renderToStaticMarkup(<HuntSetup info={info()} choice={choice()} onChoice={noop} starting={false} onStart={noop} />);
    expect(html).toContain("10 jobs at fit 70+ within 8 h");
    expect(html).toContain("waits until 3:40 AM");
    expect(html).toContain("Data Analyst + 2 related titles");
    expect(html).toContain("1 feed and 1 AI pass per cycle");
    expect(html).toContain("15 postings already checked");
    expect(html).toContain("2 waiting for an AI check");
    expect(html).toContain("Start the hunt");
    expect(html).not.toContain("disabled=\"\"><svg");
  });
  it("cannot start without target roles", () => {
    const empty = info({ plan: { ...info().plan, roles: [], related_titles: [], strategies: [] } });
    const html = renderToStaticMarkup(<HuntSetup info={empty} choice={choice()} onChoice={noop} starting={false} onStart={noop} />);
    expect(html).toContain("Add your target roles");
  });
  it("shows progress, the wait and each pass while it runs", () => {
    const html = renderToStaticMarkup(<HuntProgress run={run()} onStop={noop} onJob={noop} />);
    expect(html).toContain("Overnight hunt running");
    expect(html).toContain("1 of 4 saved");
    expect(html).toContain("Waiting until 3:40 AM");
    expect(html).toContain("Acme");
    expect(html).toContain("fit 84");
    expect(html).toContain("looked at 40 · saved 1 · 3 held for an AI check");
    expect(html).toContain("No AI plan was free");
    expect(html).toContain("Stop the hunt");
  });
  it("shows the report once it is done", () => {
    const html = renderToStaticMarkup(
      <HuntProgress run={run({ state: "completed", progress: { ...run().progress, waiting: null, report: "daily-job-search/2026-09-27/HUNT-REPORT.md" } })} onStop={noop} onJob={noop} />,
    );
    expect(html).toContain("Last overnight hunt: completed");
    expect(html).toContain("HUNT-REPORT.md");
    expect(html).not.toContain("Stop the hunt");
  });
});
