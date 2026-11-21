import { describe, expect, it } from "vitest";
import type { Job } from "../types";
import { cardSignature, checkMove, columnFor, daysSince, groupByColumn, stageAge, withPending } from "./boardModel";

const job = (over: Partial<Job> = {}): Job => ({
  id: "job-1", company: "Example Studio", title: "Graduate Engineer", location: "Dublin, Ireland", url: "https://example.org",
  description: "", status: "saved", notes: "", application_date: null, folder: null, created_at: "2026-11-01T09:00:00",
  selected_project_id: null, record_source: "posting", deleted_at: null, deletion_reason: "", ...over,
});

describe("board columns", () => {
  it("puts every stored status in exactly one column", () => {
    expect(columnFor("saved")).toBe("saved");
    expect(columnFor("prepared")).toBe("prepared");
    expect(columnFor("applied")).toBe("applied");
    expect(columnFor("interview")).toBe("interview");
    expect(columnFor("offer")).toBe("offer");
    for (const closed of ["rejected", "withdrawn", "ghosted"]) expect(columnFor(closed)).toBe("closed");
  });
  it("sorts early columns by fit and later ones by most recent", () => {
    const groups = groupByColumn([
      job({ id: "low", fit_score: 40 }),
      job({ id: "high", fit_score: 90 }),
      job({ id: "old", status: "applied", application_date: "2026-10-01" }),
      job({ id: "new", status: "applied", application_date: "2026-11-10" }),
    ]);
    expect(groups.saved.map((j) => j.id)).toEqual(["high", "low"]);
    expect(groups.applied.map((j) => j.id)).toEqual(["new", "old"]);
    expect(groups.closed).toEqual([]);
  });
});

describe("move rules (the same as the server's)", () => {
  it("does nothing when dropped on its own column", () => {
    expect(checkMove(job(), "saved", false)).toEqual({ kind: "same" });
    expect(checkMove(job({ status: "ghosted" }), "closed", false)).toEqual({ kind: "same" });
  });
  it("asks for the real submission date before Applied", () => {
    expect(checkMove(job(), "applied", false)).toEqual({ kind: "needs_date", status: "applied", required: true });
    // An interview or offer may be recorded without a remembered date.
    expect(checkMove(job(), "interview", false)).toEqual({ kind: "needs_date", status: "interview", required: false });
  });
  it("needs no date when one is recorded or an email confirmed the application", () => {
    expect(checkMove(job({ application_date: "2026-11-02" }), "applied", false)).toEqual({ kind: "ok", status: "applied" });
    expect(checkMove(job(), "applied", true)).toEqual({ kind: "ok", status: "applied" });
  });
  it("keeps a recorded application in the history", () => {
    for (const status of ["applied", "interview", "offer", "rejected", "withdrawn", "ghosted"]) {
      const check = checkMove(job({ status, application_date: "2026-11-02", folder: "x" }), "saved", false);
      expect(check.kind).toBe("blocked");
      expect(checkMove(job({ status, folder: "x" }), "prepared", false).kind).toBe("blocked");
    }
    expect(checkMove(job({ application_date: "2026-11-02" }), "saved", false).kind).toBe("same");
    expect(checkMove(job(), "saved", true).kind).toBe("same");
  });
  it("needs a prepared package for Tailored", () => {
    expect(checkMove(job(), "prepared", false)).toMatchObject({ kind: "blocked", action: "prepare" });
    expect(checkMove(job({ folder: "data/output/x" }), "prepared", false)).toEqual({ kind: "ok", status: "prepared" });
  });
  it("asks how an application closed", () => {
    expect(checkMove(job({ status: "applied", application_date: "2026-11-02" }), "closed", false)).toEqual({ kind: "choose_closed" });
  });
  it("lets a closed application reopen into a later stage", () => {
    expect(checkMove(job({ status: "rejected", application_date: "2026-11-02" }), "interview", false)).toEqual({ kind: "ok", status: "interview" });
  });
});

describe("optimistic moves and card bookkeeping", () => {
  it("shows a pending move over the server's copy, leaving other jobs untouched", () => {
    const jobs = [job(), job({ id: "job-2" })];
    const shown = withPending(jobs, { "job-1": { status: "applied", application_date: "2026-11-03" } });
    expect(shown[0].status).toBe("applied");
    expect(shown[1]).toBe(jobs[1]);
    expect(withPending(jobs, {})).toBe(jobs);
  });
  it("changes a card's signature only when what it shows changes", () => {
    expect(cardSignature(job())).toBe(cardSignature(job()));
    expect(cardSignature(job())).not.toBe(cardSignature(job({ status: "applied" })));
    expect(cardSignature(job(), "Tue 14:00")).not.toBe(cardSignature(job()));
  });
  it("describes time in the stage", () => {
    expect(daysSince("2026-11-10", "2026-11-20")).toBe(10);
    expect(daysSince(null, "2026-11-20")).toBeNull();
    expect(stageAge(job({ status: "applied", application_date: "2026-11-19" }), "2026-11-20")).toBe("Applied 1 day ago");
    expect(stageAge(job({ status: "applied" }), "2026-11-20")).toBe("Applied · date not recorded");
    expect(stageAge(job({ created_at: "2026-11-20T08:00:00" }), "2026-11-20")).toBe("Saved today");
  });
});
