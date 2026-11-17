import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { GraphStepsView, type GraphHistory } from "./GraphSteps";

const history: GraphHistory = {
  run_id: "run-1",
  note: "Values are redacted: type, size and a short hash.",
  threads: [{
    thread: "research:run-1", graph: "research", finished: false, resumes_at: ["comparison"],
    steps: [
      { checkpoint_id: "c1", step: 0, source: "loop", at: "2026-10-04T12:00:00Z", ran: [], changed: {}, removed: [], next: ["dossier"] },
      { checkpoint_id: "c2", step: 1, source: "loop", at: "2026-10-04T12:01:00Z", ran: ["dossier"],
        changed: { research: { type: "dict", size: 812, sha: "abc123", preview: "{\"summary\": \"Acme\"}" } }, removed: [],
        next: ["comparison"] },
    ],
  }],
};

describe("GraphSteps", () => {
  it("lists each node with what it changed and where a stopped run resumes, without values by default", () => {
    const html = renderToStaticMarkup(<GraphStepsView history={history} showData={false} />);
    expect(html).toContain("stopped; resumes at comparison");
    expect(html).toContain("dossier");
    expect(html).toContain("dict, 812 chars");
    expect(html).not.toContain("Acme");
    expect(renderToStaticMarkup(<GraphStepsView history={history} showData={true} />)).toContain("Acme");
  });

  it("offers to run a finished research run again from a step, and marks a rerun's fork", () => {
    const finished: GraphHistory = { ...history, threads: [{ ...history.threads[0], finished: true, resumes_at: [],
      steps: [...history.threads[0].steps, { checkpoint_id: "c3", step: 2, source: "fork", at: null, ran: [], changed: {}, removed: [], next: [] }] }] };
    const html = renderToStaticMarkup(<GraphStepsView history={finished} showData={false} onRerun={() => {}} />);
    expect(html).toContain("Run again from comparison");
    expect(html).toContain("Run again from dossier");
    expect(html).toContain("copying an earlier run");
    // A stopped run resumes by itself; it is not offered for a rerun.
    expect(renderToStaticMarkup(<GraphStepsView history={history} showData={false} onRerun={() => {}} />)).not.toContain("Run again");
  });

  it("says when a run has no checkpoints", () => {
    const html = renderToStaticMarkup(<GraphStepsView history={{ ...history, threads: [] }} showData={false} />);
    expect(html).toContain("not made by a graph");
  });
});
