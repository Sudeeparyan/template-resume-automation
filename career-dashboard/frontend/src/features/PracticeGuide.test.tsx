import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import PracticeGuide, { practiceSteps } from "./PracticeGuide";
import type { Summary } from "../types";

it("labels the demo as simulated and links the five practice steps", () => {
  const html = renderToStaticMarkup(<PracticeGuide onGo={() => {}} />);
  expect(html).toContain("Practice workspace");
  expect(html).toContain("fictional employers");
  expect(html).toContain("no real applications");
  expect(practiceSteps(undefined)).toHaveLength(5);
});

it("marks tailoring, review and tracking from current workspace data", () => {
  const data = {
    jobs: [{ id: "job-1", status: "applied", application_date: "2026-10-05" }],
    documents: [{ resumes: [{ path: "practice/resume.pdf" }] }],
    runs: [{ kind: "resume_match", state: "completed" }],
  } as Summary;
  expect(practiceSteps(data).filter((step) => step.done).map((step) => step.id)).toEqual([
    "profile", "job", "tailor", "review", "track",
  ]);
});
