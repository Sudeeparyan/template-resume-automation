import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { assistantOutputEntries, clock, dayLabel, elapsed, exportMarkdown, matches, quickReplies, unfinished, EngineChip, Exchange, HistoryDrawer, MoreMenu, Welcome } from "./Assistant";
import { RichText } from "../components/UI";
import type { ApplicationDocuments, AssistantConversation, AssistantEngine, AssistantMessage } from "../types";

const message = (steps: AssistantMessage["steps"], state: AssistantMessage["state"] = "done"): AssistantMessage => ({
  id: "m1", message: "x", response: "y", state, steps, data: {}, created_at: "", updated_at: "",
});
const step = (agent: string, state: "running" | "done" | "failed", detail = "", run_id?: string) => ({
  at: "", label: agent, state, detail, agent, run_id,
});

describe("An empty chat and the chat bar", () => {
  it("greets by the time of day and offers four starters, one of which only focuses the box", () => {
    const picked: string[] = [];
    const html = renderToStaticMarkup(<Welcome now={new Date(2026, 8, 24, 8)} onPick={(s) => picked.push(s.label)} />);
    expect(html).toContain("Good morning");
    expect(html.match(/class="chat-starter"/g)).toHaveLength(4);
    for (const label of ["Paste a posting", "Find jobs for me", "Build missing resumes", "What&#x27;s waiting on me?"]) expect(html).toContain(label);
    expect(renderToStaticMarkup(<Welcome now={new Date(2026, 8, 24, 20)} onPick={() => {}} />)).toContain("Good evening");
  });
  it("keeps search, download and auto-apply in the ⋯ menu, auto-apply explained there", () => {
    const props = { onOpen() {}, empty: false, searchLabel: "Search (Ctrl+K)", onSearch() {}, onExport() {}, onAutoApply() {} };
    const closed = renderToStaticMarkup(<MoreMenu {...props} open={false} autoApply={false} />);
    expect(closed).toContain('aria-expanded="false"');
    expect(closed).not.toContain("Auto-apply changes");
    const open = renderToStaticMarkup(<MoreMenu {...props} open autoApply />);
    expect(open).toContain("Search this chat");
    expect(open).toContain("Download as a file");
    expect(open).toContain("Auto-apply changes");
    expect(open).toContain('aria-checked="true"');
    expect(open).toContain("run without asking first");
    const empty = renderToStaticMarkup(<MoreMenu {...props} open empty autoApply={false} />);
    expect(empty.match(/role="menuitem" disabled=""/g)).toHaveLength(2);
  });
  it("names the AI in one chip that opens Settings, and warns when nothing is ready", () => {
    const engine = { provider: "auto", model: "auto", label: "Auto · free plans first", ready: true, last_fallback: null } as unknown as AssistantEngine;
    const ready = renderToStaticMarkup(<EngineChip engine={engine} />);
    expect(ready).toContain('href="#settings"');
    expect(ready).toContain("Auto · free plans first");
    expect(ready).not.toContain(" warn");
    const down = renderToStaticMarkup(<EngineChip engine={{ ...engine, provider: "codex", label: "Codex · gpt", ready: false }} />);
    expect(down).toContain("No AI ready");
    expect(down).toContain("chat-chip engine warn");
  });
});

describe("Assistant outputs", () => {
  it("shows the first profile resume and a cover letter even when no job resume exists", () => {
    const letterOnly: ApplicationDocuments[] = [{
      job_id: "job-1", company: "Example Co", title: "Support Specialist", resumes: [],
      cover_letter: { version: 2, path: "applications/job-1/cover-letter-v2.md", created_at: "2026-09-26T10:00:00Z" },
    }];
    expect(assistantOutputEntries([], "base/Example_Person_Resume.pdf")).toEqual([
      { path: "base/Example_Person_Resume.pdf", label: "Base resume" },
    ]);
    expect(assistantOutputEntries(letterOnly, null)).toEqual([
      { path: "applications/job-1/cover-letter-v2.md", label: "Example Co · Cover letter v2" },
    ]);
    expect(assistantOutputEntries(letterOnly, "base/Example_Person_Resume.pdf")).toHaveLength(2);
  });
});

describe("Composer helpers", () => {
  it("offers one-tap answers only for questions that take them", () => {
    expect(quickReplies(null)).toEqual([]);
    expect(quickReplies({ kind: "confirm_tool" }).map((q) => q.send)).toEqual(["yes", "no"]);
    expect(quickReplies({ kind: "confirm_applied" })[0].label).toBe("Yes, go ahead");
    expect(quickReplies({ kind: "posting_link" })).toEqual([]);
    expect(quickReplies({ kind: "agent" })).toEqual([]);
    const pick = quickReplies({ kind: "choose_job", candidates: [{ id: "a", company: "Acme", title: "Data Engineer" }, { id: "b", company: "Beta", title: "Analyst" }] });
    expect(pick).toEqual([{ label: "1. Acme — Data Engineer", send: "1" }, { label: "2. Beta — Analyst", send: "2" }]);
  });
  it("formats how long an exchange took", () => {
    expect(elapsed(0.4)).toBe("0 s");
    expect(elapsed(41.6)).toBe("42 s");
    expect(elapsed(65)).toBe("1 m 05 s");
    expect(elapsed(-3)).toBe("0 s");
    expect(clock("not a date")).toBe("");
    expect(clock("2026-09-19T18:30:24+00:00")).toMatch(/\d/);
  });
});

describe("Thread helpers", () => {
  const today = new Date(2026, 8, 19, 15, 0, 0);
  it("labels days relative to today", () => {
    expect(dayLabel(new Date(2026, 8, 19, 8).toISOString(), today)).toBe("Today");
    expect(dayLabel(new Date(2026, 8, 18, 23, 59).toISOString(), today)).toBe("Yesterday");
    expect(dayLabel(new Date(2026, 8, 15, 12).toISOString(), today)).toMatch(/Sep 15/);
    expect(dayLabel(new Date(2025, 11, 31, 12).toISOString(), today)).toMatch(/2025/);
    expect(dayLabel("nope", today)).toBe("");
  });
  it("finds exchanges that mention every search word, on either side", () => {
    const m = { message: "research Snowflake", response: "Snowflake is a cloud data platform in Bozeman." };
    expect(matches(m, "snowflake")).toBe(true);
    expect(matches(m, "bozeman data")).toBe(true);
    expect(matches(m, "databricks")).toBe(false);
    expect(matches(m, "   ")).toBe(true);
  });
  it("exports the conversation as Markdown with both sides and the PDF path", () => {
    const md = exportMarkdown("Snowflake resume", [
      { ...message([]), message: "paste", response: "Saved **Snowflake — Backend**.", created_at: "2026-09-19T10:00:00Z", updated_at: "2026-09-19T10:00:30Z", data: { pdf: "applications/x/resume.pdf" } },
      { ...message([], "failed"), id: "m2", message: "again", response: "Stopped.", created_at: "2026-09-19T10:01:00Z", updated_at: "2026-09-19T10:01:05Z" },
    ], today);
    expect(md.startsWith("# Snowflake resume\n")).toBe(true);
    expect(md).toContain("2 exchanges");
    expect(md).toContain("**You** ·");
    expect(md).toContain("Saved **Snowflake — Backend**.");
    expect(md).toContain("Resume PDF: applications/x/resume.pdf");
    expect(md).toContain("**Assistant** (failed) ·");
  });
});

describe("Rendering", () => {
  const names = new Map([["sponsorship", "Sponsorship gate"]]);
  const base = { names, expanded: false, onExpand() {}, onSteps() {}, onJob() {}, onSend() {}, onEdit() {}, now: Date.now() };
  it("renders a finished exchange with its tools, card and suggestions", () => {
    const html = renderToStaticMarkup(
      <Exchange
        {...base}
        message={{
          ...message([step("sponsorship", "done", "Tier B")]),
          message: "paste of a posting",
          response: "Saved. Here is `code` and\n```\nraw block\n```",
          created_at: "2026-09-19T10:00:00Z",
          updated_at: "2026-09-19T10:00:30Z",
          data: { intent: "resume_ready", pdf: "a/b.pdf", company: "Acme", title: "Engineer", coverage: 72, ats: 95, suggestions: ["open Acme"], job_id: "j1" },
        }}
      />,
    );
    expect(html).toContain("Copy your message");
    expect(html).toContain("Edit and send again");
    expect(html).toContain("Copy the reply");
    expect(html).toContain("Ask again");
    expect(html).toContain("Download PDF");
    expect(html).toContain("open Acme");
    expect(html).toContain("<pre><code>raw block</code></pre>");
    expect(html).toContain("<code>code</code>");
    expect(html).toContain("1 step in 30 s");
  });
  it("marks a failed action and opens the list of what it did", () => {
    const trace = [
      { tool: "save_posting", summary: "Saved Acme" },
      { tool: "build_resume", summary: "No AI could take this step.", error: true },
    ];
    const html = renderToStaticMarkup(<Exchange {...base} message={{ ...message([]), data: { trace } }} />);
    expect(html).toContain("What I did · 2 actions");
    expect(html).toContain("1 failed");
    expect(html).toMatch(/<details class="chat-trace" open=""/);
    const fine = renderToStaticMarkup(<Exchange {...base} message={{ ...message([]), data: { trace: trace.slice(0, 1) } }} />);
    expect(fine).not.toContain("failed</em>");
    expect(fine).not.toMatch(/<details class="chat-trace" open/);
  });
  it("shows typing dots while working and a stop note when stopping", () => {
    const working = renderToStaticMarkup(<Exchange {...base} message={message([step("assistant", "running", "")], "processing")} stopping />);
    expect(working).toContain("chat-dots");
    expect(working).toContain("Stopping after the current step");
    expect(working).not.toContain("Copy the reply");
    const stopped = renderToStaticMarkup(<Exchange {...base} message={{ ...message([], "failed"), data: { intent: "stopped" } }} />);
    expect(stopped).toContain("Send it again");
    expect(stopped).toContain("Stopped at");
  });
  it("lists earlier chats grouped by day with the open one marked", () => {
    const now = new Date();
    const conversations: AssistantConversation[] = [
      { id: "a", title: "status", count: 3, started_at: now.toISOString(), updated_at: now.toISOString(), busy: false, current: true },
      { id: "b", title: "Software Engineer, Data Platform", count: 1, started_at: "2026-01-02T10:00:00Z", updated_at: "2026-01-02T10:00:00Z", busy: true, current: false },
    ];
    const html = renderToStaticMarkup(<HistoryDrawer conversations={conversations} open onClose={() => {}} onOpen={() => {}} onNew={() => {}} onDelete={() => {}} onClear={() => {}} />);
    expect(html).toContain("Today");
    expect(html).toContain("Clear chat history");
    const empty = renderToStaticMarkup(<HistoryDrawer conversations={[]} open onClose={() => {}} onOpen={() => {}} onNew={() => {}} onDelete={() => {}} onClear={() => {}} />);
    expect(empty).not.toContain("Clear chat history");
    const busy = renderToStaticMarkup(<HistoryDrawer conversations={conversations} open working onClose={() => {}} onOpen={() => {}} onNew={() => {}} onDelete={() => {}} onClear={() => {}} />);
    expect(busy).toMatch(/history-clear"[^>]*disabled/);
    expect(html).toContain("open now");
    expect(html).toContain("Working… · 1 exchange");
    expect(html).toContain('aria-current="true"');
    expect(html).toContain("Delete Software Engineer, Data Platform");
  });
  it("keeps fenced code verbatim and never as markup", () => {
    const html = renderToStaticMarkup(<RichText text={"```\n<b>not bold</b>\n```"} />);
    expect(html).toContain("&lt;b&gt;not bold&lt;/b&gt;");
    expect(html).not.toContain("<b>not bold</b>");
  });
});

describe("suggested answers", () => {
  it("treats a template ending in an ellipsis as text to finish, not a message to send", () => {
    expect(unfinished("Add these roles: …")).toBe("Add these roles: ");
    expect(unfinished("Replace them with these roles: ...")).toBe("Replace them with these roles: ");
    expect(unfinished("yes")).toBeNull();
    expect(unfinished("Keep Cork")).toBeNull();
  });
});
