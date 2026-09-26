import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PipelineBuilder, PipelineProgress } from "./SearchPipeline";
import {
  estimatePipeline,
  formatMinutes,
  formatTokens,
  minutesLeft,
  planUse,
  stepCounts,
} from "./pipelineEstimate";
import type { PipelineChoice, PipelineInfo, PipelineRun, RouteEndpoint } from "../types";

const speed = (factor: number) => ({ factor, learned: false, runs: 0 });
const info = (over: Partial<PipelineInfo> = {}): PipelineInfo => ({
  sources: [
    { id: "default", label: "Web search", what: "AI search", ai: true },
    { id: "portals", label: "My company list", what: "No AI", ai: false },
  ],
  find: {
    find_ai: { minutes: 5, minutes_per_job: 0.5, tokens: 40000, tokens_per_job: 6000, budget_calls: 1 },
    find_pages: { minutes: 1, minutes_per_job: 0.1, tokens: 0, tokens_per_job: 0, budget_calls: 0 },
  },
  steps: [
    { id: "research", label: "Company research", what: "r", ai: true, web: true, minutes: 4, tokens: 30000, budget_calls: 3 },
    { id: "tailor", label: "Tailor resume", what: "t", ai: true, web: false, minutes: 3, tokens: 22000, budget_calls: 0 },
    { id: "study_plan", label: "Study plan", what: "s", ai: true, web: false, minutes: 1.5, tokens: 8000, budget_calls: 1 },
    { id: "pdf", label: "One-page PDF", what: "p", ai: false, web: false, minutes: 0.5, tokens: 0, budget_calls: 0 },
  ],
  max_jobs: 15,
  providers: [
    {
      id: "claude_code", label: "Claude Code", kind: "local", ready: true, web: true, cost: "Uses your plan's usage limit.", note: null,
      models: [
        { id: "sonnet", label: "Sonnet", hint: "Balanced." },
        { id: "haiku", label: "Haiku", hint: "Fastest." },
      ],
    },
    {
      id: "kimi_cli", label: "Kimi Code", kind: "local", ready: false, web: true, cost: "Uses your plan's usage limit.",
      note: "Kimi Code is not installed on this PC.", models: [{ id: "kimi-runtime", label: "Kimi", hint: "" }],
    },
  ],
  speeds: {
    "claude_code:sonnet": { find_ai: speed(1), find_pages: speed(1), research: speed(1), tailor: speed(1), study_plan: speed(1), pdf: speed(1) },
    "claude_code:haiku": { find_ai: speed(0.5), find_pages: speed(1), research: speed(0.5), tailor: speed(0.5), study_plan: speed(0.5), pdf: speed(1) },
  },
  preferences: choice(),
  tools: { pdf: true },
  budget: { limit: 15, used: 0, remaining: 15 },
  plan: { remaining_today: 5 },
  current: null,
  last: null,
  ...over,
});
function choice(over: Partial<PipelineChoice> = {}): PipelineChoice {
  return {
    count: 5, source: "default", provider: "claude_code", model: "sonnet",
    steps: { research: false, tailor: true, study_plan: true, pdf: true }, ...over,
  };
}
const noop = () => {};

describe("The pipeline estimate", () => {
  it("adds only the switched-on helpers, per job", () => {
    const e = estimatePipeline(choice(), info());
    // find 5 + 0.5*5 = 7.5; tailor 3*5 = 15; study plan 1.5*5 = 7.5; pdf 0.5*5 = 2.5
    expect(e.minutes).toBeCloseTo(32.5);
    expect(e.tokens).toBe(40000 + 6000 * 5 + 22000 * 5 + 8000 * 5);
    expect(e.calls).toBe(1 + 5);
    const research = e.rows.find((r) => r.id === "research")!;
    expect(research.on).toBe(false);
    expect(research.calls).toBe(15); // shown as what switching it on would cost
  });
  it("switching a helper on or off changes the totals at once", () => {
    const on = estimatePipeline(choice({ steps: { research: true, tailor: true, study_plan: true, pdf: true } }), info());
    const off = estimatePipeline(choice({ steps: { research: false, tailor: false, study_plan: false, pdf: false } }), info());
    expect(on.minutes - estimatePipeline(choice(), info()).minutes).toBeCloseTo(20);
    expect(off.tokens).toBe(40000 + 6000 * 5);
  });
  it("uses the chosen model's speed and today's plan as the cap", () => {
    const fast = estimatePipeline(choice({ model: "haiku" }), info());
    expect(fast.minutes).toBeCloseTo(7.5 * 0.5 + 15 * 0.5 + 7.5 * 0.5 + 2.5);
    const capped = estimatePipeline(choice({ count: 10 }), info({ plan: { remaining_today: 3 } }));
    expect(capped.jobs).toBe(3);
  });
  it("uses measured tailor tokens once a run has reported them", () => {
    const measured = info();
    measured.speeds["claude_code:sonnet"].tailor = { factor: 1, learned: false, runs: 0, tokens: 6913 };
    const e = estimatePipeline(choice(), measured);
    const tailor = e.rows.find((r) => r.id === "tailor")!;
    expect(tailor.tokens).toBe(6913 * 5);
    expect(tailor.learned).toBe(true);
  });
  it("finding on her company list uses no AI and no tokens", () => {
    const e = estimatePipeline(choice({ source: "portals", steps: {} }), info());
    expect(e.tokens).toBe(0);
    expect(e.calls).toBe(0);
    expect(e.minutes).toBeCloseTo(1.5);
  });
  it("formats time and tokens in plain words", () => {
    expect(formatMinutes(0.4)).toBe("under a minute");
    expect(formatMinutes(32.5)).toBe("33 min");
    expect(formatMinutes(95)).toBe("1 h 35 min");
    expect(formatTokens(0)).toBe("no tokens");
    expect(formatTokens(220000)).toBe("~220K tokens");
    expect(formatTokens(1_250_000)).toBe("~1.3M tokens");
  });
});

const run = (over: Partial<PipelineRun> = {}): PipelineRun => ({
  id: "p1",
  state: "running",
  config: choice({ count: 2 }),
  progress: {
    stage: "Tailor resume for Acme (job 1 of 2)",
    jobs_target: 2,
    started_epoch: 1000,
    find: { state: "done", seconds: 300, note: "Found 2 new jobs" },
    jobs: [
      { id: "a", company: "Acme", title: "Data Engineer", steps: { tailor: { state: "running", started_epoch: 1300 }, study_plan: { state: "waiting" }, pdf: { state: "waiting" } } },
      { id: "b", company: "Beta", title: "ML Engineer", steps: { tailor: { state: "waiting" }, study_plan: { state: "waiting" }, pdf: { state: "waiting" } } },
    ],
  },
  error: null,
  stop_requested: false,
  created_at: "2026-09-23T15:00:00Z",
  finished_at: null,
  ...over,
});

describe("Progress while a search runs", () => {
  it("counts steps and estimates the time left", () => {
    expect(stepCounts(run())).toEqual({ total: 7, finished: 1, failed: 0 });
    // tailor running 1 min of 3 -> 2 left; then 1.5 + 0.5 for Acme, 3 + 1.5 + 0.5 for Beta
    expect(minutesLeft(run(), info(), 1360)).toBeCloseTo(2 + 2 + 5);
  });
  it("shows each job's helpers, a stop button and time left", () => {
    const html = renderToStaticMarkup(<PipelineProgress run={run()} info={info()} onStop={noop} onJob={noop} />);
    expect(html).toContain("Search in progress");
    expect(html).toContain("Stop after this step");
    expect(html).toContain("Step 2 of 7");
    expect(html).toContain("Acme");
    expect(html).toContain("Tailor resume");
    expect(html).toContain("left");
  });
  it("points to Assurance when a finished run tailored resumes", () => {
    const done = run({
      state: "completed",
      progress: {
        ...run().progress,
        finished_epoch: 2800,
        jobs: run().progress.jobs.map((j) => ({ ...j, steps: { tailor: { state: "done", seconds: 150 } } })),
      },
    });
    const html = renderToStaticMarkup(<PipelineProgress run={done} info={info()} onStop={noop} onJob={noop} />);
    expect(html).toContain("Search finished");
    expect(html).toContain("2 new jobs found, 2 fully prepared");
    expect(html).toContain('href="#assurance"');
    expect(html).not.toContain("Stop after this step");
  });
});

describe("The setup card", () => {
  const render = (i = info(), c = choice()) =>
    renderToStaticMarkup(
      <PipelineBuilder info={i} choice={c} onChoice={noop} running={false} starting={false} onStart={noop} />,
    );
  it("asks three plain questions, names the AI from Settings, and shows the totals before starting", () => {
    const html = render();
    for (const text of ["How many jobs do you want?", "Where should we look?", "What should run for each job?"])
      expect(html).toContain(text);
    // The AI is chosen in Settings only: a status line with a link, never a picker here.
    expect(html).not.toContain("Which AI should do the work?");
    expect(html).not.toContain('aria-label="Which AI"');
    expect(html).toContain("AI: Claude Code · Sonnet");
    expect(html).toContain('href="#settings"');
    expect(html).toContain("About 33 min");
    expect(html).toContain("From your Claude Code plan&#x27;s limit");
    expect(html).toContain("Start search");
    expect(html).toContain('role="switch"');
  });
  it("says when the AI chosen in Settings is not on this PC, and where to change it", () => {
    const html = render(info(), choice({ provider: "kimi_cli", model: "kimi-runtime" }));
    expect(html).toContain("Not ready on this PC.");
    expect(html).toContain("is not ready on this PC. Choose another AI in Settings.");
  });
  it("explains the paid-call limit in plain words and sends a paid AI to Settings to raise it", () => {
    const azure = {
      id: "azure_openai", label: "Azure OpenAI", kind: "api" as const, ready: true, web: true,
      cost: "Pay per use on your Azure account.", note: null, models: [{ id: "gpt-6-luna", label: "gpt-6-luna", hint: "" }],
    };
    const paid = info({ budget: { limit: 3, used: 1, remaining: 2 }, providers: [...info().providers, azure] });
    const onAzure = choice({ provider: "azure_openai", model: "gpt-6-luna" });
    const html = render(paid, onAzure);
    expect(html).toContain("Open Settings");
    expect(html).not.toContain("Allow 7 a day");
    expect(html).toContain("This search needs 6 AI calls, but your paid limit has 2 left today.");
    expect(html).toContain("It uses 1 to find jobs and 5 for Study plan.");
    expect(html).toContain("An AI call is one request to your AI");
    expect(html).toContain("Paid calls left today: 2 of 3");
    // A free plan is never stopped by the paid limit.
    const free = render(paid);
    expect(free).not.toContain("Open Settings");
    expect(free).toContain("Free on your plan · no daily limit");
    const everything = render(paid, choice({ provider: "azure_openai", model: "gpt-6-luna", steps: { research: true, tailor: true, study_plan: true, pdf: true } }));
    expect(everything).toContain("1 to find jobs, 15 for Company research and 5 for Study plan");
    const done = render(info({ plan: { remaining_today: 0 } }));
    expect(done).toContain("Today&#x27;s plan is done");
    expect(done).toContain('href="#dashboard"');
  });
  it("warns on the PDF switch when the PDF compiler is missing", () => {
    expect(render(info({ tools: { pdf: false } }))).toContain("PDF compiler (Tectonic) is not installed");
  });
});

// Auto's route with each plan's 5-hour window, as /api/v2/pipeline sends it.
const endpoint = (provider: string, label: string, used: number, capacity: number | null, over: Partial<RouteEndpoint> = {}): RouteEndpoint => ({
  provider, label, position: 1, enabled: true, ready: true, paid: false,
  models: { strong: "m", cheap: "m" }, capabilities: ["structured", "web"], resting: null,
  last_ok: null, last_error: null, served_today: 0, paid_block: null,
  usage: { tokens: used, calls: 1, capacity, capacity_source: "guess", learned_at: null,
           percent: capacity ? Math.round((100 * used) / capacity) : null, window_resets: null, resets_text: "5:40 PM" },
  ...over,
});
const ROUTE: RouteEndpoint[] = [
  endpoint("kimi_cli", "Kimi Code", 300_000, 1_000_000),
  endpoint("codex", "Codex", 0, 1_000_000),
  endpoint("claude_code", "Claude Code", 0, 500_000),
  { ...endpoint("azure_openai", "Azure OpenAI", 0, null), paid: true, usage: null },
];

describe("How much of each plan's limit a search uses", () => {
  it("fills the plans in route order, the way Auto moves on", () => {
    const use = planUse(ROUTE, 900_000);
    expect(use.rows.map((r) => [r.provider, r.before, r.after, r.takes])).toEqual([
      ["kimi_cli", 30, 100, 700_000],
      ["codex", 0, 20, 200_000],
      ["claude_code", 0, 0, 0],
      ["azure_openai", null, null, 0],
    ]);
    expect(use.over).toBe(false);
  });
  it("skips a resting plan and says what would be billed when the free plans run out", () => {
    const route = [
      endpoint("kimi_cli", "Kimi Code", 0, 1_000_000, { resting: { until: "x", until_text: "5:40 PM", reason: "", kind: "limit" } }),
      endpoint("codex", "Codex", 900_000, 1_000_000),
      { ...endpoint("azure_openai", "Azure OpenAI", 0, null), paid: true, usage: null },
    ];
    const use = planUse(route, 250_000);
    expect(use.rows[0].resting).toBe("5:40 PM");
    expect(use.rows[1].after).toBe(100);
    expect(use.rows[2].takes).toBe(150_000);
  });
  it("with one plan chosen, shows it going past 100% instead of moving on", () => {
    const use = planUse(ROUTE, 900_000, "kimi_cli");
    expect(use.rows).toHaveLength(1);
    expect(use.rows[0].after).toBe(120);
    expect(use.over).toBe(true);
  });
  it("shows the plan meter on the setup card when Auto is chosen", () => {
    const auto = {
      id: "auto", label: "Auto · free plans first", kind: "auto" as const, ready: true, web: true,
      cost: "Uses your plans first.", note: null, models: [{ id: "auto", label: "Auto", hint: "" }], route: ROUTE,
    };
    const html = renderToStaticMarkup(
      <PipelineBuilder
        info={info({ providers: [auto, ...info().providers] })}
        choice={choice({ provider: "auto", model: "auto" })}
        onChoice={noop} running={false} starting={false} onStart={noop}
      />,
    );
    expect(html).toContain("How much of each plan&#x27;s 5-hour limit this search uses");
    expect(html).toContain("30% used now → about 52% after this search");
    expect(html).toContain("Not needed: your free plans cover this search.");
    expect(html).toContain("From your free plans first");
  });
});
