import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { Interview } from "../types";
import { formatWhen, localDay, nextInterviewByJob, UpcomingInterviews, weekDays } from "./Interviews";

const interview = (over: Partial<Interview>): Interview => ({
  id: "i1", job_id: "job-1", company: "Example Studio", title: "Graduate Engineer", stage: "technical",
  stage_label: "Technical interview", starts_at: "2026-11-25T14:00+00:00", starts_utc: "2026-11-25T14:00:00+00:00",
  duration_min: 60, where_text: "Video call", notes: "", ...over,
});

describe("interview dates", () => {
  it("shows Irish local time on both sides of the clock change", () => {
    expect(formatWhen("2026-11-25T14:00+00:00")).toContain("14:00");
    expect(formatWhen("2026-07-01T14:00+01:00")).toContain("14:00");
    expect(localDay("2026-11-25T23:30:00+00:00")).toBe("2026-11-25");
  });
  it("builds the Monday-to-Sunday week around today", () => {
    expect(weekDays("2026-11-20")).toEqual([
      "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22",
    ]);
    expect(weekDays("2026-11-16")[0]).toBe("2026-11-16");
    expect(weekDays("2026-11-22")[6]).toBe("2026-11-22");
  });
  it("keeps only each job's soonest interview for its card", () => {
    const map = nextInterviewByJob([interview({}), interview({ id: "i2", starts_at: "2026-11-28T10:00+00:00" })]);
    expect(Object.keys(map)).toEqual(["job-1"]);
    expect(map["job-1"]).toContain("14:00");
  });
});

describe("upcoming interviews card", () => {
  it("marks the busy day in the week strip and lists the interview", () => {
    const html = renderToStaticMarkup(<UpcomingInterviews items={[interview({ starts_at: "2026-11-18T14:00+00:00" })]} today="2026-11-20" onOpen={() => {}} />);
    expect(html).toContain("1 interview");
    expect(html).toContain("Example Studio · Graduate Engineer");
    expect(html).toContain("calendar.ics");
  });
  it("explains how to add one when there are none", () => {
    const html = renderToStaticMarkup(<UpcomingInterviews items={[]} today="2026-11-20" onOpen={() => {}} />);
    expect(html).toContain("No interviews in the next two weeks");
    expect(html).not.toContain("calendar.ics");
  });
});
