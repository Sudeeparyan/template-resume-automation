import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { FollowUpItem } from "../types";
import { FollowUpsCard } from "./FollowUps";

const item = (over: Partial<FollowUpItem>): FollowUpItem => ({
  job_id: "job-1", company: "Quiet Company", title: "Data Analyst", kind: "follow_up", days_quiet: 12, sent: 0,
  reason: "Applied 12 days ago, no reply recorded.", draft: null, ...over,
});

const render = (items: FollowUpItem[]) =>
  renderToStaticMarkup(<FollowUpsCard items={items} notify={() => {}} refresh={async () => {}} onOpen={() => {}} />);

describe("follow-ups card", () => {
  it("lists what is due with the reason and a draft button, never a send button", () => {
    const html = render([item({}), item({ job_id: "job-2", company: "Interview Co", kind: "thank_you", reason: "Technical interview on 18 November 2026." })]);
    expect(html).toContain("2 due");
    expect(html).toContain("Applied 12 days ago, no reply recorded.");
    expect(html).toContain("Draft follow-up");
    expect(html).toContain("Draft thank-you");
    expect(html.toLowerCase()).not.toContain(">send");
  });
  it("reopens an unsent draft instead of writing a new one", () => {
    const html = render([item({ draft: { id: 4, job_id: "job-1", kind: "follow_up", text: "x", method: "template", drafted_at: "", marked_sent_at: null } })]);
    expect(html).toContain("Open draft");
  });
  it("explains the rule when nothing is due", () => {
    expect(render([])).toContain("Applications quiet for 10 days");
  });
});
