import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AskAssistant, AskContext, RichText, ReportView } from "./UI";
import { JobList, TierBadge, tierTitle } from "./JobList";
import { ExcludedRoles } from "../features/Dashboard";
import { safeUrl, fileUrl } from "../api";
import type { Job } from "../types";
import { ProfileContext, type ProfileEntry } from "../profiles";
describe("Agent report safety", () => {
  it("renders untrusted markup as plain text", () => {
    const html = renderToStaticMarkup(
      <RichText
        text={
          "<script>alert(1)</script>\n[Unsafe](javascript:alert(1))\n[Source](https://example.test/source)"
        }
      />,
    );
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain('href="https://example.test/source"');
  });
  it("rejects credential and script navigation", () => {
    expect(safeUrl("javascript:alert(1)")).toBe("#");
    expect(safeUrl("file:///private")).toBe("#");
  });
  it("encodes output filenames", () => {
    expect(fileUrl("base/my resume.pdf")).toBe(
      "/api/files/base/my%20resume.pdf",
    );
  });
  it("makes report limitations visible", () => {
    const html = renderToStaticMarkup(
      <ReportView
        report={{
          summary: "Role expectations",
          report: "## Skills\nSQL",
          sources: [],
          limitations: ["Hiring criteria are inferred"],
        }}
      />,
    );
    expect(html).toContain("Hiring criteria are inferred");
    expect(html).toContain("<h3>Skills</h3>");
  });
  it("shows a blocked independent review and safely renders its issues", () => {
    const html = renderToStaticMarkup(<ReportView report={{
      summary: "Check the resume before applying", report: "Review text", sources: [], limitations: [],
      verdict: "blocked", issues: ["<script>Unsupported experience</script>"],
    }} />);
    expect(html).toContain("Review blocked");
    expect(html).toContain('class="badge red"');
    expect(html).toContain('aria-label="Review issues"');
    expect(html).toContain("Unsupported experience");
    expect(html).not.toContain("<script>");
  });
  it("keeps a review with unresolved issues out of the passed state", () => {
    const html = renderToStaticMarkup(<ReportView report={{
      summary: "Check this issue", report: "Review text", sources: [], limitations: [],
      verdict: "pass", issues: ["An unsupported skill is printed"],
    }} />);
    expect(html).toContain("Needs your review");
    expect(html).not.toContain("Review passed");
  });
});

it("renders hiring comparisons as safe tables", () => {
  const html = renderToStaticMarkup(
    <RichText
      text={
        "| Skill | Evidence |\n|---|---|\n| SQL | <script>unsafe</script> |"
      }
    />,
  );
  expect(html).toContain("<table>");
  expect(html).toContain("<th>Skill</th>");
  expect(html).not.toContain("<script>");
});

it("shows cover-letter and removable-role actions without nesting buttons", () => {
  const html = renderToStaticMarkup(
    <JobList
      jobs={[
        {
          id: "job-1",
          company: "Example Co",
          title: "Data Analyst",
          location: "Austin, TX",
          url: "https://example.test/job-1",
          description:
            "A complete test job description for reporting and analytics.",
          status: "saved",
          notes: "",
          application_date: null,
          folder: null,
          created_at: "2026-09-15T10:00:00Z",
          selected_project_id: null,
          record_source: "posting",
          deleted_at: null,
          deletion_reason: "",
        },
      ]}
      onSelect={() => {}}
      onCoverLetter={() => {}}
      onRemove={() => {}}
    />,
  );
  expect(html).toContain("Cover letter");
  expect(html).toContain("Remove Example Co Data Analyst");
  expect(html).not.toContain("<button><button");
});

const baseJob: Omit<Job, "id" | "title" | "url" | "created_at"> = {
  company: "Example Co",
  location: "Austin, TX",
  description: "A complete test job description for data engineering.",
  status: "saved",
  notes: "",
  application_date: null,
  folder: null,
  selected_project_id: null,
  record_source: "posting",
  deleted_at: null,
  deletion_reason: "",
};

describe("Sponsorship tiers and exclusions", () => {
  it("shows the tier with its reason and lists best odds first", () => {
    const html = renderToStaticMarkup(
      <JobList
        jobs={[
          { ...baseJob, id: "c", title: "Silent Role", url: "https://example.test/c", created_at: "2026-09-17T10:00:00Z", sponsor_tier: "C" },
          { ...baseJob, id: "s", title: "University Role", url: "https://example.test/s", created_at: "2026-09-01T10:00:00Z", sponsor_tier: "S",
            sponsor_evidence: { tier: "S", label: "Cap-exempt: .edu domain" } },
          { ...baseJob, id: "b", title: "History Role", url: "https://example.test/b", created_at: "2026-09-10T10:00:00Z", sponsor_tier: "B" },
        ]}
        onSelect={() => {}}
      />,
    );
    expect(html).toContain("Tier S");
    expect(html).toContain('title="Cap-exempt: .edu domain"');
    expect(html).toContain(`title="${tierTitle.B}"`);
    // S (cap-exempt) before B (proven sponsor) before C (silent), regardless of date.
    const order = ["University Role", "History Role", "Silent Role"].map((t) => html.indexOf(t));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain("All tiers");
  });

  it("treats a job with no tier yet as silent (C), never as excluded", () => {
    const html = renderToStaticMarkup(
      <TierBadge job={{ ...baseJob, id: "x", title: "Role", url: "https://example.test/x", created_at: "2026-09-17T10:00:00Z" }} long />,
    );
    expect(html).toContain("Tier C");
    expect(html).toContain(tierTitle.C);
  });

  it("uses the profile's own country wording for tiers (no H-1B outside the US)", () => {
    const ireland: ProfileEntry = {
      id: "example-person", name: "Example Person", country: "ie", state: "ready", locked: false, initials: "EP",
      market: { code: "ie", name: "Ireland", adjective: "Irish", paper: "A4", timezone: "UTC", default_location: "Ireland",
                tier_labels: { C: "Posting is silent on work permits; still worth applying" } },
    };
    const html = renderToStaticMarkup(
      <ProfileContext.Provider value={{ current: ireland, profiles: [ireland], reload: async () => {} }}>
        <TierBadge job={{ ...baseJob, id: "x", title: "Role", url: "https://example.test/x", created_at: "2026-09-17T10:00:00Z" }} long />
      </ProfileContext.Provider>,
    );
    expect(html).toContain("Posting is silent on work permits; still worth applying");
    expect(html).not.toContain("H-1B");
  });

  it("lists excluded roles with the triggering sentence and a restore action", () => {
    const html = renderToStaticMarkup(
      <ExcludedRoles
        jobs={[
          {
            id: "ex-1",
            company: "Acme Corp",
            title: "Data Engineer",
            location: "Austin, TX",
            url: "https://acme.example/jobs/1",
            reason: "no_sponsorship",
            reason_label: "Says it will not sponsor",
            sentence: "Must be authorized to work in the US without sponsorship now or in the future.",
            source: "discovery",
            excluded_at: "2026-09-18T10:00:00Z",
          },
        ]}
        onRestore={() => {}}
      />,
    );
    expect(html).toContain("Excluded roles · 1");
    expect(html).toContain("without sponsorship now or in the future");
    expect(html).toContain("found by discovery");
    expect(html).toContain('href="https://acme.example/jobs/1"');
    expect(html).toContain("Restore");
    expect(renderToStaticMarkup(<ExcludedRoles jobs={[]} onRestore={() => {}} />)).toBe("");
  });
});

describe("Pipeline labels and fit scores", () => {
  it("shows stage labels instead of raw statuses, and a fit badge with its rationale", () => {
    const html = renderToStaticMarkup(
      <JobList
        jobs={[
          { ...baseJob, id: "f", title: "Pipeline Role", url: "https://example.test/f",
            created_at: "2026-09-17T10:00:00Z", fit_score: 85,
            fit_rationale: "Fit 85/100 — strong overlap" },
          { ...baseJob, id: "p", title: "Drafted Role", url: "https://example.test/p",
            created_at: "2026-09-16T10:00:00Z", status: "prepared" },
        ]}
        onSelect={() => {}}
      />,
    );
    expect(html).toContain("Discovered");
    expect(html).toContain("Tailored");
    expect(html).toContain("Fit 85");
    expect(html).toContain('title="Fit 85/100 — strong overlap"');
    expect(html).not.toContain(">saved<");
    expect(html).not.toContain(">prepared<");
  });
  it("shows no fit badge before a job is scored", () => {
    const html = renderToStaticMarkup(
      <JobList
        jobs={[{ ...baseJob, id: "u", title: "Unscored Role", url: "https://example.test/u", created_at: "2026-09-17T10:00:00Z" }]}
        onSelect={() => {}}
      />,
    );
    expect(html).not.toContain("Fit ");
  });
});

describe("Ask the assistant", () => {
  const prompts = [
    { label: "How is the search going?", text: "How is the Daily Search going right now?" },
    { label: "Run a search from the chat", text: "Run the daily search for 2 jobs", send: false },
  ];
  it("stays hidden where there is no assistant to hand the question to", () => {
    expect(renderToStaticMarkup(<AskAssistant prompts={prompts} />)).toBe("");
  });
  it("offers each question and says whether it asks now or waits in the chat box", () => {
    const html = renderToStaticMarkup(
      <AskContext.Provider value={() => {}}>
        <AskAssistant prompts={prompts} />
      </AskContext.Provider>,
    );
    expect(html).toContain("Ask the assistant");
    expect(html).toContain("How is the search going?");
    expect(html).toContain("Asks now in the chat");
    expect(html).toContain("Opens the chat with this ready to send");
  });
});
