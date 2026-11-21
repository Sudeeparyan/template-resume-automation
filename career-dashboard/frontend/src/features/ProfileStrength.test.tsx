import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ProfileStrength, { strengthWord } from "./ProfileStrength";
import type { ProfileStrength as Strength } from "../types";

const strength: Strength = {
  score: 65,
  checks: [
    { id: "contact", label: "Name, email and a phone number or LinkedIn", why: "", where: "profile", done: true },
    { id: "education", label: "Education with degree and dates", why: "Dates must be exact.", where: "profile", done: false },
  ],
  next: [{ id: "education", label: "Education with degree and dates", why: "Dates must be exact.", where: "profile" }],
  tips: [{ id: "certification", label: "A certificate or course, if you have one", why: "Optional.", where: "profile" }],
};

describe("profile strength meter", () => {
  it("shows the score, what is done and what to add next with an Add button", () => {
    const html = renderToStaticMarkup(<ProfileStrength strength={strength} onAdd={() => {}} />);
    expect(html).toContain("Profile strength 65 out of 100");
    expect(html).toContain("Taking shape");
    expect(html).toContain("Education with degree and dates");
    expect(html).toContain("Dates must be exact.");
    expect(html).toContain("A certificate or course");
    expect(html.match(/> Add</g)?.length ?? html.match(/Add<\/button>/g)?.length).toBeGreaterThan(0);
  });
  it("hides the Add buttons when the page cannot open a form", () => {
    expect(renderToStaticMarkup(<ProfileStrength strength={strength} />)).not.toContain("<button");
  });
  it("words the score without judging the person", () => {
    expect(strengthWord(95)).toBe("Ready for strong resumes");
    expect(strengthWord(10)).toBe("Just started");
  });
});
