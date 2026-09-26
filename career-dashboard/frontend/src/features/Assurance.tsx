import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Download, ShieldCheck, X } from "lucide-react";
import { api } from "../api";
import { AskAssistant, Badge, Empty, Loading } from "../components/UI";
import type { AssuranceReport, Summary } from "../types";

export function confidenceTone(score: number): string {
  return score >= 100 ? "green" : score >= 60 ? "amber" : "red";
}

export default function Assurance({
  data,
  notify,
  refresh,
  onJob,
}: {
  data: Summary;
  notify: (s: string, e?: boolean) => void;
  refresh: () => Promise<void>;
  onJob: (id: string) => void;
}) {
  const jobs = data.jobs;
  const drafted = useMemo(
    () => new Set(data.documents.filter((d) => d.resumes.length > 0).map((d) => d.job_id)),
    [data.documents],
  );
  const [jobId, setJobId] = useState(
    () => jobs.find((j) => drafted.has(j.id))?.id || jobs[0]?.id || "",
  );
  const selectedJob = jobs.find((j) => j.id === jobId);
  const [report, setReport] = useState<AssuranceReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async (id: string) => {
    if (!id) return;
    try {
      setReport(await api<AssuranceReport>("/v2/assurance/" + id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load(jobId);
  }, [jobId, load]);

  async function decide(claimItems: string[], decision: "kept" | "removed") {
    setBusy(decision + claimItems.join(","));
    try {
      for (const itemId of claimItems) {
        await api(`/v2/studio/${jobId}/items/${itemId}/decision`, "POST", { decision });
      }
      await load(jobId);
      await refresh();
      notify(
        decision === "kept"
          ? "Kept. It stays on the resume for this job."
          : "Removed from this resume. The draft was repaired automatically.",
      );
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  function download() {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `assurance-${report.company.replace(/[^A-Za-z0-9]+/g, "-")}-${report.job_id.slice(0, 8)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const summary = report?.summary;
  const reviewClaims = (report?.claims || []).filter(
    (c) => c.evidence_status !== "verified" || c.decision,
  );
  const verifiedClaims = (report?.claims || []).filter(
    (c) => c.evidence_status === "verified" && !c.decision,
  );

  const intro = (
    <div>
      <div className="eyebrow">CHECK BEFORE YOU APPLY</div>
      <h1>Assurance</h1>
      <p>
        Every claim on the tailored resume, checked against your evidence.
        Predicted items wait for your keep or remove decision.
      </p>
    </div>
  );
  if (!jobs.length)
    return (
      <>
        <div className="page-title">{intro}</div>
        <section className="card">
          <Empty title="No jobs to review yet">
            Run a job search first; once a resume is tailored, its claims are checked here before you apply.
          </Empty>
        </section>
      </>
    );

  return (
    <>
      <div className="page-title">
        {intro}
        <div className="actions">
          <select
            aria-label="Job to review"
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
          >
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.company} · {j.title}
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => onJob(jobId)}>
            Open in Resume Studio
          </button>
          {selectedJob && (
            <AskAssistant
              label="Ask about these claims"
              prompts={[
                { label: "What should I keep or remove?", text: `Check the ${selectedJob.company} resume's claims and tell me in plain words what I should keep or remove before I apply.` },
                { label: "Is it ready to send?", text: `Is the ${selectedJob.company} — ${selectedJob.title} resume ready to send? Check the PDF, the scores and the Assurance claims.` },
              ]}
            />
          )}
          <button className="secondary" disabled={!report} onClick={download} title="Save the review report as JSON">
            <Download size={15} /> Report
          </button>
        </div>
      </div>

      {error && (
        <div className="callout warning" role="alert">
          {error}
          <button className="secondary" onClick={() => load(jobId)}>
            Retry
          </button>
        </div>
      )}
      {!report && !error && <Loading label="Checking the claims" />}

      {report?.note && (
        <Empty title="Nothing to check yet">{report.note}</Empty>
      )}

      {report && !report.note && summary && (
        <>
          <div className="obs-stats">
            <div className="card obs-stat">
              <span className="eyebrow">VERIFIED</span>
              <strong>{summary.verified}</strong>
              <small>Claims grounded in your evidence registry</small>
            </div>
            <div className="card obs-stat">
              <span className="eyebrow">PREDICTED</span>
              <strong className={summary.predicted ? "tone-warn" : ""}>{summary.predicted}</strong>
              <small>Tailored for this company — your call to keep them</small>
            </div>
            <div className="card obs-stat">
              <span className="eyebrow">NEEDS EVIDENCE</span>
              <strong className={summary.missing ? "tone-failed" : ""}>{summary.missing}</strong>
              <small>Tags that resolve nowhere; fix before applying</small>
            </div>
            <div className="card obs-stat">
              <span className="eyebrow">REVIEWED</span>
              <strong>
                {summary.kept + summary.removed}
                <small> / {summary.kept + summary.removed + summary.pending}</small>
              </strong>
              <small>
                {summary.pending
                  ? `${summary.pending} still waiting for you`
                  : "Every tailored item has your decision"}
              </small>
            </div>
          </div>

          {summary.pending === 0 && summary.missing === 0 && (
            <div className="callout">
              <ShieldCheck size={21} />
              <div>
                <b>Ready when you are.</b>
                <p>Every claim is verified or has your decision. Review the PDF one last time, then apply.</p>
              </div>
            </div>
          )}

          <section className="card spaced">
            <div className="section-title">
              <h2>To review</h2>
              <span className="small muted">Predicted and undecided claims first</span>
            </div>
            {!reviewClaims.length ? (
              <p className="muted">Nothing waiting — every claim on this resume is verified against your evidence.</p>
            ) : (
              <ol className="claim-list">
                {reviewClaims.map((claim, i) => (
                  <li key={i} className={"claim-row " + claim.evidence_status}>
                    <div className="claim-main">
                      <span className="claim-tags">
                        <Badge tone={confidenceTone(claim.confidence)}>Evidence {claim.confidence}</Badge>
                        <Badge tone="neutral">{claim.section}</Badge>
                        {claim.evidence_status === "predicted" && <Badge tone="amber">Predicted</Badge>}
                        {claim.evidence_status === "missing" && <Badge tone="red">Needs evidence</Badge>}
                        {claim.decision === "kept" && <Badge tone="green">Kept</Badge>}
                        {claim.decision === "removed" && <Badge tone="red">Removed</Badge>}
                      </span>
                      <p className="claim-text">{claim.text}</p>
                      <details>
                        <summary>Evidence details</summary>
                        <small>
                          {claim.line != null ? `Source line ${claim.line}` : "Not on the current page"}
                          {claim.note ? ` · ${claim.note}` : ""}
                        </small>
                        <pre>{(claim.evidence_ids || []).join("\n") || "No evidence tags"}</pre>
                      </details>
                    </div>
                    {(claim.items || []).length > 0 && (
                      <span className="claim-actions">
                        <button
                          className="secondary"
                          disabled={!!busy || claim.decision === "kept"}
                          title="Keep this on the resume"
                          onClick={() => decide(claim.items || [], "kept")}
                        >
                          <Check size={15} /> Keep
                        </button>
                        <button
                          className="secondary danger-text"
                          disabled={!!busy || claim.decision === "removed"}
                          title="Take this off the resume"
                          onClick={() => decide(claim.items || [], "removed")}
                        >
                          <X size={15} /> Remove
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <details className="card spaced">
            <summary>Verified claims ({verifiedClaims.length})</summary>
            <ol className="claim-list">
              {verifiedClaims.map((claim, i) => (
                <li key={i} className="claim-row verified">
                  <div className="claim-main">
                    <span className="claim-tags">
                      <Badge tone="green">Evidence {claim.confidence}</Badge>
                      <Badge tone="neutral">{claim.section}</Badge>
                    </span>
                    <p className="claim-text">{claim.text}</p>
                    <details>
                      <summary>Evidence details</summary>
                      <small>
                        Source line {claim.line} · {claim.note}
                      </small>
                      <pre>{(claim.evidence_ids || []).join("\n")}</pre>
                    </details>
                  </div>
                </li>
              ))}
            </ol>
          </details>
        </>
      )}
    </>
  );
}
