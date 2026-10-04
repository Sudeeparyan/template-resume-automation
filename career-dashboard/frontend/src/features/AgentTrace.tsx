import { useEffect, useRef, useState } from "react";
import {
  Ban,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  FileSearch,
  Globe,
  Info,
  LoaderCircle,
  MessageSquareQuote,
  Search,
  Sparkles,
  Eye,
  X,
  XCircle,
} from "lucide-react";
import { api, safeUrl } from "../api";
import { AGENT_LABEL, active, clock, duration, providerText } from "./agentWords";
import type { ActivityRun } from "./Agents";
import TraceTimeline from "./TraceTimeline";
import GraphSteps from "./GraphSteps";

// --- what the backend sends (GET /v2/agents/runs/<id>/trace) -----------------------

export type TraceEvent = {
  at: string;
  kind: "stage" | "ai_start" | "ai_call" | "search" | "source" | "check" | "note" | "completed" | "failed";
  label: string;
  // ai_start / ai_call
  provider?: string;
  model?: string;
  web?: boolean;
  fresh?: boolean;
  ok?: boolean;
  seconds?: number;
  error?: string;
  routed?: boolean;
  // search / source
  url?: string;
  found?: number | null;
  via?: string;
  // check: one posting's outcome
  outcome?: string;
  stage?: string;
  reason?: string;
  score?: number | null;
  job_id?: string;
  quote?: string;
  // note
  text?: string;
  working?: boolean;
};
export type TraceSource = { label: string; url: string; found?: number | null; error?: string; via?: string };
export type Trace = {
  run: ActivityRun;
  events: TraceEvent[];
  truncated: number;
  sources: TraceSource[];
  checks: Record<string, number>;
  summary: string;
  added_job_ids: string[];
  now: string;
};

// --- the trace as steps, each with what happened inside it ---------------------------

export type TraceItem =
  | { type: "note"; event: TraceEvent }
  | { type: "search"; events: TraceEvent[] }
  | { type: "thinking"; event: TraceEvent }
  | { type: "call"; event: TraceEvent }
  | { type: "sources"; events: TraceEvent[] }
  | { type: "checks"; events: TraceEvent[] };
export type TraceStep = {
  label: string;
  at: string;
  until: string | null;
  state: "done" | "active" | "failed";
  items: TraceItem[];
};

const MERGED = { search: "search", source: "sources", check: "checks" } as const;

/** Group a run's events under the stage they happened in; a run of sites or postings becomes one line. */
export function traceSteps(events: TraceEvent[], runState: string): { steps: TraceStep[]; end?: TraceEvent } {
  const steps: TraceStep[] = [];
  let end: TraceEvent | undefined;
  for (const e of events) {
    if (e.kind === "stage" || e.kind === "completed" || e.kind === "failed") {
      if (steps.length) steps[steps.length - 1].until = e.at;
      if (e.kind === "stage") steps.push({ label: e.label, at: e.at, until: null, state: "done", items: [] });
      else end = e;
      continue;
    }
    if (!steps.length) steps.push({ label: "Starting", at: e.at, until: null, state: "done", items: [] });
    const items = steps[steps.length - 1].items;
    const last = items[items.length - 1];
    if (e.kind === "ai_call") {
      // The line written before the call becomes its answer.
      let open = items.length - 1;
      while (open >= 0 && items[open].type !== "thinking") open--;
      if (open >= 0) items[open] = { type: "call", event: e };
      else items.push({ type: "call", event: e });
    } else if (e.kind === "ai_start") {
      items.push({ type: "thinking", event: e });
    } else if (e.kind === "search" || e.kind === "source" || e.kind === "check") {
      const type = MERGED[e.kind];
      if (last && last.type === type) last.events.push(e);
      else items.push({ type, events: [e] });
    } else {
      items.push({ type: "note", event: e });
    }
  }
  if (steps.length && active(runState)) steps[steps.length - 1].state = "active";
  if (steps.length && runState === "failed") steps[steps.length - 1].state = "failed";
  return { steps, end };
}

// --- words ---------------------------------------------------------------------------

const OUTCOME: Record<string, { label: string; icon: typeof Globe; tone: string; order: number }> = {
  saved: { label: "Saved", icon: CheckCircle2, tone: "done", order: 0 },
  excluded: { label: "Excluded", icon: Ban, tone: "warn", order: 1 },
  rejected: { label: "Turned down", icon: XCircle, tone: "failed", order: 2 },
  duplicate: { label: "Already have", icon: Copy, tone: "idle", order: 3 },
};
const CHECK_STAGE: Record<string, string> = {
  ai: "by the AI while searching",
  posting: "posting could not be verified",
  market: "outside your market",
  sponsorship: "sponsorship",
  reapply: "re-apply rule",
  relevance: "relevance",
  legitimacy: "employer check",
  fit: "fit check",
  save: "save",
  email: "email history",
  history: "an earlier search",
};
const OUTPUT_LABEL: Record<string, string> = {
  research: "Company research",
  hiring: "Hiring-manager view",
  comparison: "Fit with your profile",
  advice: "Resume advice",
  review: "Independent review",
  plan: "Study plan",
};

const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const since = (iso: string, until: string | null, now: number) =>
  Math.max(0, ((until ? Date.parse(until) : now) - Date.parse(iso)) / 1000);
function domain(url?: string) {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : "";
  } catch {
    return "";
  }
}

/** What to call a site in a short list: a feed's employer, a found posting's company, else the page's domain. */
function siteName(s: { label: string; url?: string; via?: string }) {
  if (s.via === "feed") return s.label;
  const company = s.label.split(" — ")[0];
  return company !== s.label ? company : domain(s.url) || s.label;
}

/** A site's first letter on a tint of its own; no favicon is fetched from anyone. */
function SiteMark({ name }: { name: string }) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <span className={"trace-mark tint-" + (hash % 6)} aria-hidden="true">
      {(name.replace(/^www\./, "")[0] || "?").toUpperCase()}
    </span>
  );
}

// --- the panel -------------------------------------------------------------------------

export default function AgentTrace({
  runId,
  runs,
  onClose,
  onJob,
  onSelect,
}: {
  runId: string;
  runs: ActivityRun[];
  onClose: () => void;
  onJob: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const [trace, setTrace] = useState<Trace>();
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const live = !trace || active(trace.run.state);

  // Poll while the run works; a finished run is read once.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    setTrace(undefined);
    setError("");
    const load = async () => {
      try {
        const next = await api<Trace>(`/v2/agents/runs/${encodeURIComponent(runId)}/trace`, "GET", undefined, {
          timeout: 10000,
        });
        if (stopped) return;
        setTrace(next);
        setError("");
        if (active(next.run.state)) timer = window.setTimeout(load, 1500);
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message);
        timer = window.setTimeout(load, 4000);
      }
    };
    void load();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [runId]);

  useEffect(() => {
    if (!live) return;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, [live]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  const others = runs.filter((r) => active(r.state) && r.id !== runId);
  return (
    <>
      <div className="trace-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="trace-panel" aria-label="What the agent is doing">
        <div className="trace-top">
          <div className="eyebrow">AGENT ACTIVITY</div>
          <button type="button" className="icon-button" aria-label="Close the activity panel" onClick={onClose}>
            <X size={19} />
          </button>
        </div>
        {others.length > 0 && (
          <div className="trace-others">
            <span>Also working:</span>
            {others.map((r) => (
              <button key={r.id} type="button" className="trace-other" onClick={() => onSelect(r.id)}>
                {r.state === "queued" ? <span className="trace-queued" /> : <LoaderCircle className="spin" size={12} />}
                {AGENT_LABEL[r.kind] || r.kind}
                {r.company ? ` · ${r.company}` : ""}
              </button>
            ))}
          </div>
        )}
        {trace ? (
          <TraceView trace={trace} now={now} onJob={onJob} />
        ) : error ? (
          <div className="callout warning">{error}</div>
        ) : (
          <p className="muted trace-loading">
            <LoaderCircle className="spin" size={15} /> Loading what this agent did…
          </p>
        )}
        {trace && error && <p className="small tone-warn trace-stale">Reconnecting: {error}</p>}
      </aside>
    </>
  );
}

/** One run's record, drawn from a trace; no fetching, so it can be rendered anywhere. */
export function TraceView({ trace, now, onJob }: { trace: Trace; now: number; onJob: (id: string) => void }) {
  const [tab, setTab] = useState<"steps" | "sources" | "timeline" | "graph">("steps");
  const scroller = useRef<HTMLDivElement>(null);
  const { run, events } = trace;
  const { steps, end } = traceSteps(events, run.state);
  const live = active(run.state);
  const calls = events.filter((e) => e.kind === "ai_call" && e.fresh).length;
  const checked = Object.values(trace.checks).reduce((a, b) => a + b, 0);
  const elapsed = live ? since(run.created_at, null, now) : run.seconds;

  // Keep the newest line in view while the run works, unless the reader has scrolled up.
  useEffect(() => {
    const box = scroller.current;
    if (box && live && box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight;
  }, [events.length, live]);

  const stats = [
    steps.length ? plural(steps.length, "step") : "",
    trace.sources.length ? plural(trace.sources.length, "site") : "",
    checked ? `${plural(checked, "posting")} checked` : "",
    trace.checks.saved ? `${trace.checks.saved} saved` : "",
    calls ? plural(calls, "AI call") : "",
  ].filter(Boolean);

  return (
    <>
      <div className="trace-head">
        <h2>{AGENT_LABEL[run.kind] || run.kind.replaceAll("_", " ")}</h2>
        <p>
          {run.company ? `${run.company} · ${run.title}` : "Whole workspace"}
          {run.provider ? ` · ${providerText(run.provider, run.model)}` : ""}
        </p>
        <div className={"trace-status " + run.state}>
          {live ? (
            <>
              <span className="rail-live" />
              {run.state === "queued" ? "Waiting in line" : "Working"} · {duration(elapsed)}
            </>
          ) : run.state === "completed" ? (
            <>
              <CheckCircle2 size={15} /> Finished in {duration(elapsed)}
            </>
          ) : (
            <>
              <XCircle size={15} /> Stopped after {duration(elapsed)}
            </>
          )}
        </div>
        {stats.length > 0 && <p className="trace-stats">{stats.join(" · ")}</p>}
      </div>

      <div className="segmented trace-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "steps"} className={tab === "steps" ? "selected" : ""} onClick={() => setTab("steps")}>
          Steps
        </button>
        <button role="tab" aria-selected={tab === "sources"} className={tab === "sources" ? "selected" : ""} onClick={() => setTab("sources")}>
          Sources <span>{trace.sources.length}</span>
        </button>
        <button role="tab" aria-selected={tab === "timeline"} className={tab === "timeline" ? "selected" : ""} onClick={() => setTab("timeline")}>
          Timeline
        </button>
        <button role="tab" aria-selected={tab === "graph"} className={tab === "graph" ? "selected" : ""} onClick={() => setTab("graph")}>
          Checkpoints
        </button>
      </div>

      <div className="trace-scroll" ref={scroller}>
        {tab === "timeline" ? (
          <TraceTimeline runId={run.id} live={live} />
        ) : tab === "graph" ? (
          <GraphSteps runId={run.id} live={live} />
        ) : tab === "sources" ? (
          <SourceList sources={trace.sources} />
        ) : (
          <>
            {run.state === "queued" && steps.length === 0 && (
              <p className="muted small">One agent works at a time; this one starts when the one before it finishes.</p>
            )}
            {!live && events.length === 0 && (
              <p className="muted small">
                This run finished before step-by-step recording was added, so only its result is shown.
              </p>
            )}
            <ol className="trace-steps">
              {steps.map((step, i) => (
                <li key={i} className={"trace-step " + step.state}>
                  <span className="trace-node">
                    {step.state === "active" ? (
                      <LoaderCircle className="spin" size={15} />
                    ) : step.state === "failed" ? (
                      <XCircle size={15} />
                    ) : (
                      <CheckCircle2 size={15} />
                    )}
                  </span>
                  <div className="trace-step-body">
                    <div className="trace-step-head">
                      <b>{step.label}</b>
                      <time dateTime={step.at} title={clock(step.at)}>
                        {duration(since(step.at, step.until, now))}
                      </time>
                    </div>
                    {step.items.map((item, j) => (
                      <TraceLine
                        key={j}
                        item={item}
                        kind={run.kind}
                        live={step.state === "active" && j === step.items.length - 1}
                        runLive={live}
                        now={now}
                        onJob={onJob}
                      />
                    ))}
                  </div>
                </li>
              ))}
            </ol>
            {trace.truncated > 0 && (
              <p className="muted small">{trace.truncated} more lines were recorded than this panel shows.</p>
            )}
            {end?.kind === "failed" && (
              <div className="callout warning trace-end">
                <XCircle size={18} />
                <span>{run.error || end.label}</span>
              </div>
            )}
            {run.state === "completed" && <Outcome trace={trace} onJob={onJob} />}
          </>
        )}
      </div>
    </>
  );
}

function TraceLine({
  item,
  kind,
  live,
  runLive,
  now,
  onJob,
}: {
  item: TraceItem;
  kind: string;
  live: boolean;
  runLive: boolean;
  now: number;
  onJob: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  if (item.type === "thinking") {
    const e = item.event;
    const who = providerText(e.provider, e.model, e.routed) || "The AI";
    if (!runLive)
      return (
        <div className="trace-line muted">
          <Brain size={14} /> Asked {who}; no answer was recorded.
        </div>
      );
    return (
      <div className="trace-line trace-thinking" aria-live="polite">
        <Sparkles size={14} />
        <span className="trace-shimmer">
          {who} is {e.web ? "searching the web and reading pages" : "thinking"}
        </span>
        <time>{duration(since(e.at, null, now))}</time>
      </div>
    );
  }
  if (item.type === "call") {
    const e = item.event;
    return (
      <div className={"trace-line" + (e.ok === false ? " bad" : "")}>
        <Brain size={14} />
        <span>
          {e.ok === false
            ? `${providerText(e.provider, e.model, e.routed)} did not finish`
            : e.fresh
              ? `${providerText(e.provider, e.model, e.routed)} answered`
              : "Reused a saved answer (no new AI call)"}
          {e.web && e.fresh ? " · searched the web" : ""}
          {e.seconds != null && e.fresh ? ` · ${duration(e.seconds)}` : ""}
        </span>
        {e.ok === false && e.error && <small className="tone-failed">{e.error}</small>}
      </div>
    );
  }
  if (item.type === "search") {
    const via = item.events[0].via;
    return (
      <div className="trace-line trace-search">
        <Search size={14} />
        <span>{via ? `Searching ${via} for` : "Asked the AI to search for"}</span>
        <span className="trace-pills">
          {item.events.map((e, i) => (
            <span key={i} className="trace-pill">
              {e.label}
            </span>
          ))}
        </span>
      </div>
    );
  }
  if (item.type === "sources") {
    const names = item.events.map((e) => siteName(e));
    const failed = item.events.filter((e) => e.error).length;
    const fromAI = item.events.every((e) => e.via === "ai");
    const n = item.events.length;
    return (
      <div className="trace-group">
        <button type="button" className="trace-line trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          {live ? <LoaderCircle className="spin" size={14} /> : <Globe size={14} />}
          <span>
            {!fromAI
              ? `Read ${plural(n, "site")}${failed ? ` (${failed} could not be read)` : ""}`
              : kind === "discovery"
                ? `Found ${plural(n, "posting")} on the web`
                : `Cites ${plural(n, "page")}`}
          </span>
          <span className="trace-marks">
            {names.slice(0, 4).map((name, i) => (
              <SiteMark key={i} name={name} />
            ))}
          </span>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <small className="trace-sub">
            {names.slice(0, 3).join(", ")}
            {n > 3 ? ` +${n - 3} more` : ""}
          </small>
        </button>
        {open && (
          <ul className="trace-list">
            {item.events.map((e, i) => (
              <li key={i}>
                <SiteMark name={names[i]} />
                <div>
                  {e.url ? (
                    <a href={safeUrl(e.url)} target="_blank" rel="noreferrer">
                      {e.label}
                    </a>
                  ) : (
                    <span>{e.label}</span>
                  )}
                  <small>
                    {[domain(e.url), e.found != null && !e.error ? `${e.found} matched your roles` : "", e.error ? `could not be read: ${e.error}` : ""]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (item.type === "checks") {
    const counts: Record<string, number> = {};
    for (const e of item.events) counts[e.outcome || "other"] = (counts[e.outcome || "other"] || 0) + 1;
    const sorted = [...item.events].sort(
      (a, b) => (OUTCOME[a.outcome || ""]?.order ?? 9) - (OUTCOME[b.outcome || ""]?.order ?? 9),
    );
    return (
      <div className="trace-group">
        <button type="button" className="trace-line trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          {live ? <LoaderCircle className="spin" size={14} /> : <FileSearch size={14} />}
          <span>Checked {plural(item.events.length, "posting")}</span>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="trace-chips trace-sub">
            {Object.entries(counts)
              .sort(([a], [b]) => (OUTCOME[a]?.order ?? 9) - (OUTCOME[b]?.order ?? 9))
              .map(([outcome, n]) => (
                <span key={outcome} className={"trace-chip tone-" + (OUTCOME[outcome]?.tone || "idle")}>
                  {n} {(OUTCOME[outcome]?.label || outcome).toLowerCase()}
                </span>
              ))}
          </span>
        </button>
        {open && (
          <ul className="trace-list">
            {sorted.slice(0, 150).map((e, i) => {
              const o = OUTCOME[e.outcome || ""] || { label: e.outcome || "Checked", icon: Info, tone: "idle" };
              return (
                <li key={i} className="trace-posting">
                  <o.icon size={14} className={"tone-" + o.tone} />
                  <div>
                    {e.url ? (
                      <a href={safeUrl(e.url)} target="_blank" rel="noreferrer">
                        {e.label}
                      </a>
                    ) : (
                      <span>{e.label}</span>
                    )}
                    <small>
                      {o.label}
                      {e.stage && CHECK_STAGE[e.stage] ? ` · ${CHECK_STAGE[e.stage]}` : ""}
                      {e.score != null ? ` · fit ${e.score}/100` : ""}
                      {e.reason ? ` — ${e.reason}` : ""}
                    </small>
                    {e.quote && <q>{e.quote}</q>}
                  </div>
                  {e.job_id && (
                    <button type="button" className="text-button" onClick={() => onJob(e.job_id!)}>
                      Open
                    </button>
                  )}
                </li>
              );
            })}
            {sorted.length > 150 && <li className="muted small">…and {sorted.length - 150} more</li>}
          </ul>
        )}
      </div>
    );
  }
  const e = item.event;
  if (e.text)
    return (
      <div className="trace-group">
        <button type="button" className="trace-line trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          <MessageSquareQuote size={14} />
          <span>{e.label}</span>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {open && <p className="trace-quote">{e.text}</p>}
      </div>
    );
  const sees = /\bSees\b/.test(e.label);
  return (
    <div className={"trace-line" + (sees ? " trace-sees" : "")}>
      {live && e.working ? <LoaderCircle className="spin" size={14} /> : sees ? <Eye size={14} /> : <Info size={14} />}
      <span>{e.label}</span>
    </div>
  );
}

function SourceList({ sources }: { sources: TraceSource[] }) {
  if (!sources.length) return <p className="muted small">No web pages or career feeds yet.</p>;
  const groups = [
    { title: "Read directly by the app (no AI)", items: sources.filter((s) => s.via === "feed") },
    { title: "Found or cited by the AI", items: sources.filter((s) => s.via !== "feed") },
  ].filter((g) => g.items.length);
  return (
    <>
      {groups.map((g) => (
        <section key={g.title} className="trace-sources">
          <div className="rail-label">{g.title}</div>
          <ul className="trace-list">
            {g.items.map((s, i) => {
              const name = siteName(s);
              return (
                <li key={i}>
                  <SiteMark name={name} />
                  <div>
                    {s.url ? (
                      <a href={safeUrl(s.url)} target="_blank" rel="noreferrer">
                        {s.label}
                      </a>
                    ) : (
                      <span>{s.label}</span>
                    )}
                    <small>
                      {[domain(s.url), s.found != null && !s.error ? `${s.found} matched` : "", s.error ? `could not be read: ${s.error}` : ""]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      <p className="muted small trace-footnote">
        The AI's own browsing happens inside its app. The pages it lists here come from its answer; the app checks every
        posting again itself before saving anything.
      </p>
    </>
  );
}

function Outcome({ trace, onJob }: { trace: Trace; onJob: (id: string) => void }) {
  const saved = trace.events.filter((e) => e.kind === "check" && e.outcome === "saved" && e.job_id);
  const outputs = Object.entries(trace.run.outputs);
  if (!saved.length && !outputs.length && !trace.summary) return null;
  return (
    <section className="trace-outcome">
      <div className="rail-label">Result</div>
      {saved.length > 0 && (
        <ul className="trace-saved">
          {saved.map((e) => (
            <li key={e.job_id}>
              <CheckCircle2 size={14} className="tone-done" />
              <span>{e.label}</span>
              {e.score != null && <small>fit {e.score}/100</small>}
              <button type="button" className="text-button" onClick={() => onJob(e.job_id!)}>
                Open
              </button>
            </li>
          ))}
        </ul>
      )}
      {outputs.map(([key, summary]) => (
        <div key={key} className="trace-output">
          <b>{OUTPUT_LABEL[key] || key}</b>
          <p>{summary || "Report saved."}</p>
        </div>
      ))}
      {trace.summary && (
        <details className="trace-notes">
          <summary>Full search notes</summary>
          <p>{trace.summary}</p>
        </details>
      )}
    </section>
  );
}
