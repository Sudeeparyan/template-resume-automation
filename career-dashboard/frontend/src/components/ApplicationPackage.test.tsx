import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import ApplicationPackage, { applicationEmail } from "./ApplicationPackage";
import type { Job, Summary } from "../types";

const job = {
  id: "job-1", company: "Example Employer", title: "Graduate Engineer", status: "saved",
  url: "https://example.org/jobs/1", posting_state: "active", fit_score: 80,
  application_date: null, notes: "",
} as Job;
const data = { goals: { date: "2026-10-05" }, documents: [], runs: [] } as unknown as Summary;

it("offers preparation and the apply link without submitting", () => {
  const html = renderToStaticMarkup(<ApplicationPackage job={job} data={data} refresh={async () => {}} notify={() => {}} />);
  expect(html).toContain("Prepare application");
  expect(html).toContain("Open posting / apply");
  expect(html).toContain("Mark as applied");
  expect(html).toContain("Review each item, then apply through the posting yourself");
  expect(html).not.toContain("Open in mail app");
});

it("uses an editable, attachment-free email draft", () => {
  expect(applicationEmail(job).subject).toBe("Application: Graduate Engineer at Example Employer");
  expect(applicationEmail(job).body).toContain("I have attached my tailored resume");
  const withResume = { ...data, documents: [{ job_id: "job-1", resumes: [{ label: "Resume", path: "applications/job/resume.pdf" }] }] } as Summary;
  const html = renderToStaticMarkup(<ApplicationPackage job={job} data={withResume} refresh={async () => {}} notify={() => {}} />);
  expect(html).toContain("Word resume");
  expect(html).toContain("Generate cover letter");
  expect(html).toContain("Open in mail app");
});
