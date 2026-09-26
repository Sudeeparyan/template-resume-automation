import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DataView, Lines, ProfileEditor } from "./ProfileParts";
import { cleanFields, dateRank, describeChange, describeSync, emptyFields, grouped, statusBadge } from "./profileView";
import { EntryTools } from "./Profile";
import type { ProfileEntry } from "../types";

const entry = (over: Partial<ProfileEntry> = {}): ProfileEntry => ({
  id: "EXP-1",
  kind: "experience",
  title: "Data Engineering Intern",
  summary: "Built pipelines",
  data: {},
  source: "User supplied in Profile",
  revision: 1,
  review_state: "registered",
  deleted: false,
  label: "Data Engineering Intern",
  group: null,
  status: "confirmed",
  usage: "",
  sources: [],
  fields: { title: "Data Engineering Intern", employer: "InsOps Inc.", dates: "Jul 2024 - Present", location: "", bullets: ["Built pipelines"] },
  form: [
    { key: "title", label: "Role title", type: "text", required: true },
    { key: "employer", label: "Employer", type: "text" },
    { key: "bullets", label: "What you did", type: "list" },
  ],
  extra: [],
  in_sync: true,
  ...over,
});

describe("Profile view helpers", () => {
  it("shows an unreviewed edit before the registry status", () => {
    expect(statusBadge({ review_state: "user_updated", status: "confirmed" }).label).toBe("Waiting for your review");
    expect(statusBadge({ review_state: "registered", status: "hold" }).tone).toBe("amber");
    expect(statusBadge({ review_state: "registered", status: null }).label).toBe("Registered");
  });
  it("orders roles by end date, current first", () => {
    expect(dateRank("Jul 2024 - Present")).toBeGreaterThan(dateRank("Jun 2023 - Jun 2024"));
    expect(dateRank("Jun 2023 - Jun 2024")).toBeGreaterThan(dateRank("Jul 2022 - May 2023"));
    expect(dateRank("")).toBe(0);
  });
  it("groups in the given order and keeps unknown groups", () => {
    const groups = grouped(
      [entry({ id: "a", group: "Used it" }), entry({ id: "b", group: "Strong" }), entry({ id: "c", group: "Zeta" })],
      ["Strong", "Used it"],
    );
    expect(groups.map((g) => g.name)).toEqual(["Strong", "Used it", "Zeta"]);
  });
  it("starts new forms empty and drops blank lines on save", () => {
    expect(emptyFields(entry().form)).toEqual({ title: "", employer: "", bullets: [""] });
    expect(cleanFields({ bullets: [" a ", "", "  "], title: "x" })).toEqual({ bullets: ["a"], title: "x" });
  });
  it("reads chat change sets as sentences", () => {
    expect(describeChange({ operation: "remove", id: "SKILL-1" }, { "SKILL-1": "Cloud" })).toEqual({
      action: "Remove",
      name: "Cloud",
      text: "",
    });
    expect(describeChange({ operation: "add", kind: "skill", title: "dbt", summary: "Used" }, {}).action).toBe("Add skill");
  });
  it("says where a save was applied", () => {
    expect(describeSync("Saved.", null)).toBe("Saved.");
    expect(
      describeSync("Saved.", {
        updated: ["Profile settings (profile.yml)", "Evidence registry (evidence.yml)", "Base resume", "Draft resumes"],
        drafts: [
          { job_id: "a", company: "Acme", title: "Data Engineer" },
          { job_id: "b", company: "Beta", title: "Analyst" },
        ],
        revision: "2026-09-23.1",
      }),
    ).toBe(
      "Saved. Also updated: profile settings, evidence registry, base resume and 2 draft resumes (Acme and Beta). Rebuild their PDFs in Resume Studio.",
    );
  });
});

describe("Entry tools", () => {
  it("offers Remove only for entries resumes can do without", () => {
    const tools = (over: Partial<ProfileEntry>) =>
      renderToStaticMarkup(<EntryTools entry={entry(over)} onEdit={() => {}} onRemove={() => {}} />);
    expect(tools({})).toContain("Remove Data Engineering Intern");
    expect(tools({ locked: "Printed on every resume" })).not.toContain("Remove Data Engineering Intern");
    expect(tools({ locked: "Printed on every resume" })).toContain("Edit Data Engineering Intern");
  });
});

describe("Profile rendering", () => {
  it("renders stored data as labelled rows and tags, never as JSON", () => {
    const html = renderToStaticMarkup(
      <DataView value={{ target_roles: { primary: ["Data Engineer", "ML Engineer"], max_years_required: 4 }, remote: true }} />,
    );
    expect(html).toContain("Target roles");
    expect(html).toContain("Max years required");
    expect(html).toContain("Data Engineer");
    expect(html).toContain("Yes");
    expect(html).not.toMatch(/[{}]|&quot;/);
  });
  it("shows short lines as tags and sentences as a list", () => {
    expect(renderToStaticMarkup(<Lines items={["Python", "SQL"]} />)).toContain('class="tag "');
    expect(renderToStaticMarkup(<Lines items={["Developed an end-to-end streaming platform for medical devices"]} />)).toContain("<li>");
  });
  it("builds the edit form from the entry's fields", () => {
    const html = renderToStaticMarkup(
      <ProfileEditor entry={entry()} initialKind="experience" schema={{}} busy={false} onSave={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("Edit Data Engineering Intern");
    expect(html).toContain('value="InsOps Inc."');
    expect(html).toContain("Built pipelines");
    expect(html).toContain("Add line");
    expect(html).not.toContain("changed outside this form");
  });
  it("warns before a form save replaces wording changed elsewhere", () => {
    const html = renderToStaticMarkup(
      <ProfileEditor
        entry={entry({ in_sync: false, summary: "Changed by chat" })}
        initialKind="experience"
        schema={{}}
        busy={false}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("changed outside this form");
    expect(html).toContain("Changed by chat");
  });
  it("offers every kind when adding", () => {
    const schema = { experience: entry().form, skill: [{ key: "title", label: "Skill", type: "text" as const, required: true }] };
    const html = renderToStaticMarkup(
      <ProfileEditor entry={null} initialKind="skill" schema={schema} busy={false} onSave={() => {}} onClose={() => {}} />,
    );
    expect(html).toContain("What are you adding?");
    expect(html).toContain(">Experience</option>");
    expect(html).toContain("Skill (required)");
  });
});
