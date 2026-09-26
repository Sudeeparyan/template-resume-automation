// The Daily Search pipeline: a three-step setup card and a live progress card.
// Written for a student, not a developer: one question per step, plain words,
// and the time, tokens and AI calls update the moment anything changes.
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Building2,
  Check,
  CircleCheck,
  CircleDashed,
  CircleX,
  Clock,
  Coins,
  Gauge,
  Globe,
  KeyRound,
  Laptop,
  LoaderCircle,
  Minus,
  Play,
  Plus,
  Route,
  Rss,
  Shuffle,
  SkipForward,
  Square,
  TriangleAlert,
} from "lucide-react";
import { Switch } from "../components/UI";
import { useMarket } from "../profiles";
import type { PipelineChoice, PipelineInfo, PipelineRun, PipelineStepState } from "../types";
import {
  QUICK_PICKS,
  clockAfter,
  estimatePipeline,
  formatMinutes,
  formatSeconds,
  formatTokens,
  minutesLeft,
  planUse,
  stepCounts,
} from "./pipelineEstimate";
import type { EstimateRow } from "./pipelineEstimate";

const SOURCE_ICON: Record<string, ReactNode> = {
  default: <Globe size={20} />,
  balanced_five: <Shuffle size={20} />,
  portals: <Building2 size={20} />,
  feeds: <Rss size={20} />,
};
const COUNT_CHIPS = [1, 3, 5, 10, 15];
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
// What the "AI calls" number is, in one breath.
const CALLS_EXPLAINED =
  "An AI call is one request to your AI to search the web or write a study plan. Calls on your Kimi, Codex and Claude plans are free and never count; the daily limit only stops paid calls (Azure or an API key), so a search can never run up a bill by accident. Tailoring a resume is not counted.";
const SOURCE_WORD: Record<string, string> = {
  you: "the limit you set",
  learned: "learned when it last ran out",
  guess: "a starting guess",
};

// "1 to find jobs, 15 for Company research and 5 for Study plan"
function callBreakdown(rows: EstimateRow[]): string {
  const parts = rows
    .filter((row) => row.on && row.calls > 0)
    .map((row) => (row.id === "find" ? `${row.calls} to find jobs` : `${row.calls} for ${row.label}`));
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts.join("");
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="pipe-step">
      <span className="pipe-step-num" aria-hidden="true">
        {n}
      </span>
      <div className="pipe-step-body">
        <h3>{title}</h3>
        {children}
      </div>
    </li>
  );
}

function Cost({ row, jobs }: { row: EstimateRow; jobs: number }) {
  const scope = row.id === "find" ? "" : ` for ${plural(jobs, "job")}`;
  return (
    <span className={"helper-cost" + (row.on ? "" : " off")}>
      {!row.on && <span className="helper-cost-save">Off saves</span>}
      <span>
        <Clock size={13} /> {formatMinutes(row.minutes)}
      </span>
      <span>
        <Coins size={13} /> {formatTokens(row.tokens)}
      </span>
      {row.calls > 0 && (
        <span>
          <Gauge size={13} /> {plural(row.calls, "AI call")}
        </span>
      )}
      {scope && <span className="helper-cost-scope">{scope.trim()}</span>}
    </span>
  );
}

export function PipelineBuilder({
  info,
  choice,
  onChoice,
  running,
  starting,
  onStart,
}: {
  info: PipelineInfo;
  choice: PipelineChoice;
  onChoice: (next: PipelineChoice) => void;
  running: boolean;
  starting: boolean;
  onStart: () => void;
}) {
  const market = useMarket();
  const gate = market.dual ? "work-authorization" : market.us ? "visa-sponsorship" : "work-permit";
  const estimate = estimatePipeline(choice, info);
  const provider = info.providers.find((p) => p.id === choice.provider);
  const model = provider?.models.find((m) => m.id === choice.model);
  const source = info.sources.find((s) => s.id === choice.source);
  const remaining = info.plan.remaining_today;
  const needsAI = !!source?.ai || info.steps.some((s) => s.ai && choice.steps[s.id]);
  // The daily limit is on paid calls: it can only stop a search run on a paid AI by name.
  const paidChoice = provider?.kind === "api";
  const overBudget = paidChoice && needsAI && estimate.calls > info.budget.remaining;
  // How much of each plan's 5-hour limit this search takes (Auto: across the route in order).
  const route = info.providers.find((p) => p.kind === "auto")?.route;
  const plans =
    needsAI && provider && provider.kind !== "api"
      ? planUse(route, estimate.tokens, provider.kind === "local" ? provider.id : undefined)
      : null;
  const pick = QUICK_PICKS.find((p) => Object.entries(p.steps).every(([id, on]) => !!choice.steps[id] === on));
  const blocked = running
    ? "A search is running. You can start the next one when it finishes."
    : remaining <= 0
      ? "Today's plan is done, so there is nothing left to search for. Edit your plan on the Dashboard to search for more."
      : needsAI && !provider?.ready
        ? `${provider?.label ?? "The chosen AI"} is not ready on this PC. Choose another AI in Settings.`
        : null;
  const limitWord = !needsAI
    ? "Nothing from your AI limits"
    : provider?.kind === "auto"
      ? "From your free plans first (see below)"
      : provider?.kind === "local"
        ? `From your ${provider.label} plan's limit`
        : provider
          ? `Billed to your ${provider.label} key`
          : "";
  const set = (next: Partial<PipelineChoice>) => onChoice({ ...choice, ...next });
  const setSteps = (steps: Record<string, boolean>) => set({ steps: { ...choice.steps, ...steps } });
  const count = (n: number) => set({ count: Math.max(1, Math.min(info.max_jobs, n)) });
  return (
    <section className="card pipe-card" aria-labelledby="pipe-title">
      <div className="section-title">
        <h2 id="pipe-title">Set up today's search</h2>
        <span className="small muted">Your choices are saved for next time.</span>
      </div>
      {/* The AI is chosen in one place, Settings; the search follows it. */}
      <p className="pipe-ai" role="status">
        <span className="option-icon" aria-hidden="true">
          {provider?.kind === "auto" ? <Route size={18} /> : provider?.kind === "local" ? <Laptop size={18} /> : <KeyRound size={18} />}
        </span>
        <span className="pipe-ai-text">
          <b>
            AI: {provider?.label ?? "none ready"}
            {model && provider && provider.models.length > 1 ? ` · ${model.label}` : ""}
          </b>
          <small>
            {provider && !provider.ready ? "Not ready on this PC. " : provider?.cost ? provider.cost + " " : ""}
            {needsAI ? "" : "This search needs no AI."}
          </small>
        </span>
        <a className="text-button" href="#settings">
          Change in Settings
        </a>
      </p>
      <fieldset className="pipe-fieldset" disabled={running}>
        <ol className="pipe-steps">
          <Step n={1} title="How many jobs do you want?">
            <div className="count-row">
              <div className="stepper">
                <button type="button" aria-label="One fewer job" disabled={choice.count <= 1} onClick={() => count(choice.count - 1)}>
                  <Minus size={18} />
                </button>
                <output aria-live="polite" aria-label="Jobs to find">
                  {choice.count}
                </output>
                <button type="button" aria-label="One more job" disabled={choice.count >= info.max_jobs} onClick={() => count(choice.count + 1)}>
                  <Plus size={18} />
                </button>
              </div>
              <div className="chips" role="group" aria-label="Quick job counts">
                {COUNT_CHIPS.filter((n) => n <= info.max_jobs).map((n) => (
                  <button type="button" key={n} className={"chip" + (n === choice.count ? " selected" : "")} aria-pressed={n === choice.count} onClick={() => count(n)}>
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <p className="step-hint">
              {remaining <= 0 ? (
                <>Today's plan is done. </>
              ) : choice.count > remaining ? (
                <>Your plan has {remaining} left today, so this search stops at {remaining}. </>
              ) : (
                <>Only jobs that pass the {gate} and never-re-apply checks are saved, so you may get fewer. </>
              )}
              {(remaining <= 0 || choice.count > remaining) && (
                <a className="text-button inline" href="#dashboard">
                  Edit plan on the Dashboard
                </a>
              )}
            </p>
          </Step>

          <Step n={2} title="Where should we look?">
            <div className="option-grid" role="radiogroup" aria-label="Where to look">
              {info.sources.map((s) => (
                <button type="button" role="radio" key={s.id} aria-checked={s.id === choice.source} className={"option-card" + (s.id === choice.source ? " selected" : "")} onClick={() => set({ source: s.id })}>
                  <span className="option-icon">{SOURCE_ICON[s.id] ?? <Globe size={20} />}</span>
                  <b>{s.label}</b>
                  <small>{s.what}</small>
                  <span className={"badge " + (s.ai ? "" : "green")}>{s.ai ? "Uses AI" : "Free · no AI"}</span>
                </button>
              ))}
            </div>
          </Step>

          <Step n={3} title="What should run for each job?">
            <div className="quick-picks" role="group" aria-label="Quick picks">
              <span>Quick picks:</span>
              {QUICK_PICKS.map((p) => (
                <button type="button" key={p.id} className={"chip" + (pick?.id === p.id ? " selected" : "")} aria-pressed={pick?.id === p.id} onClick={() => setSteps(p.steps)}>
                  {p.label}
                </button>
              ))}
            </div>
            <ul className="helper-list">
              <li className="helper-row on">
                <div className="helper-text">
                  <b>Find jobs</b>
                  <span>Searches for new postings and checks each one for {gate.replace("-", " ")} and past applications.</span>
                  <Cost row={estimate.rows[0]} jobs={estimate.jobs} />
                </div>
                <span className="always-on">
                  <Check size={14} /> Always on
                </span>
              </li>
              {info.steps.map((step) => {
                const row = estimate.rows.find((r) => r.id === step.id)!;
                return (
                  <li key={step.id} className={"helper-row" + (row.on ? " on" : "")}>
                    <div className="helper-text">
                      <b>{step.label}</b>
                      <span>{step.what}</span>
                      <Cost row={row} jobs={estimate.jobs} />
                      {step.id === "pdf" && !info.tools.pdf && (
                        <span className="helper-warning">
                          <TriangleAlert size={14} /> The PDF compiler (Tectonic) is not installed on this PC, so this step will fail until it is.
                        </span>
                      )}
                    </div>
                    <Switch checked={row.on} label={step.label} onChange={(on) => setSteps({ [step.id]: on })} />
                  </li>
                );
              })}
            </ul>
          </Step>
        </ol>
      </fieldset>

      <div className="pipe-summary">
        <div className="pipe-totals">
          <div className="pipe-total">
            <Clock size={20} />
            <div>
              <strong>About {formatMinutes(estimate.minutes)}</strong>
              <small>Done around {clockAfter(estimate.minutes)}</small>
            </div>
          </div>
          <div className="pipe-total">
            <Coins size={20} />
            <div>
              <strong>{formatTokens(estimate.tokens)}</strong>
              <small>{limitWord}</small>
            </div>
          </div>
          <div className={"pipe-total" + (overBudget ? " warn" : "")}>
            <Gauge size={20} />
            <div>
              <strong>{estimate.calls ? plural(estimate.calls, "AI call") : "No AI calls"}</strong>
              <small>
                {paidChoice
                  ? `Paid calls left today: ${info.budget.remaining} of ${info.budget.limit}`
                  : provider?.kind === "auto"
                    ? `Free on your plans · paid backup: ${info.budget.remaining} calls left today`
                    : "Free on your plan · no daily limit"}
              </small>
            </div>
          </div>
        </div>
        <button type="button" className="primary start-button" disabled={!!blocked || starting} onClick={onStart}>
          <Play size={18} /> {starting ? "Starting…" : "Start search"}
        </button>
      </div>
      {blocked && <p className="pipe-blocked">{blocked}</p>}
      {plans && plans.rows.length > 0 && (
        <div className={"pipe-plan-use" + (plans.over ? " over" : "")}>
          <p className="pipe-plan-title">
            <b>How much of each plan's 5-hour limit this search uses</b>
            <small> · an estimate from {formatTokens(estimate.tokens)}</small>
          </p>
          <ul>
            {plans.rows.map((row) => (
              <li key={row.provider}>
                <span className="pipe-plan-name">{row.label}</span>
                {row.resting ? (
                  <small className="pipe-plan-wide">Resting until {row.resting} after reaching its limit, so it is skipped.</small>
                ) : row.paid ? (
                  <small className="pipe-plan-wide">
                    {row.takes > 0
                      ? `About ${formatTokens(row.takes)} would go here, billed to Azure.`
                      : "Not needed: your free plans cover this search."}
                  </small>
                ) : row.before === null ? (
                  <small className="pipe-plan-wide">{formatTokens(row.takes)} from this plan (its limit is not known yet).</small>
                ) : (
                  <>
                    <span className="plan-meter" aria-hidden="true">
                      <span className="used" style={{ width: Math.min(100, row.before) + "%" }} />
                      <span
                        className="adds"
                        style={{ width: Math.max(0, Math.min(100, row.after ?? 0) - Math.min(100, row.before)) + "%" }}
                      />
                    </span>
                    <small>
                      {row.before}% used now → about {row.after}% after this search
                      {row.takes === 0 ? " (not needed)" : ""}
                      {row.resets ? ` · window resets ${row.resets}` : ""}
                      {row.source ? ` · limit: ${SOURCE_WORD[row.source] ?? row.source}` : ""}
                    </small>
                  </>
                )}
              </li>
            ))}
          </ul>
          {plans.over && (
            <p className="small">
              <TriangleAlert size={13} />{" "}
              {provider?.kind === "local"
                ? `This search may need more than ${provider.label} has left in this window. If it runs out, the remaining steps stop; choose Auto so the next plan takes over.`
                : `About ${formatTokens(plans.leftover)} more than your plans have left. Steps past that wait for a plan to reset, or use Azure if you allow paid calls.`}
            </p>
          )}
        </div>
      )}
      {overBudget && !blocked && (
        <div className="callout warning pipe-budget">
          <div>
            <p>
              <b>
                This search needs {plural(estimate.calls, "AI call")}, but your paid limit has {info.budget.remaining} left today.
              </b>{" "}
              It uses {callBreakdown(estimate.rows)}. Steps past the limit will stop.
            </p>
            <p className="small">
              {CALLS_EXPLAINED} To fit, choose Auto in Settings (or allow more paid calls a day there), switch off Company research (3 calls per job) or pick fewer jobs.
            </p>
          </div>
          <a className="secondary" href="#settings">
            Open Settings
          </a>
        </div>
      )}
      <details className="pipe-breakdown">
        <summary>How is this worked out?</summary>
        <div className="table-scroll">
          <table className="small">
            <thead>
              <tr>
                <th>Step</th>
                <th>Time</th>
                <th>Tokens</th>
                <th>AI calls</th>
              </tr>
            </thead>
            <tbody>
              {estimate.rows
                .filter((row) => row.on)
                .map((row) => (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td>{formatMinutes(row.minutes)}</td>
                    <td>{formatTokens(row.tokens)}</td>
                    <td>{row.calls}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <p className="small muted">
          {estimate.learned
            ? "Times are based on your past searches with this AI. "
            : "These are first-run estimates; they get closer after each search you run with this AI. "}
          Resume tailoring tokens are measured from your past runs once there are some; the other token counts are
          rough, because the local AI apps do not report exact usage. Steps run one after another.
        </p>
        <p className="small muted">
          {CALLS_EXPLAINED} The AI and the paid limit are set in Settings.
        </p>
      </details>
    </section>
  );
}

function StateIcon({ state }: { state: PipelineStepState["state"] }) {
  if (state === "done") return <CircleCheck size={15} aria-label="done" />;
  if (state === "failed") return <CircleX size={15} aria-label="problem" />;
  if (state === "running") return <LoaderCircle size={15} className="spin" aria-label="running" />;
  if (state === "skipped") return <SkipForward size={15} aria-label="skipped" />;
  return <CircleDashed size={15} aria-label="waiting" />;
}

export function PipelineProgress({
  run,
  info,
  onStop,
  onJob,
}: {
  run: PipelineRun;
  info: PipelineInfo;
  onStop: () => void;
  onJob: (id: string) => void;
}) {
  const active = run.state === "queued" || run.state === "running";
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 5000);
    return () => clearInterval(timer);
  }, [active]);
  const counts = stepCounts(run);
  const progress = run.progress;
  const elapsed = Math.max(0, (progress.finished_epoch ?? now) - progress.started_epoch);
  const provider = info.providers.find((p) => p.id === run.config.provider);
  const model = provider?.models.find((m) => m.id === run.config.model);
  const labels: Record<string, string> = Object.fromEntries(info.steps.map((s) => [s.id, s.label]));
  const prepared = progress.jobs.filter((job) => Object.values(job.steps).every((s) => s.state === "done")).length;
  const title = {
    queued: "Starting your search",
    running: "Search in progress",
    completed: "Search finished",
    stopped: "Search stopped",
    failed: "Search stopped by a problem",
  }[run.state];
  const found = progress.jobs.length;
  const outcome =
    run.state === "failed"
      ? "Nothing after the problem ran."
      : `${plural(found, "new job")} found` +
        (found && Object.keys(progress.jobs[0].steps).length ? `, ${prepared} fully prepared` : "") +
        (counts.failed ? `, ${plural(counts.failed, "step")} had a problem` : "");
  const find = progress.find;
  return (
    <section className={"card pipe-progress " + run.state} aria-live="polite">
      <div className="pipe-progress-head">
        <div>
          <div className="eyebrow">{active ? "RUNNING NOW" : "LAST SEARCH"}</div>
          <h2>
            {active && <LoaderCircle size={20} className="spin" />} {title}
          </h2>
          <p className="small">
            {active ? progress.stage : outcome} · {provider?.label ?? run.config.provider}
            {model && provider && provider.models.length > 1 ? ` · ${model.label}` : ""}
          </p>
        </div>
        <div className="pipe-progress-meta">
          {active ? (
            <>
              <strong>About {formatMinutes(minutesLeft(run, info, now))} left</strong>
              <small>
                {counts.total ? `Step ${Math.min(counts.finished + 1, counts.total)} of ${counts.total}` : "Finding jobs first"} ·{" "}
                {formatMinutes(elapsed / 60)} so far
              </small>
              <button type="button" className="secondary" disabled={run.stop_requested} onClick={onStop}>
                <Square size={15} /> {run.stop_requested ? "Stopping after this step…" : "Stop after this step"}
              </button>
            </>
          ) : (
            <>
              <strong>{formatMinutes(elapsed / 60)}</strong>
              <small>total time</small>
            </>
          )}
        </div>
      </div>
      {active && counts.total ? <progress className="pipe-bar" max={counts.total} value={counts.finished} /> : null}
      {run.error && <div className="callout warning">{run.error}</div>}
      <ol className="pipe-timeline">
        <li className={"pipe-find " + find.state}>
          <StateIcon state={find.state} />
          <b>Find jobs</b>
          <span>{find.note || find.error || (find.state === "running" ? "Searching and checking postings…" : "Waiting")}</span>
          <small>{formatSeconds(find.seconds)}</small>
        </li>
        {progress.jobs.map((job) => (
          <li className="pipe-job" key={job.id}>
            <button type="button" className="pipe-job-name" onClick={() => onJob(job.id)}>
              <b>{job.company}</b>
              <small>{job.title}</small>
            </button>
            <div className="pipe-chips">
              {Object.entries(job.steps).map(([id, step]) => (
                <span key={id} className={"pipe-chip " + step.state} title={step.error || step.note || ""}>
                  <StateIcon state={step.state} /> {labels[id] ?? id}
                  {step.seconds ? ` · ${formatSeconds(step.seconds)}` : ""}
                </span>
              ))}
            </div>
            {Object.entries(job.steps)
              .filter(([, step]) => step.state === "failed")
              .map(([id, step]) => (
                <p className="pipe-error" key={id}>
                  {labels[id] ?? id}: {step.error}
                </p>
              ))}
          </li>
        ))}
      </ol>
      {!active && found > 0 && (
        <div className="actions pipe-next">
          {run.config.steps.tailor && (
            <a className="primary" href="#assurance">
              Review suggestions in Assurance
            </a>
          )}
          <a className={run.config.steps.tailor ? "secondary" : "primary"} href="#resumes">
            Open Resume Studio
          </a>
        </div>
      )}
    </section>
  );
}
