import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import Agents, { pipelineStages } from "./Agents";
import Profile from "./Profile";
import Assurance, { confidenceTone } from "./Assurance";
import type { Job, Summary } from "../types";

const job = (over: Partial<Job>): Job => ({
  id: over.id || "j1",
  company: "Example Co",
  title: "Data Engineer",
  location: "Austin, TX",
  url: "https://example.test/j1",
  description: "A complete test job description for data engineering.",
  status: "saved",
  notes: "",
  application_date: null,
  folder: null,
  created_at: "2026-09-17T10:00:00Z",
  selected_project_id: null,
  record_source: "posting",
  deleted_at: null,
  deletion_reason: "",
  ...over,
});
const noReviews = { pending: 0, kept: 0, removed: 0, tailored_jobs: 0 };

describe("The five-stage pipeline summary", () => {
  it("is idle everywhere before anything happens", () => {
    const stages = pipelineStages([], noReviews, 0);
    expect(stages.map((s) => s.label)).toEqual(["Discover", "Rank", "Tailor", "Guardrail", "Apply"]);
    expect(stages.every((s) => s.state === "idle")).toBe(true);
    expect(stages[1].note).toBe("No jobs yet");
  });
  it("reads rank, tailor, guardrail and apply straight from the workspace", () => {
    const jobs = [
      job({ id: "a", fit_score: 88 }),
      job({ id: "b", fit_score: 71 }),
      job({ id: "c", fit_score: null }),
      job({ id: "d", status: "applied", application_date: "2026-09-12", fit_score: 64 }),
    ];
    const discovery = {
      id: "r1", kind: "discovery", job_id: null, company: null, title: null,
      state: "completed", stage: null, error: null, provider: null, model: null,
      created_at: new Date().toISOString(), updated_at: "", seconds: 4, outputs: {}, events: [],
    };
    const stages = pipelineStages(jobs, { pending: 2, kept: 1, removed: 0, tailored_jobs: 3 }, 2, discovery);
    const byLabel = Object.fromEntries(stages.map((s) => [s.label, s]));
    expect(byLabel.Discover.state).toBe("done");
    expect(byLabel.Rank).toMatchObject({ state: "warn", note: "3/4 scored" });
    expect(byLabel.Tailor).toMatchObject({ state: "warn", note: "3/4 tailored · 2 PDF ready" });
    expect(byLabel.Guardrail).toMatchObject({ state: "warn", note: "2 items to review" });
    expect(byLabel.Apply.state).toBe("done");
  });
  it("lights the search while it runs and closes the guardrail when reviews are done", () => {
    const jobs = [job({ id: "a", fit_score: 90 })];
    const running = {
      id: "r2", kind: "discovery", job_id: null, company: null, title: null,
      state: "running", stage: "Searching", error: null, provider: null, model: null,
      created_at: new Date().toISOString(), updated_at: "", seconds: null, outputs: {}, events: [],
    };
    const stages = pipelineStages(jobs, { pending: 0, kept: 4, removed: 1, tailored_jobs: 1 }, 1, running);
    const byLabel = Object.fromEntries(stages.map((s) => [s.label, s]));
    expect(byLabel.Discover.state).toBe("active");
    expect(byLabel.Rank.state).toBe("done");
    expect(byLabel.Tailor.state).toBe("done");
    expect(byLabel.Guardrail).toMatchObject({ state: "done", note: "All items reviewed" });
  });
});

describe("Simplified pages render their empty and loading states", () => {
  it("Assurance explains itself before there is anything to review", () => {
    const data = { jobs: [], documents: [] } as unknown as Summary;
    const html = renderToStaticMarkup(
      <Assurance data={data} notify={() => {}} refresh={async () => {}} onJob={() => {}} />,
    );
    expect(html).toContain("No jobs to review yet");
  });
  it("Assurance maps evidence scores to tones", () => {
    expect(confidenceTone(100)).toBe("green");
    expect(confidenceTone(60)).toBe("amber");
    expect(confidenceTone(0)).toBe("red");
  });
  it("Profile and Agents show skeletons while loading", () => {
    const profile = renderToStaticMarkup(<Profile notify={() => {}} refresh={async () => {}} />);
    expect(profile).toContain('aria-label="Loading your knowledge library"');
    const data = { jobs: [], documents: [], runs: [] } as unknown as Summary;
    const agents = renderToStaticMarkup(
      <Agents data={data} notify={() => {}} onJob={() => {}} onSettings={() => {}} onAssurance={() => {}} />,
    );
    expect(agents).toContain('aria-label="Loading agent activity"');
  });
});
