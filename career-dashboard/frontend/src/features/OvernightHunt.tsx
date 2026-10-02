// The overnight hunt: one goal ("find 10 jobs that fit at 70+ by morning"), and the app keeps
// searching job boards, employer feeds and the web until it is met or the time is up. It
// waits out AI usage limits instead of stopping, then prepares each job it saved.
import { useCallback, useEffect, useState } from "react";
import { CircleCheck, CircleDashed, CircleX, LoaderCircle, Minus, Moon, Play, Plus, SkipForward, Square, Target } from "lucide-react";
import { api } from "../api";
import { Switch } from "../components/UI";
import type { HuntChoice, HuntInfo, HuntPass, HuntRun, HuntStatus } from "../types";

const STEP_LABELS: Record<string, string> = {
  research: "Company research",
  tailor: "Tailored resume",
  study_plan: "Study plan",
  pdf: "Resume PDF",
};
const TARGET_CHIPS = [5, 10, 20, 30];
const HOUR_CHIPS = [2, 4, 6, 8, 10, 12];
const FIT_CHIPS: { value: number; hint: string }[] = [
  { value: 60, hint: "wide" },
  { value: 70, hint: "solid" },
  { value: 80, hint: "close" },
  { value: 90, hint: "near-perfect" },
];
const WHERE: { id: HuntChoice["sources"]; label: string; what: string }[] = [
  { id: "all", label: "Everywhere", what: "Job boards and employer feeds first, then AI web searches" },
  { id: "feeds", label: "Boards + feeds only", what: "No AI searching; AI only checks the requirements" },
  { id: "ai", label: "AI web search only", what: "Focused searches role by role and site by site" },
];
const ACTIVE = ["queued", "running", "waiting"];
const plural = (n: number, word: string, many = word + "s") => `${n} ${n === 1 ? word : many}`;

function hoursLeft(run: HuntRun, now = Date.now() / 1000): string {
  const end = run.progress.deadline_epoch;
  if (!end) return "";
  const left = Math.max(0, end - now);
  const h = Math.floor(left / 3600);
  const m = Math.round((left % 3600) / 60);
  return h ? `${h} h ${m} min left` : `${m} min left`;
}

function PassIcon({ pass }: { pass: HuntPass }) {
  if (pass.state === "done") return <CircleCheck size={15} aria-label="done" />;
  if (pass.state === "failed") return <CircleX size={15} aria-label="failed" />;
  if (pass.state === "skipped" || pass.state === "stopped") return <SkipForward size={15} aria-label={pass.state} />;
  if (pass.state === "running") return <LoaderCircle size={15} className="spin" aria-label="running" />;
  return <CircleDashed size={15} aria-label="waiting" />;
}

function passDetail(pass: HuntPass): string {
  if (pass.error) return pass.error;
  if (pass.note) return pass.note;
  if (pass.state !== "done") return "";
  const parts = [`looked at ${pass.looked ?? 0}`, `saved ${pass.saved ?? 0}`];
  if (pass.held) parts.push(`${pass.held} held for an AI check`);
  if (pass.ai_checked) parts.push(`${pass.ai_checked} checked by AI`);
  return parts.join(" · ");
}

export function HuntSetup({
  info,
  choice,
  onChoice,
  starting,
  onStart,
  blocked,
}: {
  info: HuntInfo;
  choice: HuntChoice;
  onChoice: (next: HuntChoice) => void;
  starting: boolean;
  onStart: () => void;
  blocked?: string | null;
}) {
  const set = (next: Partial<HuntChoice>) => onChoice({ ...choice, ...next });
  const target = (n: number) => set({ target: Math.max(1, Math.min(info.limits.max_target, n)) });
  // When the plan cannot be built the server sends only { error, strategies }; the controls still show.
  const plan = { ...info.plan, roles: info.plan.roles ?? [], related_titles: info.plan.related_titles ?? [] };
  const aiPasses = plan.strategies.filter((s) => s.kind === "ai").length;
  const feedPasses = plan.strategies.filter((s) => s.kind === "feeds").length;
  const remembered = Object.values(info.memory.outcomes).reduce((sum, n) => sum + n, 0);
  const noRoles = !plan.roles.length;
  return (
    <section className="card pipe-card hunt-card" aria-labelledby="hunt-title">
      <div className="section-title">
        <h2 id="hunt-title">
          <Moon size={18} /> Overnight hunt
        </h2>
        <span className="small muted">Keeps searching until your goal is met, then prepares each job.</span>
      </div>
      <p className="step-hint">
        Leave the app open and go to sleep. It reads job boards and employer career feeds first (no AI), then runs
        focused AI searches for each role, checks every job's requirements against your profile, and waits for your AI
        plans to reset when they hit their limits. Nothing is ever submitted. To have it run every night by itself,
        with the list ready when you wake up, switch on <b>Morning jobs</b> in Settings → This profile; it uses the
        choices below.
      </p>
      <ol className="pipe-steps">
        <li className="pipe-step">
          <span className="pipe-step-num" aria-hidden="true">1</span>
          <div className="pipe-step-body">
            <h3>How many good jobs do you want by morning?</h3>
            <div className="count-row">
              <div className="stepper">
                <button type="button" aria-label="One fewer job" disabled={choice.target <= 1} onClick={() => target(choice.target - 1)}>
                  <Minus size={18} />
                </button>
                <output aria-live="polite" aria-label="Jobs to find">{choice.target}</output>
                <button type="button" aria-label="One more job" disabled={choice.target >= info.limits.max_target} onClick={() => target(choice.target + 1)}>
                  <Plus size={18} />
                </button>
              </div>
              <div className="chips" role="group" aria-label="Quick job counts">
                {TARGET_CHIPS.map((n) => (
                  <button type="button" key={n} className={"chip" + (n === choice.target ? " selected" : "")} aria-pressed={n === choice.target} onClick={() => target(n)}>
                    {n}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </li>
        <li className="pipe-step">
          <span className="pipe-step-num" aria-hidden="true">2</span>
          <div className="pipe-step-body">
            <h3>How close a match, and for how long?</h3>
            <div className="chips" role="group" aria-label="Fit bar">
              {FIT_CHIPS.map((f) => (
                <button type="button" key={f.value} className={"chip" + (f.value === choice.min_fit ? " selected" : "")} aria-pressed={f.value === choice.min_fit} onClick={() => set({ min_fit: f.value })}>
                  Fit {f.value}+ <small>{f.hint}</small>
                </button>
              ))}
            </div>
            <div className="chips" role="group" aria-label="Hours">
              {HOUR_CHIPS.map((h) => (
                <button type="button" key={h} className={"chip" + (h === choice.hours ? " selected" : "")} aria-pressed={h === choice.hours} onClick={() => set({ hours: h })}>
                  {h} h
                </button>
              ))}
            </div>
            <p className="step-hint">
              A job is saved only when its requirements check scores at least {choice.min_fit}; a higher bar means fewer, closer matches.
            </p>
          </div>
        </li>
        <li className="pipe-step">
          <span className="pipe-step-num" aria-hidden="true">3</span>
          <div className="pipe-step-body">
            <h3>Where to look</h3>
            <div className="option-grid" role="radiogroup" aria-label="Where the hunt looks">
              {WHERE.map((w) => (
                <button type="button" role="radio" key={w.id} aria-checked={w.id === choice.sources} className={"option-card" + (w.id === choice.sources ? " selected" : "")} onClick={() => set({ sources: w.id })}>
                  <b>{w.label}</b>
                  <small>{w.what}</small>
                </button>
              ))}
            </div>
            <details className="hunt-plan">
              <summary className="small">
                The plan: {plan.roles.length ? plan.roles.join(", ") : "no target roles yet"}
                {plan.related_titles.length ? ` + ${plural(plan.related_titles.length, "related title")}` : ""} ·{" "}
                {plural(feedPasses, "feed")} and {plural(aiPasses, "AI pass", "AI passes")} per cycle
              </summary>
              {plan.error && <p className="small">The search plan could not be read: {plan.error}</p>}
              {!!plan.related_titles.length && (
                <p className="small">Also matches: {plan.related_titles.join(", ")}.</p>
              )}
              <ul className="small">
                {plan.strategies.map((s) => (
                  <li key={s.id}>
                    {s.kind === "feeds" ? "No AI · " : "AI · "}
                    {s.label}
                    {s.queries?.length ? <span className="muted"> — {s.queries.join(" · ")}</span> : null}
                  </li>
                ))}
              </ul>
              <p className="small muted">Change the titles from the Assistant, e.g. “also search for insights analyst roles”.</p>
            </details>
          </div>
        </li>
        <li className="pipe-step">
          <span className="pipe-step-num" aria-hidden="true">4</span>
          <div className="pipe-step-body">
            <h3>What to prepare for each job</h3>
            <ul className="helper-list">
              {Object.keys(STEP_LABELS).map((step) => (
                <li key={step} className={"helper-row" + (choice.steps[step] ? " on" : "")}>
                  <div className="helper-text">
                    <b>{STEP_LABELS[step]}</b>
                  </div>
                  <Switch checked={!!choice.steps[step]} label={STEP_LABELS[step]} onChange={(on) => set({ steps: { ...choice.steps, [step]: on } })} />
                </li>
              ))}
              <li className={"helper-row" + (choice.allow_paid ? " on" : "")}>
                <div className="helper-text">
                  <b>Use a paid AI when every free plan is resting</b>
                  <span>Off: the hunt waits for Kimi, Codex or Claude to reset instead of billing Azure or an API key.</span>
                </div>
                <Switch checked={choice.allow_paid} label="Allow a paid AI" onChange={(on) => set({ allow_paid: on })} />
              </li>
            </ul>
          </div>
        </li>
      </ol>
      <div className="pipe-summary">
        <div className="pipe-totals">
          <div className="pipe-total">
            <Target size={20} />
            <div>
              <strong>
                {choice.target} jobs at fit {choice.min_fit}+ within {choice.hours} h
              </strong>
              <small>
                {info.ai.ready
                  ? "A free AI plan is ready now."
                  : info.ai.wakes_text
                    ? `Every free plan is resting; it starts with the no-AI sources and waits until ${info.ai.wakes_text}.`
                    : info.ai.note || "No AI plan is ready; only the no-AI sources can run."}
              </small>
            </div>
          </div>
          {remembered > 0 && (
            <div className="pipe-total">
              <CircleCheck size={20} />
              <div>
                <strong>{plural(remembered, "posting")} already checked</strong>
                <small>
                  Skipped next time{info.memory.held ? ` · ${info.memory.held} waiting for an AI check` : ""}
                </small>
              </div>
            </div>
          )}
        </div>
        <button type="button" className="primary start-button" disabled={!!blocked || starting || noRoles} onClick={onStart}>
          <Play size={18} /> {starting ? "Starting…" : "Start the hunt"}
        </button>
      </div>
      {(blocked || noRoles) && (
        <p className="pipe-blocked">{blocked || "Add your target roles in Profile (or tell the Assistant) before hunting."}</p>
      )}
    </section>
  );
}

export function HuntProgress({ run, onStop, onJob }: { run: HuntRun; onStop: () => void; onJob: (id: string) => void }) {
  const active = ACTIVE.includes(run.state);
  const saved = run.progress.saved || [];
  const target = run.config.target;
  const percent = Math.min(100, Math.round((100 * saved.length) / Math.max(1, target)));
  const passes = run.progress.passes || [];
  const prepare = run.progress.prepare;
  return (
    <section className="card pipe-progress hunt-progress" aria-labelledby="hunt-progress-title" aria-live="polite">
      <div className="section-title">
        <h2 id="hunt-progress-title">
          <Moon size={18} /> {active ? "Overnight hunt running" : `Last overnight hunt: ${run.state}`}
        </h2>
        {active && (
          <button type="button" className="secondary" onClick={onStop} disabled={run.stop_requested}>
            <Square size={15} /> {run.stop_requested ? "Stopping at the next step…" : "Stop the hunt"}
          </button>
        )}
      </div>
      <p className="hunt-stage">
        <b>
          {saved.length} of {target} saved
        </b>{" "}
        at fit {run.config.min_fit}+ · cycle {run.progress.cycle || 1}
        {active && run.progress.deadline_epoch ? ` · ${hoursLeft(run)}` : ""}
      </p>
      <progress className="pipe-bar" max={Math.max(1, target)} value={saved.length} aria-label={`Jobs saved: ${percent}%`} />
      {run.progress.stage && <p className="small">{run.progress.stage}</p>}
      {run.progress.waiting && (
        <p className="hunt-waiting" role="status">
          <Moon size={16} /> Waiting until {run.progress.waiting.until_text}: {run.progress.waiting.why}.
        </p>
      )}
      {run.error && <p className="pipe-blocked">{run.error}</p>}
      {!!saved.length && (
        <ul className="hunt-saved">
          {saved.map((job) => {
            const steps = prepare?.jobs.find((j) => j.id === job.id)?.steps || {};
            const done = Object.entries(steps).filter(([, s]) => s.state === "done").map(([id]) => STEP_LABELS[id] ?? id);
            return (
              <li key={job.id}>
                <button type="button" className="history-job" onClick={() => onJob(job.id)}>
                  <span>
                    <b>{job.company}</b> · {job.title}
                  </span>
                  {job.fit != null && <span className="badge green">fit {job.fit}</span>}
                </button>
                <small className="muted">
                  {job.location ? job.location + " · " : ""}found by {job.pass}
                  {done.length ? ` · ready: ${done.join(", ")}` : ""}
                </small>
              </li>
            );
          })}
        </ul>
      )}
      {!!passes.length && (
        <details className="hunt-passes" open={active}>
          <summary className="small">Where it looked · {plural(passes.length, "pass", "passes")}</summary>
          <ul>
            {passes.slice(-30).map((pass, i) => (
              <li key={pass.id + i} className="hunt-pass">
                <PassIcon pass={pass} /> <span>{pass.label}</span> <small className="muted">{passDetail(pass)}</small>
              </li>
            ))}
          </ul>
        </details>
      )}
      {run.progress.report && !active && (
        <p className="small">
          Report: <code>{run.progress.report}</code>
        </p>
      )}
    </section>
  );
}

export default function OvernightHunt({
  notify,
  onJob,
  refresh,
  pipelineActive,
}: {
  notify: (s: string, e?: boolean) => void;
  onJob: (id: string) => void;
  refresh: () => Promise<void>;
  pipelineActive: boolean;
}) {
  const [info, setInfo] = useState<HuntInfo | null>(null);
  const [choice, setChoice] = useState<HuntChoice | null>(null);
  const [starting, setStarting] = useState(false);
  const load = useCallback(
    () =>
      api<HuntInfo>("/v2/hunt")
        .then((loaded) => {
          setInfo(loaded);
          setChoice((was) => was ?? loaded.defaults);
        })
        .catch((e) => notify((e as Error).message, true)),
    [notify],
  );
  const poll = useCallback(
    () =>
      api<HuntStatus>("/v2/hunt/status", "GET", undefined, { timeout: 15000 })
        .then((status) => setInfo((was) => (was ? { ...was, ...status } : was)))
        .catch(() => {}),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);
  const active = !!info?.current;
  useEffect(() => {
    const timer = setInterval(poll, active ? 5000 : 30000);
    return () => clearInterval(timer);
  }, [active, poll]);
  const saved = info?.current?.progress.saved.length ?? 0;
  useEffect(() => {
    if (saved) refresh().catch(() => {});
  }, [saved, refresh]);
  const start = async () => {
    if (!choice) return;
    setStarting(true);
    try {
      const { target, hours, min_fit, sources, allow_paid, require_ai_fit, steps } = choice;
      const run = await api<HuntRun>("/v2/hunt/run", "POST", { target, hours, min_fit, sources, allow_paid, require_ai_fit, steps });
      setInfo((was) => (was ? { ...was, current: run } : was));
      notify("The hunt started. Keep the app open; it runs even if you leave this page.");
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setStarting(false);
    }
  };
  const stop = async (run: HuntRun) => {
    try {
      const stopped = await api<HuntRun>(`/v2/hunt/${run.id}/stop`, "POST");
      setInfo((was) => (was ? { ...was, current: stopped } : was));
      notify("Stopping at the next step. Jobs already saved stay saved.");
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  if (!info || !choice) return null;
  const shown = info.current ?? info.last;
  return (
    <>
      {info.current ? (
        <HuntProgress run={info.current} onStop={() => stop(info.current!)} onJob={onJob} />
      ) : (
        <HuntSetup
          info={info}
          choice={choice}
          onChoice={setChoice}
          starting={starting}
          onStart={start}
          blocked={pipelineActive ? "A Daily Search is running. Start the hunt when it finishes." : null}
        />
      )}
      {!info.current && shown && <HuntProgress run={shown} onStop={() => {}} onJob={onJob} />}
    </>
  );
}
