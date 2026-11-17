import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import TraceTimeline, { eventText, spanLabel, spanSummary, timelineRows, type Span } from "./TraceTimeline";

function span(id: string, parent: string | null, name: string, start: number, end: number | null, extra: Partial<Span> = {}): Span {
  return {
    trace_id: "t1", span_id: id, parent_span_id: parent, name, kind: "INTERNAL", start_ns: start, end_ns: end,
    duration_ms: end == null ? null : (end - start) / 1e6, status: "UNSET", status_message: "", attributes: {}, events: [],
    ...extra,
  };
}

const run = span("a", null, "agent.run research", 0, 10_000_000_000, { attributes: { "career.run_kind": "research" } });
const ai = span("b", "a", "chat claude-sonnet-5-5", 1_000_000_000, 6_000_000_000, {
  attributes: {
    "gen_ai.provider.name": "claude_code", "gen_ai.request.model": "claude-sonnet-5-5", "career.ai.action": "role_research",
    "gen_ai.usage.input_tokens": 1200, "gen_ai.usage.output_tokens": 800, "career.ai.web": true,
  },
  events: [{ name: "slot_acquired", time_ns: 1_000_000_000, attributes: { wait_ms: 40 } }],
});
const http = span("c", "a", "GET boards-api.greenhouse.io", 7_000_000_000, 7_500_000_000, {
  status: "ERROR", status_message: "HTTP 404",
  attributes: { "http.response.status_code": 404, "url.full": "https://boards-api.greenhouse.io/v1/boards/acme/jobs" },
});
const orphan = span("d", "missing-parent", "GET api.lever.co", 9_000_000_000, null);

describe("agent run timeline", () => {
  it("places every span under its parent on one time axis", () => {
    const rows = timelineRows([http, orphan, ai, run]);
    expect(rows.map((r) => [r.span.span_id, r.depth, r.kind])).toEqual([
      ["a", 0, "run"], ["b", 1, "ai"], ["c", 1, "http"], ["d", 0, "http"],
    ]);
    expect(rows[1].offset).toBeCloseTo(10);
    expect(rows[1].width).toBeCloseTo(50);
    // A span still running reaches the end of the axis.
    expect(rows[3].offset + rows[3].width).toBeCloseTo(100);
    expect(timelineRows([])).toEqual([]);
  });

  it("names what each span did in plain words", () => {
    expect(spanLabel(run)).toBe("Agent run · research");
    expect(spanLabel(ai)).toBe("AI · claude_code claude-sonnet-5-5 · role research");
    expect(spanSummary(ai)).toBe("1200 in / 800 out tokens · with web search");
    expect(spanSummary(http)).toBe("HTTP 404");
    expect(eventText(ai.events[0])).toBe("Waited 40 ms for a free AI slot");
    expect(eventText({ name: "route.failed", time_ns: 0, attributes: { provider: "kimi_cli", limit: true, reason: "usage limit" } }))
      .toBe("kimi_cli failed (plan limit reached): usage limit");
    expect(eventText({ name: "route.plan", time_ns: 0, attributes: { candidates: ["kimi_cli", "codex"], skipped: [] } }))
      .toBe("Route: kimi_cli → codex");
    expect(eventText({ name: "robots_refused", time_ns: 0, attributes: { host: "example.ie", reason: "robots.txt disallows it" } }))
      .toBe("Not read: robots.txt disallows it (example.ie)");
  });

  it("shows a loading line before the spans arrive", () => {
    const html = renderToStaticMarkup(<TraceTimeline runId="run-1" live={false} />);
    expect(html).toContain("Loading the timeline");
  });

  it("keeps identical span IDs in different traces separate and renders cyclic rows once", () => {
    const repeated = span("a", null, "agent.run research", 11, 20, { trace_id: "t2" });
    const child = span("b", "a", "ai.request", 12, 18, { trace_id: "t2" });
    const rows = timelineRows([run, ai, repeated, child]);
    expect(rows.map((r) => [r.span.trace_id, r.span.span_id, r.depth])).toEqual([
      ["t1", "a", 0], ["t1", "b", 1], ["t2", "a", 0], ["t2", "b", 1],
    ]);
    expect(timelineRows([span("e", "f", "cycle", 0, 5), span("f", "e", "cycle", 1, 4)]))
      .toHaveLength(2);
  });
});
