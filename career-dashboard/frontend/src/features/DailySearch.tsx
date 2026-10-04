import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { api } from "../api";
import { AskAssistant, Running, Empty } from "../components/UI";
import { JobList } from "../components/JobList";
import { PipelineBuilder, PipelineProgress } from "./SearchPipeline";
import OvernightHunt from "./OvernightHunt";
import Coverage from "./Coverage";
import type { Job, PipelineChoice, PipelineInfo, PipelineRun, PipelineStatus, Summary } from "../types";
/** What a search sends: the AI is left out, so the one chosen in Settings does the work. */
function searchOnly({ count, source, steps }: PipelineChoice) {
  return { count, source, steps };
}

/** Salary sections never hide a recorded application or promote a researched estimate to an offer. */
export function opportunityGroups(jobs: Job[]) {
  const groups: Record<"salary_matches" | "estimated_matches" | "researched_leads" | "needs_research" | "below_floor" | "applications" | "other", Job[]> = {
    salary_matches: [], estimated_matches: [], researched_leads: [], needs_research: [], below_floor: [], applications: [], other: [],
  };
  for (const job of jobs) {
    const opportunity = job.opportunity;
    if (job.application_date || !["saved", "prepared"].includes(job.status)) groups.applications.push(job);
    else if (!opportunity) groups[job.market === "ie" ? "needs_research" : "other"].push(job);
    else if (opportunity.salary_state === "below_floor" || opportunity.section === "below_floor") groups.below_floor.push(job);
    else if (opportunity.salary.kind === "advertised" && opportunity.salary_state === "meets_floor" && opportunity.section === "salary_matches") groups.salary_matches.push(job);
    else if (opportunity.salary.kind === "researched" && opportunity.section === "researched_leads" && opportunity.salary_state === "meets_floor") groups.researched_leads.push(job);
    else if (opportunity.salary.kind === "unknown" && opportunity.section === "estimated_matches" && opportunity.estimate) groups.estimated_matches.push(job);
    else groups.needs_research.push(job);
  }
  return groups;
}

export function OpportunityLists({ jobs, onJob }: { jobs: Job[]; onJob: (id: string) => void }) {
  const groups = opportunityGroups(jobs);
  const sections = [
    ["salary_matches", "Advertised salary matches", "The advertised salary meets your floor. Review permit checks and application readiness separately."],
    ["estimated_matches", "Market estimate meets your floor", "These postings state no salary; similar advertised roles pay at least your floor. Confirm the base salary with the recruiter before you apply."],
    ["researched_leads", "Researched salary leads", "Salary estimates from cited research. Confirm the actual offer with the employer before treating it as a salary match."],
    ["needs_research", "Salary needs confirmation", "Salary is missing, unverified, or the range does not confirm your floor. These roles are outside the salary shortlist."],
    ["below_floor", "Below your salary floor", "Saved for reference and kept outside the salary shortlist."],
    ["applications", "Tracked applications", "Your application history stays visible regardless of salary evidence."],
    ["other", "Other saved jobs", "These jobs do not have an Ireland salary assessment."],
  ] as const;
  if (!jobs.length) return <Empty title="No jobs in this view">New discoveries will appear here with their salary evidence.</Empty>;
  return <>{sections.map(([key, title, note]) => groups[key].length > 0 && <section className="spaced" key={key} aria-label={title}>
    <h2>{title} · {groups[key].length}</h2>
    <p className="small muted">{note}</p>
    <JobList jobs={groups[key]} onSelect={onJob} filters={false} />
  </section>)}</>;
}

export default function DailySearch({
  data,
  refresh,
  notify,
  onJob,
  onAdd,
}: {
  data: Summary;
  refresh: () => Promise<void>;
  notify: (s: string, e?: boolean) => void;
  onJob: (id: string) => void;
  onAdd: () => void;
}) {
  const [runs, setRuns] = useState<any[]>([]);
  const [all, setAll] = useState(false);
  const [info, setInfo] = useState<PipelineInfo | null>(null);
  const [choice, setChoice] = useState<PipelineChoice | null>(null);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    api("/search-runs")
      .then((r) => setRuns(r.runs))
      .catch((e) => notify(e.message, true));
  }, [data]);
  const loadPipeline = useCallback(
    () =>
      api<PipelineInfo>("/v2/pipeline")
        .then((loaded) => {
          setInfo(loaded);
          setChoice((was) => was ?? loaded.preferences);
        })
        .catch((e) => notify((e as Error).message, true)),
    [notify],
  );
  const refreshStatus = useCallback(
    () =>
      api<PipelineStatus>("/v2/pipeline/status", "GET", undefined, { timeout: 15000 })
        .then((status) => setInfo((was) => (was ? { ...was, ...status } : was)))
        .catch(() => {}),
    [],
  );
  useEffect(() => {
    loadPipeline();
  }, [loadPipeline]);
  useEffect(() => {
    refreshStatus();
  }, [data, refreshStatus]);
  const active = !!info?.current;
  useEffect(() => {
    if (active) return;
    const timer = setInterval(refreshStatus, 15000);
    return () => clearInterval(timer);
  }, [active, refreshStatus]);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refreshStatus, 3000);
    return () => clearInterval(timer);
  }, [active, refreshStatus]);
  // When a search ends: reload the job list and the estimates it just taught.
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) {
      refresh().catch(() => {});
      loadPipeline();
    }
    wasActive.current = active;
  }, [active, refresh, loadPipeline]);
  // Remember her choices (not on the first load), a moment after she stops changing them.
  const firstChoice = useRef(true);
  useEffect(() => {
    if (!choice?.provider) return;
    if (firstChoice.current) {
      firstChoice.current = false;
      return;
    }
    const timer = setTimeout(
      () => api("/v2/pipeline/preferences", "PUT", searchOnly(choice)).catch((e) => notify((e as Error).message, true)),
      600,
    );
    return () => clearTimeout(timer);
  }, [choice, notify]);
  const start = async () => {
    if (!choice) return;
    setStarting(true);
    try {
      const run = await api<PipelineRun>("/v2/pipeline/run", "POST", searchOnly(choice));
      setInfo((was) => (was ? { ...was, current: run } : was));
      notify("Search started. It keeps running if you leave this page.");
      await refresh();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setStarting(false);
    }
  };
  const stop = async (run: PipelineRun) => {
    try {
      const stopped = await api<PipelineRun>(`/v2/pipeline/${run.id}/stop`, "POST");
      setInfo((was) => (was ? { ...was, current: stopped } : was));
      notify("Stopping after the current step. Finished work is kept.");
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  const current = runs.find((r) => r.date === data.goals.date);
  const ids = new Set((current?.jobs || []).map((j: any) => j.id));
  const latestDiscovery = data.runs.find((r) => r.kind === "discovery");
  const running = data.runs.find(
    (r) => r.kind === "discovery" && ["queued", "running"].includes(r.state),
  );
  const builder =
    info && choice ? (
      <PipelineBuilder
        info={info}
        choice={choice}
        onChoice={setChoice}
        running={active}
        starting={starting}
        onStart={start}
      />
    ) : (
      <section className="card" role="status" aria-label="Loading search options">
        <div className="skeleton skeleton-title" />
        <div className="skeleton skeleton-card" />
      </section>
    );
  const shown = info?.current ?? info?.last;
  const progress = info && shown && (
    <PipelineProgress run={shown} info={info} onStop={() => stop(shown)} onJob={onJob} />
  );
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">CONSISTENCY OVER PERFECTION</div>
          <h1>Daily Search</h1>
          <p>
            Choose how many jobs you want and which helpers should run. You will
            see how long it takes before you start.
          </p>
        </div>
        <div className="actions">
          <AskAssistant
            prompts={[
              { label: "How is the search going?", text: "How is the Daily Search going right now?" },
              { label: "Why were jobs turned away?", text: "Why did the last search save only the jobs it did? Show me what it turned away and why." },
              { label: "Run a search from the chat", text: "Run the daily search for 2 jobs with research, a tailored resume, the study plan and the PDF", send: false },
              { label: "Hunt overnight", text: "Hunt overnight for 10 jobs that fit at 75 or better, with a tailored resume and PDF for each", send: false },
            ]}
          />
        </div>
      </div>
      {/* The day's plan in one line; the week and the plan editor live on the Dashboard. */}
      <p className="search-status" role="status">
        <b>
          {data.goals.remaining_today ? `${data.goals.remaining_today} left today` : "Today's plan is done"}
        </b>
        <span>
          {data.goals.week_completed}/{data.goals.current_week_target} applied this week
        </span>
        <a href="#dashboard">Your week and plan on the Dashboard →</a>
      </p>
      {active ? (
        <>
          {progress}
          {builder}
        </>
      ) : (
        <>
          {builder}
          {progress}
        </>
      )}
      {running && !active && <Running run={running} />}
      <OvernightHunt notify={notify} onJob={onJob} refresh={refresh} pipelineActive={active} />
      <Coverage refreshKey={data.runs.slice(0, 5).map((run) => `${run.id}:${run.state}:${run.updated_at}`).join("|")} />
      {latestDiscovery?.result?.summary && (
        <details className="search-notes">
          <summary>What the last job search tried, and why it stopped</summary>
          <p className="small">{latestDiscovery.result.summary}</p>
          {!!latestDiscovery.result.excluded?.length && (
            <>
              <p className="small">
                <b>Excluded by the sponsorship gate</b> — never shown as leads. The exact sentence is
                kept so a wrong call can be restored from the Dashboard.
              </p>
              <table className="small">
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>Role</th>
                    <th>The posting says</th>
                  </tr>
                </thead>
                <tbody>
                  {latestDiscovery.result.excluded.map((e: { company: string; title: string; url: string; sentence: string; reason: string }, i: number) => (
                    <tr key={i}>
                      <td>{e.company}</td>
                      <td>{e.title}</td>
                      <td>
                        “{e.sentence}” <span className="muted">({e.reason})</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {!!latestDiscovery.result.rejected_leads?.length && (
            <details>
              <summary className="small">Other leads set aside · {latestDiscovery.result.rejected_leads.length}</summary>
              <pre className="small">{latestDiscovery.result.rejected_leads.join("\n")}</pre>
            </details>
          )}
        </details>
      )}
      <section className="card">
        <div className="section-title">
          <div className="segmented">
            <button
              className={!all ? "selected" : ""}
              onClick={() => setAll(false)}
            >
              Today’s finds
            </button>
            <button
              className={all ? "selected" : ""}
              onClick={() => setAll(true)}
            >
              All saved jobs · {data.jobs.length}
            </button>
          </div>
          <button className="text-button" onClick={onAdd}>
            ＋ Save posting
          </button>
        </div>
        <OpportunityLists
          jobs={all ? data.jobs : data.jobs.filter((j) => ids.has(j.id))}
          onJob={onJob}
        />
      </section>
      <section className="card spaced">
        <h2>Search history</h2>
        {runs.length ? (
          runs.map((r) => (
            <details key={r.date}>
              <summary>
                {r.date} · {r.jobs.length} saved postings
              </summary>
              <p className="preserve">
                {r.notes || "No search notes recorded."}
              </p>
              {r.jobs.map((j: any) =>
                j.deleted_at ? (
                  <div className="history-job muted" key={j.id}>
                    {j.company} · {j.title} · removed from active list
                  </div>
                ) : (
                  <button
                    className="history-job"
                    key={j.id}
                    onClick={() => onJob(j.id)}
                  >
                    {j.company} · {j.title}
                    <ArrowUpRight size={16} />
                  </button>
                ),
              )}
            </details>
          ))
        ) : (
          <Empty title="Every search has a history">
            New discoveries are saved here and excluded from future results.
          </Empty>
        )}
      </section>
    </>
  );
}
