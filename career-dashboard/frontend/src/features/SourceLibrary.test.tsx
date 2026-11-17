import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ProfileEntry } from "../profiles";
import SourceLibrary, { etaLabel, marketSelection, profileBuildSettings, progressPercent } from "./SourceLibrary";
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
  it("starts a new profile with sources, permission facts and the build", () => {
    const html = renderToStaticMarkup(<OnboardingWorkspace profile={profile} notify={() => {}} onBuilt={() => {}} setupForm={false} chooseSetup={() => {}} />);
    expect(html).toContain("confirm your permission to work in Build settings");
    expect(html).toContain("Build Agent for You");
    expect(html).not.toContain("United States, or both");
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

  it("shows Ireland as the only market when this copy offers one market", () => {
    const html = renderToStaticMarkup(<SourceLibrary profile={{ ...profile, offered_markets: ["ie"] }} notify={() => {}} compact />);
    expect(html).toContain("Job market: Ireland");
    expect(html).not.toContain("United States");
    expect(html).not.toMatch(/type="checkbox"[^>]*\/> Ireland/);
    expect(profileBuildSettings({ ...profile, offered_markets: ["ie"], target_markets: ["us"] }).markets).toEqual(["us"]);
    const legacy = renderToStaticMarkup(<SourceLibrary profile={{ ...profile, offered_markets: ["ie"], target_markets: ["us"] }} notify={() => {}} />);
    expect(legacy).toContain("United States is no longer offered in this copy.");
    expect(legacy).toContain("Create a separate profile for an offered market");
    expect(legacy).toContain("US roles and US resume format");
    expect(legacy).toMatch(/class="source-build-button" disabled=""/);
    expect(profileBuildSettings({ ...profile, offered_markets: ["ie"], target_markets: ["us", "ie"] }).markets).toEqual(["ie"]);
  });

  it("uses saved market and eligibility settings when reopening a profile", () => {
    const html = renderToStaticMarkup(<SourceLibrary profile={{
      ...profile,
      state: "ready",
      offered_markets: ["ie", "us"],
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

  it("detects persisted market and eligibility changes without discarding edits on ordinary profile reloads", () => {
    const signature = (entry: ProfileEntry) => JSON.stringify(profileBuildSettings(entry));
    const saved: ProfileEntry = {
      ...profile, target_markets: ["us"],
      work_authorization_by_market: { us: { status: "authorized", citizenship: "noncitizen", needs_sponsorship_later: "yes" } },
    };
    expect(signature({ ...saved, name: "Updated display name", state: "ready" })).toBe(signature(saved));
    const updated: ProfileEntry = {
      ...saved, target_markets: ["ie", "us"],
      work_authorization_by_market: { us: { status: "authorized", citizenship: "noncitizen", needs_sponsorship_later: "no" } },
    };
    expect(signature(updated)).not.toBe(signature(saved));
    expect(profileBuildSettings(updated)).toMatchObject({
      markets: ["ie", "us"], authorization: { us: { needs_sponsorship_later: "no" }, ie: { status: "unknown" } },
    });
  });
});
