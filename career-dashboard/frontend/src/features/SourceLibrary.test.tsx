import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ProfileEntry } from "../profiles";
import SourceLibrary, { etaLabel, marketSelection, progressPercent } from "./SourceLibrary";
import OnboardingWorkspace from "./OnboardingWorkspace";

const profile: ProfileEntry = {
  id: "sample-profile",
  name: "Sample Person",
  country: "ie",
  state: "onboarding",
  locked: false,
  initials: "SP",
  market: null,
};

describe("profile source library", () => {
  it("gives first-run AI setup instructions while Settings is unavailable", () => {
    const html = renderToStaticMarkup(<OnboardingWorkspace profile={profile} notify={() => {}} onBuilt={() => {}} setupForm={false} chooseSetup={() => {}} />);
    expect(html).toContain("Set up AI before the first build");
    expect(html).toContain("career-dashboard/.env.example");
    expect(html).toContain("career-dashboard/.env");
    expect(html).toContain("Settings opens after the profile is built");
  });
  it("offers the complete source and build workflow to a new profile", () => {
    const html = renderToStaticMarkup(<SourceLibrary profile={profile} notify={() => {}} compact />);
    expect(html).toContain("Profile sources");
    expect(html).toContain("Choose files");
    expect(html).toContain("Add personal information");
    expect(html).toContain("Build Agent for You");
    expect(html).toContain("Ireland");
    expect(html).toContain("United States");
    expect(html).toMatch(/type="checkbox" checked=""[^>]*\/> Ireland/);
    expect(html).toMatch(/class="source-build-button" disabled=""/);
  });

  it("uses saved market and eligibility settings when reopening a profile", () => {
    const html = renderToStaticMarkup(<SourceLibrary profile={{
      ...profile,
      state: "ready",
      target_markets: ["us"],
      work_authorization_by_market: { us: { status: "authorized", citizenship: "noncitizen" } },
    }} notify={() => {}} />);
    expect(html).toContain("Rebuild profile &amp; agents");
    expect(html).toContain("US roles and US resume format");
    expect(html).toContain('<option value="authorized" selected="">Authorized</option>');
    expect(html).toContain('<option value="noncitizen" selected="">Noncitizen</option>');
    expect(html).toContain("Will employer sponsorship be needed later?");
    expect(html).toContain('<option value="unknown" selected="">Not confirmed</option>');
  });

  it("bounds progress and keeps an Ireland default for unknown markets", () => {
    expect(progressPercent(-5)).toBe(0);
    expect(progressPercent(60.7)).toBe(61);
    expect(progressPercent(120)).toBe(100);
    expect(marketSelection(["xx"])).toEqual(["ie"]);
    expect(marketSelection(["us", "ie", "us"])).toEqual(["us", "ie"]);
    expect(etaLabel(60, 150)).toContain("left");
  });
});
