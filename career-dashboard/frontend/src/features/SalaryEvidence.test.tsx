import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SalaryEvidence, salaryAmount } from "../components/SalaryEvidence";
import { opportunityGroups, OpportunityLists } from "./DailySearch";
import type { Job, Opportunity } from "../types";

const opportunity = (over: Partial<Opportunity> = {}): Opportunity => ({
  salary: { kind: "advertised", currency: "EUR", minimum: 40000, maximum: 45000, annual_min: 40000, annual_max: 45000, period: "year", quote: "Salary €40,000–€45,000 per year", url: "https://example.test/jobs/1", sources: [], observed_at: "2026-10-02" },
  salary_state: "meets_floor", section: "salary_matches", floor: 36000,
  permit: { state: "needs_confirmation", summary: "Occupation and hours need confirmation.", checks: [{ id: "hours", label: "Hours per week", state: "unknown", note: "The posting does not state contracted hours." }], sources: [{ title: "Official permit criteria", url: "https://example.test/permit" }] },
  sponsorship: { state: "silent", quote: "" }, ...over,
});
const job = (id: string, over: Partial<Job> = {}): Job => ({
  id, company: "Example Company", title: `Role ${id}`, location: "Dublin", market: "ie", url: `https://example.test/jobs/${id}`, description: "A full job posting", status: "saved", notes: "", application_date: null, folder: null, created_at: "2026-10-02", selected_project_id: null, record_source: "posting", deleted_at: null, deletion_reason: "", opportunity: opportunity(), ...over,
});

describe("Salary evidence and shortlist sections", () => {
  it("keeps estimates and incomplete ranges out of advertised salary matches", () => {
    const base = opportunity();
    const jobs = [job("advertised"), job("research", { opportunity: opportunity({ section: "researched_leads", salary: { ...base.salary, kind: "researched" } }) }),
      job("pending", { opportunity: opportunity({ salary_state: "needs_confirmation", section: "needs_research" }) }),
      job("below", { opportunity: opportunity({ salary_state: "below_floor", section: "below_floor" }) })];
    const groups = opportunityGroups(jobs);
    expect(groups.salary_matches.map((j) => j.id)).toEqual(["advertised"]);
    expect(groups.researched_leads.map((j) => j.id)).toEqual(["research"]);
    expect(groups.needs_research.map((j) => j.id)).toEqual(["pending"]);
    expect(groups.below_floor.map((j) => j.id)).toEqual(["below"]);
  });
  it("preserves application history, legacy jobs and other markets", () => {
    const groups = opportunityGroups([
      job("applied", { status: "applied", opportunity: opportunity({ salary_state: "below_floor", section: "below_floor" }) }),
      job("rejected", { status: "rejected", opportunity: null }),
      job("legacy-ie", { opportunity: null }), job("us", { market: "us", opportunity: null }),
    ]);
    expect(groups.applications.map((j) => j.id)).toEqual(["applied", "rejected"]);
    expect(groups.needs_research.map((j) => j.id)).toEqual(["legacy-ie"]);
    expect(groups.other.map((j) => j.id)).toEqual(["us"]);
    expect(Object.values(groups).flat()).toHaveLength(4);
  });
  it("shows quotes, source links and missing permit criteria without implying approval", () => {
    const base = opportunity();
    const estimate = opportunity({ salary: { ...base.salary, kind: "researched", sources: [{ title: "Salary report", url: "https://example.test/salary", quote: "The stated range covers comparable roles.", observed_at: "2026-10-01" }] } });
    const html = renderToStaticMarkup(<SalaryEvidence job={job("research", { opportunity: estimate })} />);
    expect(html).toContain("researched estimate, not an advertised offer");
    expect(html).toContain("https://example.test/salary");
    expect(html).toContain("The stated range covers comparable roles.");
    expect(html).toContain("Hours per week");
    expect(html).toContain("Needs confirmation");
    expect(html).toContain("not a permit approval");
    expect(html).toContain("€36,000");
  });
  it("does not make unsafe source URLs clickable", () => {
    const base = opportunity();
    const html = renderToStaticMarkup(<SalaryEvidence job={job("unsafe", { opportunity: opportunity({ salary: { ...base.salary, url: "javascript:alert(1)", sources: [] } }) })} />);
    expect(html).not.toContain("javascript:");
  });
  it("shows upper bounds honestly and retains pending sections", () => {
    const base = opportunity();
    expect(salaryAmount({ ...base.salary, minimum: null, maximum: 40000 })).toBe("up to €40,000 / year");
    expect(salaryAmount({ ...base.salary, kind: "researched", minimum: null, maximum: null, annual_min: 42000, annual_max: 50000 })).toBe("€42,000–€50,000 / year");
    expect(salaryAmount({ ...base.salary, currency: "", period: "UNKNOWN" })).toContain("currency unconfirmed");
    const html = renderToStaticMarkup(<OpportunityLists jobs={[job("pending", { opportunity: null })]} onJob={() => {}} />);
    expect(html).toContain("Salary needs confirmation");
    expect(html).not.toContain("Advertised salary matches");
    expect(html).not.toContain("Ready to apply");
  });
});
