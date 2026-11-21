import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { api } from "../api";
import { Badge, Field, Modal } from "../components/UI";
import type { Notify } from "../components/UI";
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

/** How the 0-100 fit was made: requirement coverage, role and seniority, location. */
export function ScoreBreakdown({ fit }: { fit: JobFit }) {
  const rows: [string, number, number, string][] = [
    ["Requirements met", fit.components.requirements, 75, "Must-haves weigh most; partly met counts half."],
    ["Role and seniority", fit.components.role_seniority, 15, "The title matches your target roles and level."],
    ["Location", fit.components.location, 10, "The place is one you can work in."],
  ];
  return (
    <div className="score-breakdown" aria-label="How the fit score is made">
      {rows.map(([label, value, max, hint]) => (
        <div key={label} className="score-row" title={hint}>
          <span>{label}</span>
          <span className="score-track" aria-hidden="true"><span style={{ width: `${Math.round((value / max) * 100)}%` }} /></span>
          <b>{value}<small>/{max}</small></b>
        </div>
      ))}
      {!fit.must_have_ok && (
        <p className="small fit-cap">Fewer than half of the must-haves are met, so this role is below the bar for a tailored resume.</p>
      )}
    </div>
  );
}

/** The checked list itself: each requirement quoted from the posting, with the evidence that meets it.
 * `onHaveIt` offers "I have this" on gaps: the person adds it to their profile in their own words. */
export function FitList({ fit, onHaveIt }: { fit: JobFit; onHaveIt?: (item: FitRequirement) => void }) {
  const reqs = fit.matrix.requirements;
  return (
    <>
      <p className="fit-rationale">{fit.rationale}</p>
      <ScoreBreakdown fit={fit} />
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
                  {onHaveIt && r.status !== "met" && (
                    <button type="button" className="text-button fit-have" onClick={() => onHaveIt(r)}>
                      <Plus size={14} /> I have this
                    </button>
                  )}
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
export default function FitCheck({ jobId, notify }: { jobId: string; notify?: Notify }) {
  const [fit, setFit] = useState<JobFit | null>(null);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);
  const [haveIt, setHaveIt] = useState<FitRequirement | null>(null);
  const load = useCallback((alive: () => boolean) => {
    setError("");
    return api<JobFit>(`/v2/jobs/${encodeURIComponent(jobId)}/fit`)
      .then((f) => alive() && setFit(f))
      .catch((e) => alive() && setError((e as Error).message));
  }, [jobId]);
  useEffect(() => {
    let alive = true;
    setFit(null);
    void load(() => alive);
    return () => {
      alive = false;
    };
  }, [load]);
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
      {fit && <FitList fit={fit} onHaveIt={notify ? setHaveIt : undefined} />}
      {haveIt && notify && (
        <HaveItDialog item={haveIt} notify={notify} onClose={() => setHaveIt(null)}
          onSaved={() => {
            setHaveIt(null);
            // The evidence changed, so the saved check is out of date: read the new one.
            void load(() => true);
          }} />
      )}
    </div>
  );
}

/** "I have this": the person says, in their own words, where they used it. Saved as a
 * profile entry through the normal Profile path; nothing is added without their text. */
export function HaveItDialog({ item, notify, onClose, onSaved }: {
  item: FitRequirement;
  notify: Notify;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(item.text.slice(0, 120));
  const [details, setDetails] = useState("");
  const [busy, setBusy] = useState(false);
  const enough = details.trim().split(/\s+/).filter(Boolean).length >= 4;
  return (
    <Modal title="Add this to your profile" onClose={onClose}>
      <p className="small">
        The posting asks for: “{item.excerpt}”. If you have it, say where and how you used it, in your own words. It is saved to
        your Profile like any other entry, and only what you write here can appear on a resume.
      </p>
      <form onSubmit={async (event) => {
        event.preventDefault();
        if (!enough) return;
        setBusy(true);
        try {
          await api("/v2/profile/items", "POST", { kind: "fact", title: title.trim(), fields: { title: title.trim(), details: details.trim() } });
          notify("Added to your Profile. The requirement check has been read again with it.");
          onSaved();
        } catch (e) {
          notify((e as Error).message, true);
        } finally {
          setBusy(false);
        }
      }}>
        <Field label="Short label">
          <input value={title} maxLength={250} required onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Where and how you used it (your own words)">
          <textarea rows={5} maxLength={5000} value={details} onChange={(e) => setDetails(e.target.value)}
            placeholder="For example: in my final-year project I wrote SQL queries to clean 2 years of sales data." />
        </Field>
        <div className="actions">
          <button type="submit" className="primary" disabled={busy || !enough || !title.trim()}>{busy ? "Saving…" : "Add to my profile"}</button>
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
