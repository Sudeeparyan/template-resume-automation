import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { FitList } from "./FitCheck";
import type { JobFit } from "../types";

const fit: JobFit = {
  matrix: {
    requirements: [
      { text: "Python", category: "required", excerpt: "Required: Python and SQL.", status: "met", evidence_ids: ["SKILL-LANGUAGES-001"], note: "" },
      { text: "Kubernetes", category: "required", excerpt: "Experience with Kubernetes is required.", status: "missing", evidence_ids: [], note: "" },
      { text: "degree in computer science", category: "preferred", excerpt: "Preferred: a degree in computer science.", status: "partial", evidence_ids: ["COURSEWORK-MS-001"], note: "coursework only" },
      { text: "Build dashboards", category: "responsibility", excerpt: "Build dashboards.", status: "unknown", evidence_ids: [], note: "" },
    ],
    hard_blockers: [{ excerpt: "Candidates must hold an active Top Secret security clearance.", reason: "requires a security clearance" }],
    summary: "",
  },
  method: "ai", provider: "kimi_cli", model: "k3", provider_label: "Kimi Code", score: 58,
  components: { requirements: 33, role_seniority: 15, location: 10 }, must_have_ok: true,
  rationale: "Fit 58/100. Meets 1 of 2 must-haves (Python). Missing: Kubernetes.", ai_error: "",
};

describe("The requirement check", () => {
  it("quotes each item from the posting, with the evidence that meets it and the gaps named", () => {
    const html = renderToStaticMarkup(<FitList fit={fit} />);
    expect(html).toContain("Meets 1 of 2 must-haves");
    expect(html).toContain("Must-haves <small>· 1 of 2 met</small>");
    expect(html).toContain("The posting says: “Required: Python and SQL.”");
    expect(html).toContain("Your evidence: SKILL-LANGUAGES-001");
    expect(html).toContain('class="fit-item missing"');
    expect(html).toContain("coursework only");
    expect(html).toContain("Blocker: requires a security clearance.");
    // An item the rules could not name is not shown as met or missing.
    expect(html).not.toContain("Build dashboards");
    expect(html).not.toContain("The work itself");
  });
});
