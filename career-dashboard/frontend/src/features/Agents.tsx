import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Eye,
  FileCheck2,
  FileText,
  Globe,
  ListOrdered,
  LoaderCircle,
  PanelRight,
  Play,
  Radar,
  RefreshCw,
  ScanSearch,
  Send,
  ShieldCheck,
  UserRoundSearch,
  XCircle,
} from "lucide-react";
import { api } from "../api";
import { useMarket } from "../profiles";
import { Badge, Loading } from "../components/UI";
import type { Summary } from "../types";
import { AGENT_LABEL, active, ago, clock, duration, providerText } from "./agentWords";
import AgentTrace from "./AgentTrace";

export { AGENT_LABEL, PROVIDER_LABEL } from "./agentWords";

// --- what the backend sends ----------------------------------------------------

type TraceEvent = {
  at: string;
  kind: "stage" | "ai_call" | "completed" | "failed";
  label: string;
  provider?: string;
  model?: string;
  action?: string;
  web?: boolean;
  fresh?: boolean;
  ok?: boolean;
  seconds?: number;
  error?: string;
  /** Auto picked this plan for the call. */
  routed?: boolean;
};
export type ActivityRun = {
  id: string;
  kind: string;
  job_id: string | null;
  company: string | null;
  title: string | null;
  state: string;
  stage: string | null;
  error: string | null;
  provider: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  seconds: number | null;
  outputs: Record<string, string>;
  events: TraceEvent[];
};
type Activity = {
  runs: ActivityRun[];
  // daily_call_limit caps paid calls only; free plan calls never count against it.
  budget: {
    daily_call_limit: number;
    calls_today: number;
    remaining_calls: number;
    paid_calls_today?: number;
    free_calls_today?: number;
    can_start?: boolean;
    cache_hits: number;
    cached_results: number;
  };
  calls_today: {
    completed: number;
    failed: number;
    running: number;
    by_provider: Record<string, number>;
  };
  reviews: { pending: number; kept: number; removed: number; tailored_jobs: number };
  ai: { default: { provider: string; model: string } };
};

// --- words shown on the page -----------------------------------------------------

const OUTPUT_LABEL: Record<string, string> = {
  research: "Company research",
  hiring: "Hiring-manager view",
  comparison: "Fit with your profile",
  review: "Independent review",
  plan: "Study plan",
};

type StepState = "done" | "active" | "failed" | "warn" | "idle";
export type { StepState };
export type PipelineStage = {
  label: string;
  icon: typeof Globe;
  does: string;
  state: StepState;
  note: string;
};
type Step = {
  id: string;
  label: string;
  does: string;
  tags: { text: string; tone: string }[];
  icon: typeof Globe;
  run?: string; // the agent kind that can be started from the grid
};
const FLOW: Step[] = [
  {
    id: "posting",
    label: "Posting check",
    does: "Is the job still open?",
    tags: [{ text: "No AI", tone: "neutral" }],
    icon: ScanSearch,
  },
  {
    id: "research",
    label: "Company research",
    does: "Public facts about the employer and role",
    tags: [{ text: "AI + web", tone: "green" }],
    icon: Building2,
    run: "research",
  },
  {
    id: "hiring",
    label: "Hiring-manager view",
    does: "What they will look for",
    tags: [{ text: "Never sees you", tone: "lime" }],
    icon: UserRoundSearch,
    run: "research",
  },
  {
    id: "comparison",
    label: "Fit with your profile",
    does: "Your evidence against the role",
    tags: [{ text: "Uses your profile", tone: "amber" }],
    icon: ShieldCheck,
    run: "research",
  },
  {
    id: "build",
    label: "Resume PDF",
    does: "Contract-sized PDF and match check",
    tags: [{ text: "No AI", tone: "neutral" }],
    icon: FileText,
  },
  {
    id: "review",
    label: "Independent review",
    does: "Reads only the PDF and the job",
    tags: [{ text: "AI", tone: "green" }],
    icon: FileCheck2,
    run: "resume_match",
  },
];

// --- small helpers ---------------------------------------------------------------

function StateIcon({ state, size = 16 }: { state: StepState | string; size?: number }) {
  if (state === "active" || active(state))
    return <LoaderCircle className="spin tone-active" size={size} />;
  if (state === "done" || state === "completed")
    return <CheckCircle2 className="tone-done" size={size} />;
  if (state === "failed") return <XCircle className="tone-failed" size={size} />;
  if (state === "warn") return <AlertTriangle className="tone-warn" size={size} />;
  return <Circle className="tone-idle" size={size} />;
}

/** Where one job stands at each step, from its latest runs. */
function jobSteps(
  job: Summary["jobs"][number],
  latest: (kind: string) => ActivityRun | undefined,
  built: boolean,
): Record<string, { state: StepState; run?: ActivityRun; note: string }> {
  const out: Record<string, { state: StepState; run?: ActivityRun; note: string }> = {};
  out.posting = {
    state:
      job.posting_state === "active"
        ? "done"
        : job.posting_state === "expired"
          ? "failed"
          : job.posting_state === "needs_review"
            ? "warn"
            : "idle",
    note: job.posting_state
      ? `${job.posting_state.replace("_", " ")}${job.last_verified_at ? " · checked " + ago(job.last_verified_at) : ""}`
      : "Not checked yet",
  };
  const research = latest("research");
  const order = ["research", "hiring", "comparison"];
  // The step a research run is on, from what it has produced so far.
  const reached = research
    ? research.state === "completed"
      ? 3
      : order.findIndex((k) => !research.outputs[k])
    : -1;
  order.forEach((step, i) => {
    let state: StepState = "idle";
    if (research) {
      if (i < reached || research.state === "completed") state = "done";
      else if (i === reached)
        state = active(research.state)
          ? "active"
          : research.state === "failed"
            ? "failed"
            : "idle";
    }
    out[step] = {
      state,
      run: research,
      note: !research
        ? "Not run yet"
        : state === "done"
          ? research.outputs[step] || "Done"
          : state === "active"
            ? research.stage || "Working"
            : state === "failed"
              ? research.error || "Failed"
              : "Waiting for the step before",
    };
  });
  for (const [step, kind] of [
    ["review", "resume_match"],
  ]) {
    const run = latest(kind);
    out[step] = {
      state: !run
        ? "idle"
        : run.state === "completed"
          ? "done"
          : active(run.state)
            ? "active"
            : "failed",
      run,
      note: !run
        ? "Not run yet"
        : run.state === "completed"
          ? run.outputs[step] || "Done"
          : run.error || run.stage || "Working",
    };
  }
  const build = latest("resume_build");
  out.build = {
    state: build && active(build.state) ? "active" : built ? "done" : "idle",
    run: build,
    note: built ? "PDF ready" : "Not built yet — open Resume Studio",
  };
  return out;
}

/** The simple view: where the whole pipeline stands, in five plain stages. */
export function pipelineStages(
  jobs: Summary["jobs"],
  reviews: Activity["reviews"],
  builtCount: number,
  discovery?: ActivityRun,
  postings = "Ireland postings",
  resumeShape = "a contract-sized resume",
): PipelineStage[] {
  const total = jobs.length;
  const scored = jobs.filter((j) => typeof j.fit_score === "number").length;
  const submitted = jobs.filter((j) => ["applied", "interview", "offer"].includes(j.status)).length;
  const interviewing = jobs.filter((j) => ["interview", "offer"].includes(j.status)).length;
  const discoveryState: StepState = !discovery
    ? "idle"
    : active(discovery.state)
      ? "active"
      : discovery.state === "completed"
        ? "done"
        : "failed";
  return [
    {
      label: "Discover", icon: Radar, does: `5 latest ${postings}, every day`,
      state: discoveryState,
      note: discovery ? `Last search ${ago(discovery.created_at)} · ${discovery.state}` : "No search yet",
    },
    {
      label: "Rank", icon: ListOrdered, does: "Each job scored against your profile",
      state: total === 0 ? "idle" : scored === total ? "done" : scored > 0 ? "warn" : "idle",
      note: total ? `${scored}/${total} scored` : "No jobs yet",
    },
    {
      label: "Tailor", icon: FileText, does: `${resumeShape.charAt(0).toUpperCase()}${resumeShape.slice(1)} fitted to each company`,
      state: total === 0 || reviews.tailored_jobs === 0 ? "idle" : reviews.tailored_jobs >= total ? "done" : "warn",
      note: `${reviews.tailored_jobs}/${total} tailored · ${builtCount} PDF ready`,
    },
    {
      label: "Guardrail", icon: ShieldCheck, does: "You review predicted projects and skills",
      state: reviews.tailored_jobs === 0 ? "idle" : reviews.pending > 0 ? "warn" : "done",
      note: reviews.tailored_jobs === 0 ? "Nothing tailored yet" : reviews.pending ? `${reviews.pending} items to review` : "All items reviewed",
    },
    {
      label: "Apply", icon: Send, does: "You submit; replies are tracked for you",
      state: submitted ? "done" : "idle",
      note: submitted ? `${submitted} submitted · ${interviewing} in interviews` : "Nothing submitted yet",
    },
  ];
}

// --- the page ----------------------------------------------------------------------

export default function Agents({
  data,
  notify,
  onJob,
  onSettings,
  onAssurance,
}: {
  data: Summary;
  notify: (text: string, error?: boolean) => void;
  onJob: (id: string) => void;
  onSettings: () => void;
  onAssurance: () => void;
}) {
  const market = useMarket();
  const [activity, setActivity] = useState<Activity>();
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "running" | "failed">("all");
  const [starting, setStarting] = useState("");
  const [view, setView] = useState<"pipeline" | "advanced">("pipeline");
  // The run shown in the side panel, and the live runs it was closed on (not reopened for them).
  const [watch, setWatch] = useState<string | null>(null);
  const [closed, setClosed] = useState<string[]>([]);

  const load = useCallback(async () => {
    try {
      setActivity(await api<Activity>("/v2/agents/activity?limit=200"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  const working = activity?.runs.some((r) => active(r.state)) ?? false;
  useEffect(() => {
    void load();
    const timer = window.setInterval(load, working ? 3000 : 10000);
    return () => window.clearInterval(timer);
  }, [load, working]);

  const latest = useMemo(() => {
    const map = new Map<string, ActivityRun>();
    for (const r of activity?.runs || []) {
      const key = r.kind + ":" + (r.job_id || "");
      if (!map.has(key)) map.set(key, r); // runs arrive newest first
    }
    return (kind: string, jobId: string | null) => map.get(kind + ":" + (jobId || ""));
  }, [activity]);

  // A run that starts working opens the side panel, the way a research assistant shows its steps.
  useEffect(() => {
    if (watch || !activity) return;
    const open = (r: ActivityRun) => active(r.state) && !closed.includes(r.id);
    const live = activity.runs.find((r) => r.state === "running" && open(r)) ?? activity.runs.find(open);
    if (live) setWatch(live.id);
  }, [activity, watch, closed]);
  // Closing it holds for every run already working; only a run that starts later opens it again.
  const closeWatch = useCallback(() => {
    const live = (activity?.runs || []).filter((r) => active(r.state)).map((r) => r.id);
    setClosed((ids) => [...ids, ...live, ...(watch ? [watch] : [])]);
    setWatch(null);
  }, [activity, watch]);

  const built = useMemo(
    () =>
      new Set(
        data.documents.filter((d) => d.resumes.length > 0).map((d) => d.job_id),
      ),
    [data.documents],
  );
  const rows = useMemo(
    () =>
      data.jobs.map((job) => ({
        job,
        steps: jobSteps(job, (kind) => latest(kind, job.id), built.has(job.id)),
      })),
    [data.jobs, latest, built],
  );

  if (error && !activity) return <div className="callout warning">{error}</div>;
  if (!activity) return <Loading label="Loading agent activity" />;

  const running = activity.runs.filter((r) => active(r.state));
  const failedToday = activity.calls_today.failed;
  const budget = activity.budget;
  const paidToday = budget.paid_calls_today ?? budget.calls_today;
  const used = Math.min(100, (paidToday / Math.max(1, budget.daily_call_limit)) * 100);
  // Runs stop only when the main AI is a paid one and today's paid calls are used up.
  const cannotStart = budget.can_start === false;
  const discovery = latest("discovery", null);
  // A failure a later run of the same agent on the same job has already fixed.
  const fixed = new Set(
    activity.runs
      .filter((r, i) =>
        r.state === "failed" &&
        activity.runs.slice(0, i).some(
          (n) => n.kind === r.kind && n.job_id === r.job_id && n.state === "completed",
        ),
      )
      .map((r) => r.id),
  );
  const open_failures = activity.runs.filter((r) => r.state === "failed" && !fixed.has(r.id));
  const shown = activity.runs.filter((r) =>
    filter === "running" ? active(r.state) : filter === "failed" ? open_failures.includes(r) : true,
  );

  async function start(kind: string, jobId: string) {
    setStarting(kind + jobId);
    try {
      const r = await api<{ id: string; existing?: boolean }>("/v2/agents/run", "POST", {
        kind,
        job_id: jobId,
      });
      notify(r.existing ? "That agent is already working on this job." : `${AGENT_LABEL[kind]} started.`);
      setOpen(r.id);
      await load();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setStarting("");
    }
  }

  // The simple view: where the whole pipeline stands, in five plain stages.
  const total = data.jobs.length;
  const reviews = activity.reviews;
  const stages = pipelineStages(data.jobs, reviews, built.size, discovery, market.postings, market.resumeShape);

  return (
    <div className={"agents-page" + (watch ? " with-trace" : "")}>
      <div className="agents-main">
      <div className="page-title">
        <div>
          <div className="eyebrow">WHAT THE AGENTS ARE DOING</div>
          <h1>Agents</h1>
          <p>Every job moves through the same steps. Open any run to watch each search, site and decision live, see what it cost, and rerun anything that failed.</p>
        </div>
        <button className="secondary provider-pill" onClick={onSettings} title="Change in Settings">
          <span className="status-dot" />
          Running on {providerText(activity.ai.default.provider, activity.ai.default.model)}
        </button>
      </div>

      <div className="segmented page-switch">
        <button className={view === "pipeline" ? "selected" : ""} onClick={() => setView("pipeline")}>
          Pipeline
        </button>
        <button className={view === "advanced" ? "selected" : ""} onClick={() => setView("advanced")}>
          Advanced
        </button>
      </div>

      {view === "pipeline" ? (
        <>
          {open_failures.length > 0 && (
            <div className="callout warning" role="alert">
              <AlertTriangle size={21} />
              <div>
                <b>{open_failures.length} agent run{open_failures.length === 1 ? "" : "s"} need a retry.</b>
                <p>Nothing is lost — rerun what failed from the Advanced view.</p>
              </div>
              <button
                className="secondary"
                onClick={() => {
                  setView("advanced");
                  setFilter("failed");
                }}
              >
                Review failures
              </button>
            </div>
          )}
          <section className="card spaced">
            <div className="section-title">
              <h2>Your job pipeline</h2>
              <span className="small muted">
                {total} saved {total === 1 ? "job" : "jobs"}
              </span>
            </div>
            <ol className="flow five">
              {stages.map((stage) => (
                <li key={stage.label} className={"flow-node" + (stage.state === "active" ? " live" : "")}>
                  <span className="flow-icon"><stage.icon size={18} /></span>
                  <b>{stage.label}</b>
                  <small>{stage.does}</small>
                  <span className="flow-count">
                    <StateIcon state={stage.state} size={14} /> {stage.note}
                  </span>
                  {stage.label === "Guardrail" && reviews.pending > 0 && (
                    <button className="secondary" onClick={onAssurance}>
                      Review now
                    </button>
                  )}
                  {stage.label === "Discover" && discovery && (
                    <button className="secondary flow-watch" onClick={() => setWatch(discovery.id)}>
                      <Eye size={14} /> {active(discovery.state) ? "Watch live" : "See what it did"}
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </section>
          <section className="card spaced">
            <div className="section-title">
              <h2>What the agents did</h2>
              <span className="small muted">Open a run to see each step, site and decision</span>
            </div>
            {activity.runs.length === 0 ? (
              <p className="muted">No agent has run yet. A job search starts from Daily Search or the morning run.</p>
            ) : (
              <ul className="trace-recent">
                {activity.runs.slice(0, 8).map((r) => (
                  <li key={r.id}>
                    <button
                      className={"trace-recent-row" + (watch === r.id ? " selected" : "")}
                      onClick={() => setWatch(r.id)}
                    >
                      <StateIcon state={fixed.has(r.id) ? "warn" : r.state} size={16} />
                      <span className="trace-recent-name">
                        <b>{AGENT_LABEL[r.kind] || r.kind.replaceAll("_", " ")}</b>
                        <small>{r.company ? `${r.company} · ${r.title}` : "Whole workspace"}</small>
                      </span>
                      <span className={"trace-recent-stage" + (r.state === "failed" && !fixed.has(r.id) ? " tone-failed" : "")}>
                        {active(r.state)
                          ? r.stage || "Waiting to start"
                          : r.state === "failed"
                            ? r.error
                            : r.stage === "Complete"
                              ? "Finished"
                              : r.stage}
                      </span>
                      <small className="trace-recent-when">{ago(r.created_at)}</small>
                      <ChevronRight size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : (
        <>

      <div className="obs-stats">
        <div className="card obs-stat">
          <span className="eyebrow">WORKING NOW</span>
          <strong>{running.length}</strong>
          <small>
            {running.length
              ? `${AGENT_LABEL[running[0].kind] || running[0].kind}${running[0].company ? " · " + running[0].company : ""}`
              : "All agents are idle"}
          </small>
        </div>
        <div className="card obs-stat">
          <span className="eyebrow">PAID AI CALLS TODAY</span>
          <strong>
            {paidToday}
            <small> / {budget.daily_call_limit}</small>
          </strong>
          <div className={"meter" + (used >= 100 ? " full" : used >= 80 ? " high" : "")}>
            <span style={{ width: used + "%" }} />
          </div>
          <small>
            {budget.remaining_calls
              ? `${budget.remaining_calls} paid left today`
              : "Paid limit reached: Azure is skipped until tomorrow"}
            {budget.free_calls_today !== undefined && ` · ${budget.free_calls_today} free on your plans`}
          </small>
        </div>
        <div className="card obs-stat">
          <span className="eyebrow">REUSED RESULTS</span>
          <strong>{budget.cache_hits}</strong>
          <small>Saved answers reused instead of a new call</small>
        </div>
        <div className="card obs-stat">
          <span className="eyebrow">FAILED CALLS TODAY</span>
          <strong className={open_failures.length ? "tone-failed" : ""}>{failedToday}</strong>
          <small>
            {open_failures.length
              ? `${open_failures.length} still need a retry — see Failed below`
              : failedToday
                ? "All since fixed by a later run"
                : "No failures today"}
          </small>
        </div>
      </div>

      <section className="card spaced">
        <div className="section-title">
          <h2>How a job moves through the agents</h2>
          <span className="small muted">
            {data.jobs.length} saved {data.jobs.length === 1 ? "job" : "jobs"}
            {discovery &&
              ` · last job search ${ago(discovery.created_at)} (${discovery.state})`}
          </span>
        </div>
        <ol className="flow">
          <li className="flow-node flow-start">
            <span className="flow-icon"><Radar size={18} /></span>
            <b>Find jobs</b>
            <small>Job search + your manual saves</small>
            <span className="flow-tags"><Badge tone="green">AI + web</Badge></span>
            <span className="flow-count">
              <StateIcon state={discovery ? (active(discovery.state) ? "active" : discovery.state) : "idle"} size={14} />
              {discovery ? `Last search ${discovery.state}` : "No search yet"}
            </span>
          </li>
          {FLOW.map((step) => {
            const states = rows.map((r) => r.steps[step.id]?.state);
            const done = states.filter((s) => s === "done").length;
            const live = states.filter((s) => s === "active").length;
            const bad = states.filter((s) => s === "failed" || s === "warn").length;
            const Icon = step.icon;
            return (
              <li key={step.id} className={"flow-node" + (live ? " live" : "")}>
                <span className="flow-icon"><Icon size={18} /></span>
                <b>{step.label}</b>
                <small>{step.does}</small>
                <span className="flow-tags">
                  {step.tags.map((t) => (
                    <Badge key={t.text} tone={t.tone}>{t.text}</Badge>
                  ))}
                </span>
                <span className="flow-count">
                  {live ? (
                    <><StateIcon state="active" size={14} /> {live} working</>
                  ) : bad ? (
                    <><StateIcon state="failed" size={14} /> {done}/{rows.length} done · {bad} need attention</>
                  ) : (
                    <><StateIcon state={done && done === rows.length ? "done" : "idle"} size={14} /> {done}/{rows.length} done</>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="card spaced">
        <div className="section-title">
          <h2>Each job, step by step</h2>
          <span className="obs-legend small">
            <StateIcon state="done" size={13} /> done <StateIcon state="active" size={13} /> working{" "}
            <StateIcon state="failed" size={13} /> failed <StateIcon state="idle" size={13} /> not yet
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="muted">No saved jobs yet. Run a job search from Daily Search or save a posting.</p>
        ) : (
          <div className="obs-grid-wrap">
            <table className="obs-grid">
              <thead>
                <tr>
                  <th>Job</th>
                  {FLOW.map((s) => (
                    <th key={s.id}>{s.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(({ job, steps }) => (
                  <tr key={job.id}>
                    <th scope="row">
                      <button className="text-button obs-job" onClick={() => onJob(job.id)} title="Open in Resume Studio">
                        <b>{job.company}</b>
                        <small>{job.title}</small>
                      </button>
                    </th>
                    {FLOW.map((s) => {
                      const cell = steps[s.id];
                      // Research, hiring view and fit are one run: start it from its first
                      // step, retry it from whichever step failed. A review needs a PDF.
                      const canRun =
                        !!s.run &&
                        (cell.state === "failed" ||
                          (cell.state === "idle" && (s.run !== "research" || s.id === "research"))) &&
                        !(s.run === "resume_match" && steps.build.state !== "done");
                      return (
                        <td key={s.id} className={"obs-cell " + cell.state} title={cell.note}>
                          <button
                            className="obs-cell-button"
                            disabled={!cell.run}
                            onClick={() => cell.run && setOpen(open === cell.run.id ? null : cell.run.id)}
                          >
                            <StateIcon state={cell.state} />
                            <span>
                              {cell.state === "done"
                                ? "Done"
                                : cell.state === "active"
                                  ? "Working"
                                  : cell.state === "failed"
                                    ? s.id === "posting" ? "Closed" : "Failed"
                                    : cell.state === "warn"
                                      ? "Check"
                                      : "—"}
                            </span>
                          </button>
                          {canRun && (
                            <button
                              className="obs-run"
                              disabled={!!starting || cannotStart}
                              title={
                                cannotStart
                                  ? "Today's paid AI limit is used up and your main AI is a paid one"
                                  : `Start ${AGENT_LABEL[s.run!]} for ${job.company}`
                              }
                              onClick={() => start(s.run!, job.id)}
                            >
                              {starting === s.run! + job.id ? <LoaderCircle className="spin" size={12} /> : cell.state === "failed" ? <RefreshCw size={12} /> : <Play size={12} />}
                              {cell.state === "failed" ? "Retry" : "Run"}
                            </button>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card spaced">
        <div className="section-title">
          <h2>Activity log</h2>
          <div className="segmented">
            {(
              [
                ["all", "All", activity.runs.length],
                ["running", "Working", running.length],
                ["failed", "Needs retry", open_failures.length],
              ] as const
            ).map(([id, label, n]) => (
              <button key={id} className={filter === id ? "selected" : ""} onClick={() => setFilter(id)}>
                {label} <span>{n}</span>
              </button>
            ))}
          </div>
        </div>
        {shown.length === 0 ? (
          <p className="muted">Nothing here.</p>
        ) : (
          <div className="obs-log">
            {shown.slice(0, 80).map((r) => (
              <RunRow
                key={r.id}
                run={r}
                fixed={fixed.has(r.id)}
                open={open === r.id}
                onToggle={() => setOpen(open === r.id ? null : r.id)}
                onRetry={
                  r.state === "failed" && !fixed.has(r.id) && r.job_id && ["research", "resume_match"].includes(r.kind)
                    ? () => start(r.kind, r.job_id!)
                    : undefined
                }
                retryDisabled={!!starting || cannotStart}
                onWatch={() => setWatch(r.id)}
              />
            ))}
          </div>
        )}
      </section>
        </>
      )}
      </div>
      {watch && (
        <AgentTrace runId={watch} runs={activity.runs} onClose={closeWatch} onJob={onJob} onSelect={setWatch} />
      )}
    </div>
  );
}

function RunRow({
  run,
  fixed,
  open,
  onToggle,
  onRetry,
  retryDisabled,
  onWatch,
}: {
  run: ActivityRun;
  fixed: boolean;
  open: boolean;
  onToggle: () => void;
  onRetry?: () => void;
  retryDisabled: boolean;
  onWatch: () => void;
}) {
  const calls = run.events.filter((e) => e.kind === "ai_call");
  const fresh = calls.filter((e) => e.fresh).length;
  const reused = calls.length - fresh;
  return (
    <article className={"obs-run-row " + run.state + (fixed ? " fixed" : "") + (open ? " open" : "")}>
      <button className="obs-run-head" onClick={onToggle} aria-expanded={open}>
        <StateIcon state={fixed ? "warn" : run.state} size={18} />
        <span className="obs-run-name">
          <b>{AGENT_LABEL[run.kind] || run.kind.replaceAll("_", " ")}</b>
          <small>{run.company ? `${run.company} · ${run.title}` : "Whole workspace"}</small>
        </span>
        <span className="obs-run-stage">
          {run.state === "failed" ? (
            <>
              {fixed && <Badge tone="green">Fixed by a later run</Badge>}{" "}
              <span className={fixed ? "" : "tone-failed"}>{run.error}</span>
            </>
          ) : active(run.state) ? (
            run.stage || "Waiting to start"
          ) : (
            run.stage === "Complete" ? "Finished" : run.stage
          )}
        </span>
        <span className="obs-run-meta">
          <span>{providerText(run.provider, run.model) || "No AI"}</span>
          <small>
            {ago(run.created_at)}
            {run.seconds != null && ` · ${active(run.state) ? "running " : ""}${duration(run.seconds)}`}
          </small>
        </span>
        {open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
      </button>
      {open && (
        <div className="obs-run-body">
          {run.events.length ? (
            <>
              <p className="small muted">
                {calls.length
                  ? `${fresh} new AI ${fresh === 1 ? "call" : "calls"}${reused ? `, ${reused} reused from saved results` : ""}.`
                  : "No AI calls in this run."}
              </p>
              <ol className="obs-timeline">
                {run.events.map((e, i) => (
                  <Fragment key={i}>
                    <li className={"ev " + e.kind + (e.kind === "ai_call" && e.ok === false ? " bad" : "")}>
                      <time>{clock(e.at)}</time>
                      <span className="ev-dot" />
                      <div>
                        {e.kind === "stage" && <b>{e.label}</b>}
                        {e.kind === "ai_call" && (
                          <>
                            <span>
                              {e.fresh ? "AI call" : "Reused a saved result"} ·{" "}
                              {providerText(e.provider, e.model, e.routed)}
                              {e.web && e.fresh ? " · with web search" : ""}
                              {" · "}
                              {duration(e.seconds)}
                            </span>
                            {e.ok === false && <small className="tone-failed">{e.error}</small>}
                          </>
                        )}
                        {e.kind === "completed" && <b className="tone-done">Finished</b>}
                        {e.kind === "failed" && (
                          <>
                            <b className="tone-failed">Stopped</b>
                            <small className="tone-failed">{e.label}</small>
                          </>
                        )}
                      </div>
                    </li>
                  </Fragment>
                ))}
              </ol>
            </>
          ) : (
            <p className="small muted">
              Started {clock(run.created_at)}, last update {clock(run.updated_at)}. This run finished
              before step-by-step recording was added, so only its result is shown.
            </p>
          )}
          {Object.keys(run.outputs).length > 0 && (
            <div className="obs-outputs">
              {Object.entries(run.outputs).map(([key, summary]) => (
                <div key={key}>
                  <b>{OUTPUT_LABEL[key] || key}</b>
                  <p>{summary || "Report saved."}</p>
                </div>
              ))}
            </div>
          )}
          {run.state === "failed" && run.error && (
            <div className="callout warning">
              <span>{run.error}</span>
            </div>
          )}
          <div className="actions">
            <button className="secondary" onClick={onWatch}>
              <PanelRight size={15} /> {active(run.state) ? "Watch live" : "Every step, site and decision"}
            </button>
            {onRetry && (
              <button className="secondary" disabled={retryDisabled} onClick={onRetry}>
                <RefreshCw size={15} /> Run again
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
