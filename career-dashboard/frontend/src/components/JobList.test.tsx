import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ProfileContext } from "../profiles";
import type { ProfileEntry } from "../profiles";
import type { Job } from "../types";
import { TierBadge } from "./JobList";

const profile = (country: "ie" | "us"): ProfileEntry => ({
  id: "example-profile", name: "Example Person", country, target_markets: ["ie", "us"],
  state: "ready", locked: false, initials: "EP",
  market: {
    code: country, name: country === "ie" ? "Ireland" : "United States",
    paper: country === "ie" ? "A4" : "US Letter", timezone: "Europe/Dublin", default_location: "",
    tier_labels: country === "ie" ? { C: "Posting is silent on work permits; still worth applying" } : {},
  },
});
const badge = (current: ProfileEntry, market?: "ie" | "us", label?: string) => renderToStaticMarkup(
  <ProfileContext.Provider value={{ current, profiles: [current], reload: async () => {} }}>
    <TierBadge job={{ market, sponsor_tier: "C", ...(label ? { sponsor_evidence: { label } } : {}) } as Job} long />
  </ProfileContext.Provider>,
);

describe("country-specific sponsorship wording", () => {
  it("uses US job wording inside an Ireland-primary profile", () => {
    const html = badge(profile("ie"), "us");
    expect(html).toContain("H-1B");
    expect(html).not.toContain("work permits");
  });
  it("uses Irish job wording inside a US-primary profile", () => {
    const html = badge(profile("us"), "ie");
    expect(html).toContain("work permits");
    expect(html).not.toContain("H-1B");
  });
  it("keeps recorded evidence labels and a legacy job's profile country", () => {
    expect(badge(profile("ie"))).toContain("work permits");
    expect(badge(profile("us"))).toContain("H-1B");
    expect(badge(profile("ie"), "us", "The posting's recorded label")).toContain("recorded label");
  });
});
