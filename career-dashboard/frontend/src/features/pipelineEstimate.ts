// Time, token and AI-call estimates for the Daily Search pipeline.
// The first-run numbers and the learned speed of each AI come from the server
// (backend/services/pipeline.py); the arithmetic lives here so flipping a
// switch updates the summary at once.
import type { PipelineChoice, PipelineInfo, PipelineRun, RouteEndpoint } from "../types";

export type EstimateRow = {
  id: string;
  label: string;
  on: boolean;
  required: boolean;
  minutes: number;
  tokens: number;
  calls: number;
  learned: boolean;
};
export type Estimate = {
  jobs: number;
  rows: EstimateRow[];
  minutes: number;
  tokens: number;
  calls: number;
  learned: boolean;
};

export function findKey(info: PipelineInfo, source: string): "find_ai" | "find_pages" {
  return info.sources.find((s) => s.id === source)?.ai === false ? "find_pages" : "find_ai";
}

const speedOf = (info: PipelineInfo, choice: Pick<PipelineChoice, "provider" | "model">, step: string) =>
  info.speeds[`${choice.provider}:${choice.model}`]?.[step] ?? { factor: 1, learned: false, runs: 0 };

/** Minutes one step takes for one job (or, for finding, for the whole search). */
export function stepMinutes(info: PipelineInfo, choice: PipelineChoice, step: string, jobs: number) {
  if (step === "find") {
    const key = findKey(info, choice.source);
    const cost = info.find[key];
    return (cost.minutes + cost.minutes_per_job * jobs) * speedOf(info, choice, key).factor;
  }
  const spec = info.steps.find((s) => s.id === step);
  return spec ? spec.minutes * speedOf(info, choice, step).factor : 0;
}

export function estimatePipeline(choice: PipelineChoice, info: PipelineInfo): Estimate {
  const jobs = Math.max(0, Math.min(choice.count, info.plan.remaining_today));
  const key = findKey(info, choice.source);
  const cost = info.find[key];
  const rows: EstimateRow[] = [
    {
      id: "find",
      label: "Find jobs",
      on: true,
      required: true,
      minutes: stepMinutes(info, choice, "find", jobs),
      tokens: cost.tokens + cost.tokens_per_job * jobs,
      calls: cost.budget_calls,
      learned: speedOf(info, choice, key).learned,
    },
    ...info.steps.map((step) => {
      const speed = speedOf(info, choice, step.id);
      return {
        id: step.id,
        label: step.label,
        on: !!choice.steps[step.id],
        required: false,
        minutes: stepMinutes(info, choice, step.id, jobs) * jobs,
        tokens: (speed.tokens ?? step.tokens) * jobs,
        calls: step.budget_calls * jobs,
        learned: speed.learned || speed.tokens !== undefined,
      };
    }),
  ];
  const on = rows.filter((row) => row.on);
  return {
    jobs,
    rows,
    minutes: on.reduce((sum, row) => sum + row.minutes, 0),
    tokens: on.reduce((sum, row) => sum + row.tokens, 0),
    calls: on.reduce((sum, row) => sum + row.calls, 0),
    learned: on.some((row) => row.learned),
  };
}

export type PlanUseRow = {
  provider: string;
  label: string;
  paid: boolean;
  /** Percent of the plan's 5-hour limit used before and after this search (null: limit unknown). */
  before: number | null;
  after: number | null;
  /** Tokens of this search the plan is expected to take. */
  takes: number;
  resting: string | null;
  resets: string;
  source: string;
};
export type PlanUse = { rows: PlanUseRow[]; leftover: number; over: boolean };

const percent = (tokens: number, capacity: number | null) =>
  capacity ? Math.round((100 * tokens) / capacity) : null;

/**
 * How a search's tokens fall across her plans' 5-hour windows. Under Auto they
 * fill the plans in route order, the way the router moves on when one is used
 * up; with one plan chosen, all of it lands there (``over`` when it will not fit).
 */
export function planUse(route: RouteEndpoint[] | undefined, tokens: number, only?: string): PlanUse {
  let left = Math.max(0, tokens);
  const rows: PlanUseRow[] = [];
  for (const e of route ?? []) {
    if (only ? e.provider !== only : !e.enabled || !e.ready) continue;
    const base = { provider: e.provider, label: e.label, paid: e.paid, resets: e.usage?.resets_text || "",
                   source: e.usage?.capacity_source || "", resting: null as string | null };
    if (e.resting) {
      rows.push({ ...base, before: null, after: null, takes: 0, resting: e.resting.until_text });
      continue;
    }
    if (e.paid) {
      if (e.paid_block) continue;
      rows.push({ ...base, before: null, after: null, takes: left });
      left = 0;
      continue;
    }
    const used = e.usage?.tokens ?? 0;
    const capacity = e.usage?.capacity ?? null;
    const takes = only || !capacity ? left : Math.min(left, Math.max(0, capacity - used));
    rows.push({ ...base, before: percent(used, capacity), after: percent(used + takes, capacity), takes });
    left -= takes;
  }
  const over = rows.some((row) => !row.paid && (row.after ?? 0) > 100) || left > 0;
  return { rows, leftover: left, over };
}

/** Minutes a running search still needs: waiting steps in full, the running one minus its time so far. */
export function minutesLeft(run: PipelineRun, info: PipelineInfo, now = Date.now() / 1000) {
  const choice = run.config;
  const progress = run.progress;
  const left = (full: number, state?: { state: string; started_epoch?: number }) => {
    if (!state || state.state === "waiting") return full;
    if (state.state === "running") return Math.max(full * 0.1, full - (now - (state.started_epoch ?? now)) / 60);
    return 0;
  };
  let total = left(stepMinutes(info, choice, "find", progress.jobs_target), progress.find);
  const enabled = info.steps.filter((s) => choice.steps[s.id]).map((s) => s.id);
  if (progress.find.state !== "done") {
    // Jobs are not known yet: assume the search finds what was asked for.
    total += enabled.reduce((sum, step) => sum + stepMinutes(info, choice, step, 0) * progress.jobs_target, 0);
    return total;
  }
  for (const job of progress.jobs)
    for (const step of Object.keys(job.steps))
      total += left(stepMinutes(info, choice, step, 0), job.steps[step]);
  return total;
}

/** "Step 3 of 11": finding counts as one, then each switched-on helper for each job found. */
export function stepCounts(run: PipelineRun) {
  const jobSteps = run.progress.jobs.flatMap((job) => Object.values(job.steps));
  const all = [run.progress.find, ...jobSteps];
  const finished = all.filter((s) => ["done", "failed", "skipped"].includes(s.state)).length;
  return {
    total: run.progress.find.state === "done" ? all.length : null,
    finished,
    failed: all.filter((s) => s.state === "failed").length,
  };
}

export function formatMinutes(minutes: number) {
  if (minutes < 1) return "under a minute";
  const rounded = Math.round(minutes);
  if (rounded < 60) return `${rounded} min`;
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function formatTokens(tokens: number) {
  if (tokens <= 0) return "no tokens";
  if (tokens < 1000) return `${Math.round(tokens)} tokens`;
  if (tokens < 1_000_000) return `~${Math.round(tokens / 1000)}K tokens`;
  return `~${(tokens / 1_000_000).toFixed(1)}M tokens`;
}

export function clockAfter(minutes: number, from = new Date()) {
  return new Date(from.getTime() + minutes * 60_000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatSeconds(seconds?: number) {
  if (seconds === undefined) return "";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  return formatMinutes(seconds / 60);
}

/** The quick picks: which helpers each one switches on. */
export const QUICK_PICKS: { id: string; label: string; steps: Record<string, boolean> }[] = [
  // The same helpers as the 07:00 morning run: research is left out (three AI calls a job).
  { id: "recommended", label: "Recommended", steps: { research: false, tailor: true, study_plan: true, pdf: true } },
  { id: "all", label: "Everything", steps: { research: true, tailor: true, study_plan: true, pdf: true } },
  { id: "resumes", label: "Just resumes", steps: { research: false, tailor: true, study_plan: false, pdf: true } },
  { id: "find", label: "Just find jobs", steps: { research: false, tailor: false, study_plan: false, pdf: false } },
];
