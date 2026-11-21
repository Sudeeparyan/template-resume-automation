import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { Job } from "../types";
import ApplicationBoard from "./ApplicationBoard";

const job = (over: Partial<Job>): Job => ({
  id: "job-1", company: "Example Studio", title: "Graduate Engineer", location: "Dublin, Ireland", url: "https://example.org",
  description: "", status: "saved", notes: "", application_date: null, folder: null, created_at: "2026-11-01T09:00:00",
  selected_project_id: null, record_source: "posting", deleted_at: null, deletion_reason: "", ...over,
});

const render = (jobs: Job[], extra: Partial<Parameters<typeof ApplicationBoard>[0]> = {}) =>
  renderToStaticMarkup(
    <ApplicationBoard jobs={jobs} evidence={new Set()} today="2026-11-20" notify={() => {}} refresh={async () => {}}
      onOpen={() => {}} onDetails={() => {}} onRemove={() => {}} {...extra} />,
  );

describe("application board", () => {
  it("shows every stage with its count, in stage words", () => {
    const html = render([
      job({ id: "a", fit_score: 85 }),
      job({ id: "b", status: "applied", application_date: "2026-11-08", company: "Second Company" }),
      job({ id: "c", status: "ghosted", application_date: "2026-10-01", company: "Third Company" }),
    ]);
    for (const label of ["Discovered", "Tailored", "Applied", "Interview", "Offer", "Closed"]) expect(html).toContain(label);
    expect(html).toContain("Discovered: 1 application");
    expect(html).toContain("Applied: 1 application");
    expect(html).toContain("Closed: 1 application");
    expect(html).toContain("Fit 85");
    expect(html).toContain("Applied 12 days ago");
    // Closed is folded away until opened, so its card is not drawn yet.
    expect(html).not.toContain("Third Company");
    // Raw status codes never show as words on the board.
    expect(html).not.toMatch(/>saved<|>prepared<|>ghosted</);
  });
  it("gives every card a keyboard grip and a Move menu without its own column", () => {
    const html = render([job({ id: "a" })]);
    expect(html).toContain('aria-label="Move Graduate Engineer at Example Studio"');
    expect(html).toContain("Move to…");
    expect(html).not.toContain('<option value="saved">');
    expect(html).toContain('<option value="applied">Applied</option>');
  });
  it("offers removal only before an application is sent", () => {
    expect(render([job({ id: "a" })])).toContain("Remove Example Studio Graduate Engineer");
    expect(render([job({ id: "a", status: "applied", application_date: "2026-11-02" })])).not.toContain("Remove Example Studio");
  });
  it("flags an upcoming interview and a follow-up that is due", () => {
    const html = render([job({ id: "a", status: "interview", application_date: "2026-11-02" })], {
      nextInterview: { a: "Tue 25 Nov · 14:00" },
      followUpDue: new Set(["a"]),
    });
    expect(html).toContain("Tue 25 Nov · 14:00");
    expect(html).toContain("Follow-up due");
  });
});
