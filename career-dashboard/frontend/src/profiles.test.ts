import { describe, expect, it } from "vitest";
import { profileRoute } from "./profiles";
import type { ProfileEntry, ProfileListing } from "./profiles";

const profile = (id: string, state: ProfileEntry["state"] = "ready") =>
  ({ id, state } as ProfileEntry);

describe("profile route after a template reset", () => {
  it("stops profile requests and returns a stale tab to onboarding when the server confirms zero profiles", () => {
    const listing: ProfileListing = { profiles: [], last_used: "" };
    expect(profileRoute(listing, "removed-profile", false)).toMatchObject({
      current: null, destination: "/", ready: false,
    });
    // An earlier fetch failure cannot override the successful empty list.
    expect(profileRoute(listing, "removed-profile", true).ready).toBe(false);
    expect(profileRoute(listing, null, false).destination).toBeNull();
  });

  it("waits through a transient listing failure and selects a surviving profile after a reset", () => {
    expect(profileRoute(null, "removed-profile", true).destination).toBeNull();
    expect(profileRoute(null, "removed-profile", true).ready).toBe(false);
    const listing: ProfileListing = { profiles: [profile("new-person")], last_used: "removed-profile" };
    expect(profileRoute(listing, "removed-profile", false)).toMatchObject({
      current: null, destination: "/p/new-person/", ready: false,
    });
    expect(profileRoute(listing, "new-person", false)).toMatchObject({
      current: listing.profiles[0], destination: null, ready: true,
    });
  });

  it("never starts an unscoped workspace request when the shell listing is unavailable", () => {
    expect(profileRoute(null, null, true)).toMatchObject({
      current: null, destination: null, ready: false, unavailable: true,
    });
    expect(profileRoute(null, null, false)).toMatchObject({ ready: false, unavailable: false });
    const listing: ProfileListing = { profiles: [profile("selected-person")], last_used: "selected-person" };
    expect(profileRoute(listing, "selected-person", true).ready).toBe(true);
  });

  it("keeps an onboarding profile out of the built workspace API", () => {
    const listing: ProfileListing = { profiles: [profile("student", "onboarding")], last_used: "student" };
    expect(profileRoute(listing, "student", false)).toMatchObject({
      current: listing.profiles[0], destination: null, ready: false,
    });
  });
});
