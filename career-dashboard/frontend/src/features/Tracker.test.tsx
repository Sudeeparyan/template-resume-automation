import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PermitFacts } from "../components/PermitBadge";
import { TrackerRowView, trackerQuery } from "./Tracker";
import type { TrackerRow } from "../types";

const row = (over: Partial<TrackerRow> = {}): TrackerRow => ({
  key: "https://boards.greenhouse.io/acme/jobs/5", title: "Graduate Data Analyst", company: "Acme Analytics",
  url: "https://boards.greenhouse.io/acme/jobs/5", location: "Dublin, Ireland", counties: ["Dublin"], region: "",
  posting_type: "graduate_programme", level: "entry", role_family: "data", years_required: null, closing_date: "",
  posted_at: "2026-10-01", first_seen: "2026-10-03T09:00:00+00:00", last_seen: "2026-10-04T09:00:00+00:00",
  salary: { kind: "advertised", min: 40000, max: 45000, currency: "EUR", period: "YEAR", quote: "€40,000 - €45,000 per year" },
  statement: "supports", statement_quote: "We support Critical Skills Employment Permit applications.", on_eures: true,
  source: "directory", source_label: "Employer directory", source_kind: "employer_feed", lead: false, attribution: "",
  sources: 2, permits: { permits_24m: 42, legal_names: ["Acme Analytics Ireland Limited"], by_year: { "2025": 30, "2026": 12 } },
  tags: ["new"], mine: null, excluded: false, ...over,
});

describe("Tracker", () => {
  it("sends only the filters that are set", () => {
    expect(trackerQuery({ county: "Cork", type: "", hide_refusing: "1" }, { offset: "50" })).toBe("county=Cork&hide_refusing=1&offset=50");
  });

  it("shows a role with its quoted permit sentence, DETE numbers, pay and a save button", () => {
    const html = renderToStaticMarkup(<TrackerRowView row={row()} floor={36605} onSave={() => {}} saving={false} />);
    expect(html).toContain("Graduate Data Analyst");
    expect(html).toContain("Posting mentions permit support");
    expect(html).toContain("DETE: 42 permits");
    expect(html).toContain("€40,000–€45,000");
    expect(html).toContain("Save to my jobs");
    expect(html).toContain("Employer directory and 1 more");
  });

  it("marks an aggregator lead with its attribution and offers no save", () => {
    const html = renderToStaticMarkup(<TrackerRowView row={row({ lead: true, attribution: "Jobs by Jooble", source_label: "Jooble", tags: ["lead"] })}
      floor={36605} onSave={() => {}} saving={false} />);
    expect(html).toContain("Lead · Jobs by Jooble");
    expect(html).not.toContain("Save to my jobs");
  });

  it("shows the person's own status instead of the save button", () => {
    const html = renderToStaticMarkup(<TrackerRowView row={row({ mine: { job_id: "J1", status: "applied" } })} floor={0} onSave={() => {}} saving={false} />);
    expect(html).toContain("Applied");
    expect(html).not.toContain("Save to my jobs");
  });

  it("states the permit facts with their sources and says it is not advice", () => {
    const html = renderToStaticMarkup(<PermitFacts row={row({ salary: { kind: "unknown", min: null, max: null, currency: "", period: "", quote: "" } })} floor={36605} />);
    expect(html).toContain("We support Critical Skills Employment Permit applications.");
    expect(html).toContain("42 permits in the last 24 complete months to Acme Analytics Ireland Limited");
    expect(html).toContain("2025: 30");
    expect(html).toContain("Confirm base pay with the recruiter (your threshold: €36,605 a year)");
    expect(html).toContain("not immigration advice");
    expect(html).toContain("enterprise.gov.ie");
  });
});
