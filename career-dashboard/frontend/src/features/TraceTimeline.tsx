import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, LoaderCircle } from "lucide-react";
import { api, safeUrl } from "../api";

// --- what the backend sends (GET /v2/agents/runs/<id>/spans) ------------------------

export type SpanEvent = { name: string; time_ns: number; attributes: Record<string, unknown> };
export type Span = {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string | null;
  start_ns: number;
  end_ns: number | null;
  duration_ms: number | null;
  status: string;
  status_message: string;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
};
export type TimelineRow = {
  span: Span;
  depth: number;
  /** Start and length on the run's time axis, in percent. */
  offset: number;
  width: number;
  kind: "run" | "ai" | "http" | "route" | "step";
};

function kindOf(name: string): TimelineRow["kind"] {
  if (name.startsWith("chat ")) return "ai";
  if (/^(GET|POST) /.test(name)) return "http";
  if (name.startsWith("ai.")) return "route";
  if (/^(agent|pipeline|hunt)\.run\b|^assistant\.turn$/.test(name)) return "run";
  return "step";
}

/** The spans as a tree (each one under the step that started it), placed on one time axis. */
export function timelineRows(spans: Span[]): TimelineRow[] {
  if (!spans.length) return [];
  const start = Math.min(...spans.map((s) => s.start_ns));
  const end = Math.max(...spans.map((s) => s.end_ns ?? s.start_ns));
  const total = Math.max(1, end - start);
  const identity = (s: Span) => `${s.trace_id}:${s.span_id}`;
  const ids = new Set(spans.map(identity));
  const children = new Map<string, Span[]>();
  for (const s of spans) {
    const parentId = `${s.trace_id}:${s.parent_span_id}`;
    const parent = s.parent_span_id && ids.has(parentId) ? parentId : "";
    children.set(parent, [...(children.get(parent) || []), s]);
  }
  const rows: TimelineRow[] = [];
  const visited = new Set<string>();
  const append = (s: Span, depth: number) => {
    const id = identity(s);
    if (visited.has(id)) return;
    visited.add(id);
    const finish = s.end_ns ?? end;
    rows.push({ span: s, depth, offset: ((s.start_ns - start) / total) * 100,
      width: Math.min(100 - ((s.start_ns - start) / total) * 100,
        Math.max(0.5, ((finish - s.start_ns) / total) * 100)), kind: kindOf(s.name) });
    walk(id, depth + 1);
  };
  const walk = (parent: string, depth: number) => {
    for (const s of [...(children.get(parent) || [])].sort((a, b) => a.start_ns - b.start_ns)) {
      append(s, depth);
    }
  };
  walk("", 0);
  // Preserve corrupt/cyclic legacy rows without making the viewer recurse forever.
  for (const s of spans) append(s, 0);
  return rows;
}

const text = (value: unknown) => (value == null ? "" : String(value));

export function spanLabel(s: Span): string {
  const a = s.attributes;
  if (s.name.startsWith("chat ")) {
    const who = text(a["career.ai.specialist"]) || text(a["career.ai.action"]);
    return `AI · ${text(a["gen_ai.provider.name"])} ${text(a["gen_ai.request.model"])}${who ? ` · ${who.replaceAll("_", " ")}` : ""}`;
  }
  if (s.name === "ai.route") return "Choosing an AI";
  if (s.name === "ai.request") return `AI request · ${text(a["career.ai.action"]).replaceAll("_", " ") || "step"}`;
  if (s.name.startsWith("agent.run")) return `Agent run · ${text(a["career.run_kind"]).replaceAll("_", " ")}`;
  return s.name;
}

/** The facts worth a glance: who answered, tokens, cache, HTTP status, what failed. */
export function spanSummary(s: Span): string {
  const a = s.attributes;
  const parts: string[] = [];
  if (a["gen_ai.usage.input_tokens"] != null)
    parts.push(`${a["gen_ai.usage.input_tokens"]} in / ${a["gen_ai.usage.output_tokens"] ?? "?"} out tokens`);
  if (a["career.ai.web"] === true) parts.push("with web search");
  if (a["career.ai.paid"] === true) parts.push("paid plan");
  if (a["career.ai.cache_hit"] === true) parts.push("answer reused from the cache");
  if (a["http.response.status_code"] != null) parts.push(`HTTP ${a["http.response.status_code"]}`);
  if (a["error.type"]) parts.push(text(a["error.type"]));
  if (s.status === "ERROR" && s.status_message) parts.push(s.status_message);
  return [...new Set(parts)].join(" · ");
}

export function eventText(e: SpanEvent): string {
  const a = e.attributes;
  switch (e.name) {
    case "slot_acquired":
      return `Waited ${a.wait_ms} ms for a free AI slot`;
    case "route.plan": {
      const order = (a.candidates as string[] | undefined) || [];
      const skipped = (a.skipped as string[] | undefined) || [];
      return `Route: ${order.join(" → ") || "no AI ready"}${skipped.length ? ` (skipped ${skipped.join("; ")})` : ""}`;
    }
    case "route.skip":
      return `Skipped ${text(a.provider)}: ${text(a.reason)}`;
    case "route.failed":
      return `${text(a.provider)} failed${a.limit ? " (plan limit reached)" : ""}: ${text(a.reason)}`;
    case "route.served":
      return `Answered by ${text(a.provider)}${Number(a.after_failures) ? ` after ${a.after_failures} failed` : ""}`;
    case "schema_repair":
      return `The answer had the wrong shape; ${text(a.provider)} was asked once more`;
    case "robots_refused":
      return `Not read: ${text(a.reason)} (${text(a.host)})`;
    case "paced":
      return `Waited ${text(a.seconds)} s before the next request to ${text(a.host)}`;
    case "exception":
      return `${text(a["exception.type"])}: ${text(a["exception.message"])}`;
    default:
      return e.name.replaceAll("_", " ");
  }
}

function ms(value: number): string {
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

/** The run on one time axis: every agent step, AI call and web request, with what each one did. */
export default function TraceTimeline({ runId, live }: { runId: string; live: boolean }) {
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [answer, setAnswer] = useState<{ key: string; text: string } | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    let stopped = false;
    generation.current += 1;
    setSpans(null);
    setError("");
    setOpen(null);
    setAnswer(null);
    const load = () =>
      api<{ spans: Span[] }>(`/v2/agents/runs/${encodeURIComponent(runId)}/spans`)
        .then((data) => {
          if (!stopped) {
            setError("");
            setSpans(data.spans);
          }
        })
        .catch((e) => !stopped && setError((e as Error).message));
    void load();
    const timer = live ? window.setInterval(load, 4000) : undefined;
    return () => {
      stopped = true;
      generation.current += 1;
      if (timer) window.clearInterval(timer);
    };
  }, [runId, live]);

  async function showAnswer(key: string) {
    const requestGeneration = generation.current;
    try {
      const stored = await api<{ result: unknown }>(`/v2/traces/ai-result/${key}`);
      if (requestGeneration === generation.current) setAnswer({ key, text: JSON.stringify(stored.result, null, 2) });
    } catch (e) {
      if (requestGeneration === generation.current) setAnswer({ key, text: (e as Error).message });
    }
  }

  if (error) return <p className="muted small">{error}</p>;
  if (!spans)
    return (
      <p className="muted small">
        <LoaderCircle className="spin" size={14} /> Loading the timeline…
      </p>
    );
  if (!spans.length)
    return <p className="muted small">No timeline was recorded for this run (runs from before tracing show their steps only).</p>;
  return (
    <div className="timeline" role="list">
      <p className="muted small">
        Every step, AI call and web request on one time axis. The timeline never stores prompts or your documents.
      </p>
      {timelineRows(spans).map((row) => {
        const s = row.span;
        const id = `${s.trace_id}:${s.span_id}`;
        const expanded = open === id;
        const summary = spanSummary(s);
        const url = text(s.attributes["url.full"]);
        const key = text(s.attributes["career.ai.cache_key"]);
        return (
          <div key={id} role="listitem" className={`timeline-row kind-${row.kind}${s.status === "ERROR" ? " failed" : ""}`}>
            <button
              type="button"
              className="timeline-label"
              style={{ paddingLeft: 6 + row.depth * 14 }}
              onClick={() => setOpen(expanded ? null : id)}
              aria-expanded={expanded}
            >
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span>{spanLabel(s)}</span>
              <small>{s.duration_ms != null ? ms(s.duration_ms) : "running"}</small>
            </button>
            <div className="timeline-track" aria-hidden="true">
              <span className="timeline-bar" style={{ left: `${row.offset}%`, width: `${row.width}%` }} />
            </div>
            {expanded && (
              <div className="timeline-detail">
                {summary && <p>{summary}</p>}
                {url && (
                  <a href={safeUrl(url)} target="_blank" rel="noreferrer">
                    {url}
                  </a>
                )}
                {s.events.length > 0 && (
                  <ul>
                    {s.events.map((e, i) => (
                      <li key={i}>{eventText(e)}</li>
                    ))}
                  </ul>
                )}
                {key && (
                  <button type="button" className="text-button" onClick={() => void showAnswer(key)}>
                    Show the stored answer (on this computer only)
                  </button>
                )}
                {key && answer?.key === key && <pre className="timeline-answer">{answer.text}</pre>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
