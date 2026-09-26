import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import { Badge } from "../components/UI";
import type { FitRequirement, JobFit } from "../types";

const GROUPS: [FitRequirement["category"], string][] = [
  ["required", "Must-haves"],
  ["preferred", "Nice to have"],
  ["responsibility", "The work itself"],
];
const STATUS: Record<FitRequirement["status"], { word: string; tone: "green" | "amber" | "neutral" }> = {
  met: { word: "You have it", tone: "green" },
  partial: { word: "Partly", tone: "amber" },
  missing: { word: "Gap", tone: "neutral" },
  unknown: { word: "Not checked", tone: "neutral" },
};

/** The checked list itself: each requirement quoted from the posting, with the evidence that meets it. */
export function FitList({ fit }: { fit: JobFit }) {
  const reqs = fit.matrix.requirements;
  return (
    <>
      <p className="fit-rationale">{fit.rationale}</p>
      {fit.matrix.hard_blockers.map((b) => (
        <p key={b.excerpt} className="callout warning fit-blocker">
          <b>Blocker: {b.reason}.</b> “{b.excerpt}”
        </p>
      ))}
      {GROUPS.map(([category, title]) => {
        const items = reqs.filter((r) => r.category === category && r.status !== "unknown");
        if (!items.length) return null;
        const met = items.filter((r) => r.status === "met").length;
        return (
          <section key={category} className="fit-group">
            <h4>
              {title} <small>· {met} of {items.length} met</small>
            </h4>
            <ul>
              {items.map((r) => (
                <li key={r.text + r.excerpt} className={"fit-item " + r.status}>
                  <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].word}</Badge>
                  <details>
                    <summary>{r.text}</summary>
                    <p className="small">
                      The posting says: “{r.excerpt}”
                      {r.evidence_ids.length ? <> · Your evidence: {r.evidence_ids.join(", ")}</> : null}
                      {r.note ? <> · {r.note}</> : null}
                    </p>
                  </details>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}

/** Studio's requirement check: read from the saved check (no AI); "Check again" uses a free plan when one is free. */
export default function FitCheck({ jobId }: { jobId: string }) {
  const [fit, setFit] = useState<JobFit | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    let alive = true;
    setFit(null);
    setError("");
    api<JobFit>(`/v2/jobs/${encodeURIComponent(jobId)}/fit`)
      .then((f) => alive && setFit(f))
      .catch((e) => alive && setError((e as Error).message));
    return () => {
      alive = false;
    };
  }, [jobId]);
  async function again() {
    setChecking(true);
    setError("");
    try {
      setFit(await api<JobFit>(`/v2/jobs/${encodeURIComponent(jobId)}/fit`, "POST", undefined, { timeout: 180000 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }
  return (
    <div className="card fit-check">
      <div className="section-head">
        <div>
          <div className="eyebrow">REQUIREMENT CHECK · WHAT THE JOB ASKS FOR</div>
          <h3>
            {fit ? `Fit ${fit.score}/100` : "Checking the posting…"}
            {fit && (
              <Badge tone={fit.method === "ai" ? "green" : "neutral"}>
                {fit.method === "ai" ? `Checked by ${fit.provider_label || "AI"}` : "Checked by rules"}
              </Badge>
            )}
          </h3>
          <p className="small">
            Every item is quoted from the posting and matched only to evidence registered in your profile. The same list
            guides the tailoring, the coverage score and the study plan.
          </p>
        </div>
        <button className="secondary" disabled={checking || !fit} onClick={() => void again()}>
          <RefreshCw size={16} className={checking ? "spin" : ""} />
          {checking ? "Checking…" : "Check again"}
        </button>
      </div>
      {error && <p className="callout warning">{error}</p>}
      {fit && <FitList fit={fit} />}
    </div>
  );
}
