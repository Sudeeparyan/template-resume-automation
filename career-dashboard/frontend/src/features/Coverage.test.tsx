import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CoverageReport, coverageSourceLabel } from "./Coverage";

describe("Recorded search coverage", () => {
  it("explains partial searches, blockers and scope without claiming exhaustive results", () => {
    const html = renderToStaticMarkup(<CoverageReport data={{ sources: [
      { key: "ai:ie:1:county:kerry", state: "attempted", cursor: {}, checked_at: "2026-10-02", found: 2, error: null },
      { key: "employer:https://example.test/careers", state: "partial", cursor: { offset: 25 }, checked_at: "2026-10-02", found: 3, error: null },
      { key: "jobs_ie:data analyst", state: "blocked", cursor: { page: 2 }, checked_at: "2026-10-02", found: 0, error: "The source denied access." },
    ], complete: 0, partial: 1, failed: 1, scope: "Configured sources only; coverage is not exhaustive." }} />);
    expect(html).toContain("County Kerry");
    expect(html).toContain("Search attempted");
    expect(html).toContain("More to check");
    expect(html).toContain("Access blocked");
    expect(html).toContain("Offset: 25");
    expect(html).toContain("The source denied access.");
    expect(html).toContain("coverage is not exhaustive");
  });
  it("renders an honest empty state and readable source labels", () => {
    const html = renderToStaticMarkup(<CoverageReport data={{ sources: [], complete: 0, partial: 0, failed: 0, scope: "Configured sources only." }} />);
    expect(html).toContain("No source checks have been recorded yet");
    expect(coverageSourceLabel("feeds:directory")).toBe("Public feed directory");
    expect(coverageSourceLabel("workday:example.test/site:Ireland")).toBe("Workday · example.test/site · Ireland");
  });
});
