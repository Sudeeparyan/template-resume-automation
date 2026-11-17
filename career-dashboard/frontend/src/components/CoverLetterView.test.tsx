import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CoverLetterView } from "./CoverLetterView";
import type { CoverLetter } from "../types";

const letter = (over: Partial<CoverLetter> = {}): CoverLetter => ({
  job_id: "job-1", company: "Acme Analytics", title: "Data Analyst", version: 2,
  content: "Example Graduate\n\nDear Hiring Team,\n\nI am applying for the Data Analyst role.",
  path: "applications/job-1/cover-letter-v2.md", docx_path: "applications/job-1/cover-letter-v2.docx",
  created_at: "2026-10-04T10:00:00Z", method: "ai", evidence_ids: ["PROJ-001"],
  company_facts: [{ text: "Acme builds reporting software.", quote: "builds reporting software", url: "https://acme.example/about" }],
  note: "", review_required: true, ...over,
});

describe("CoverLetterView", () => {
  it("says the AI draft was checked, lists the company facts with sources and offers the Word copy", () => {
    const html = renderToStaticMarkup(<CoverLetterView letter={letter()} onClose={() => {}} />);
    expect(html).toContain("Written by the AI from your registered evidence, then checked");
    expect(html).toContain("Company facts it uses (1)");
    expect(html).toContain("https://acme.example/about");
    expect(html).toContain("/v2/jobs/job-1/cover-letter/download?format=docx");
    expect(html).toContain("Draft only.");
  });

  it("explains a template letter and why the AI draft was set aside, with no Word link when copies are off", () => {
    const html = renderToStaticMarkup(
      <CoverLetterView letter={letter({ method: "template", docx_path: "", company_facts: [],
                                        note: "The AI's draft was set aside (numbers that are not in the evidence: 45)." })}
                       onClose={() => {}} />,
    );
    expect(html).toContain("Built from your own registered sentences.");
    expect(html).toContain("set aside");
    expect(html).not.toContain("Download Word");
    expect(html).not.toContain("Company facts it uses");
  });
});
