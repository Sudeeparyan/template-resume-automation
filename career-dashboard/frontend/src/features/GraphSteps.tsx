import { useEffect, useState } from "react";
import { api } from "../api";

// --- what the backend sends (GET /v2/agents/runs/<id>/graph) -------------------------

export type GraphValue = { type: string; size: number; sha: string; preview?: string };
export type GraphStep = {
  checkpoint_id: string;
  step: number | null;
  source: string | null;
  at: string | null;
  ran: string[];
  changed: Record<string, GraphValue>;
  removed: string[];
  next: string[];
};
export type GraphThread = { thread: string; graph: string; steps: GraphStep[]; finished: boolean; resumes_at: string[] };
export type GraphHistory = { run_id: string; threads: GraphThread[]; note: string };

const time = (at: string | null) => (at ? new Date(at).toLocaleTimeString() : "");

// Graphs whose runs can be run again from a step (POST /v2/agents/runs/<id>/rerun).
const RERUNNABLE = new Set(["research"]);

/** A graph run checkpoint by checkpoint: the node that ran, what it changed, what runs next. */
export function GraphStepsView({ history, showData, onRerun, busy }: {
  history: GraphHistory;
  showData: boolean;
  onRerun?: (checkpointId: string, next: string[]) => void;
  busy?: boolean;
}) {
  if (!history.threads.length)
    return <p className="muted small">This run was not made by a graph, so it has no checkpoints. The Steps and Timeline tabs show it.</p>;
  return (
    <div className="graph-steps">
      {history.threads.map((thread) => (
        <section key={thread.thread}>
          <h4>
            {thread.graph}{" "}
            <span className="muted small">
              {thread.finished ? "finished" : `stopped; resumes at ${thread.resumes_at.join(", ")}`} · {thread.steps.length} checkpoints
            </span>
          </h4>
          <ol>
            {thread.steps.map((step) => (
              <li key={step.checkpoint_id}>
                <strong>{step.ran.length ? step.ran.join(", ") : "start"}</strong>{" "}
                {step.source === "fork" && <span className="muted small">(run again from here, copying an earlier run) </span>}
                <span className="muted small">{time(step.at)}</span>
                {Object.entries(step.changed).map(([key, value]) => (
                  <div key={key} className="small">
                    <code>{key}</code> <span className="muted">{value.type}, {value.size} chars</span>
                    {showData && value.preview && <pre className="graph-preview">{value.preview}</pre>}
                  </div>
                ))}
                {step.next.length > 0 && (
                  <div className="muted small">
                    next: {step.next.join(", ")}
                    {onRerun && thread.finished && RERUNNABLE.has(thread.graph) && !step.next.includes("(parallel tasks)") && (
                      <>
                        {" "}
                        <button type="button" className="link-button" disabled={busy}
                          title="A new run that keeps the results before this point and runs the rest again, with fresh AI answers. This run stays as it is."
                          onClick={() => onRerun(step.checkpoint_id, step.next)}>
                          Run again from {step.next.join(" and ")}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </section>
      ))}
      <p className="muted small">{history.note}</p>
    </div>
  );
}

export default function GraphSteps({ runId, live }: { runId: string; live: boolean }) {
  const [showData, setShowData] = useState(false);
  const [history, setHistory] = useState<GraphHistory | null>(null);
  const [error, setError] = useState("");
  const [rerun, setRerun] = useState<{ busy: boolean; message: string }>({ busy: false, message: "" });
  const runAgain = (checkpointId: string, next: string[]) => {
    setRerun({ busy: true, message: "" });
    api<{ id: string }>(`/v2/agents/runs/${encodeURIComponent(runId)}/rerun`, "POST", { checkpoint_id: checkpointId })
      .then((started) => setRerun({ busy: false, message: `Started run ${started.id.slice(0, 8)} from ${next.join(" and ")}. It appears in the list of runs.` }))
      .catch((e: Error) => setRerun({ busy: false, message: e.message }));
  };
  useEffect(() => {
    let stopped = false;
    const load = () =>
      api<GraphHistory>(`/v2/agents/runs/${encodeURIComponent(runId)}/graph?content=${showData}`)
        .then((found) => !stopped && setHistory(found))
        .catch((e: Error) => !stopped && setError(e.message));
    load();
    const timer = live ? setInterval(load, 4000) : undefined;
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [runId, live, showData]);
  if (error) return <p className="callout warning">{error}</p>;
  if (!history) return <p className="muted small">Loading the checkpoints…</p>;
  return (
    <>
      <label className="small">
        <input type="checkbox" checked={showData} onChange={(e) => setShowData(e.target.checked)} /> Show my data (this
        computer only)
      </label>
      {rerun.message && <p className="callout small" role="status">{rerun.message}</p>}
      <GraphStepsView history={history} showData={showData} onRerun={runAgain} busy={rerun.busy} />
    </>
  );
}
