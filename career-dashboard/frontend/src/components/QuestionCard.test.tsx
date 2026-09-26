import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import QuestionCard, { type Question } from "./QuestionCard";
import { composerHint, type SetupView } from "../features/OnboardingChat";

const country: Question = {
  id: "q1",
  key: "country",
  text: "Which country should I search for jobs in?",
  options: [
    { label: "Ireland", description: "From your documents · A4 resumes", value: "ie" },
    { label: "United States", description: "US Letter resumes", value: "us" },
  ],
  multi: false,
  other: false,
  skippable: false,
};
const roles: Question = {
  id: "q2",
  text: "Which roles should I search for?",
  options: [
    { label: "Data Analyst", value: "Data Analyst" },
    { label: "Data Scientist", value: "Data Scientist" },
    { label: "ML Engineer", value: "ML Engineer" },
  ],
  multi: true,
  other: true,
  skippable: false,
  defaults: ["Data Analyst", "Data Scientist", "Not an option"],
  placeholder: "Other roles, separated by commas",
};
const noop = () => {};

describe("QuestionCard", () => {
  it("numbers single-choice answers and offers no skip or free text when neither is allowed", () => {
    const html = renderToStaticMarkup(<QuestionCard question={country} busy={false} onAnswer={noop} />);
    expect(html).toContain("Ireland");
    expect(html).toContain("From your documents · A4 resumes");
    expect(html).toMatch(/question-key[^>]*>1</);
    expect(html).toMatch(/question-key[^>]*>2</);
    expect(html).not.toContain("Skip");
    expect(html).not.toContain("<input");
  });
  it("pre-selects the known defaults of a multi-select and counts them on Continue", () => {
    const html = renderToStaticMarkup(<QuestionCard question={roles} busy={false} onAnswer={noop} />);
    expect(html.match(/question-option selected/g)?.length).toBe(2);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("Continue with 2");
    expect(html).toContain("Other roles, separated by commas");
  });
  it("shows Skip only on a skippable question, and an open question is just a line to type", () => {
    const open: Question = { id: "q3", text: "When can you start?", options: [], multi: false, other: true, skippable: true, placeholder: "A date or notice period" };
    const html = renderToStaticMarkup(<QuestionCard question={open} busy={false} onAnswer={noop} />);
    expect(html).toContain("Skip");
    expect(html).toContain("A date or notice period");
    expect(html).not.toContain("question-options");
  });
  it("disables every answer while the last one is being sent", () => {
    const html = renderToStaticMarkup(<QuestionCard question={country} busy onAnswer={noop} />);
    expect(html.match(/disabled=""/g)?.length).toBe(2);
  });
});

describe("the setup chat composer", () => {
  const view = (state: string, pending: Question | null = null): SetupView => ({
    messages: [],
    pending,
    thinking: false,
    intake: { state, steps: [], files: [] },
  });
  it("invites documents before reading, and a typed answer while a question is open", () => {
    expect(composerHint(view("empty"))).toBe("Paste text or attach documents");
    expect(composerHint(view("reading"))).toContain("stop");
    expect(composerHint(view("review", roles))).toBe("Tick answers above, or type the complete list here");
    expect(composerHint(view("review", country))).toBe("Tell me anything to add or correct");
  });
});
