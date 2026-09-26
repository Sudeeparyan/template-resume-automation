import { useContext, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Copy,
  Download,
  FileText,
  ArrowRight,
  MessageSquareText,
  RefreshCw,
  Save,
  Search,
  Settings2,
  Sparkles,
} from "lucide-react";
import { API_BASE, api, fileUrl } from "../api";
import { AskAssistant, AskContext, Badge, Field, Modal, ReportView, Running } from "../components/UI";
import { JobList } from "../components/JobList";
import { useMarket } from "../profiles";
import { macroSpan, readField, writeField } from "./studioFields";
import FitCheck from "./FitCheck";
import { AgentControl } from "./AgentControl";
import type { CoverLetter, Job, Run, Summary } from "../types";
type Draft = {
  match?: {
    score: number | null;
    ats_readiness?: { score: number; components: Record<string, number> };
    resume_coverage?: { score: number; breakdown: Record<string, any> };
    opportunity_fit?: { label: string; hard_blockers: string[] };
    keywords?: {
      requirement: string;
      category: string;
      status: string;
      excerpt: string;
    }[];
    missing_keywords?: string[];
    missing_supported?: string[];
    missing_unsupported?: string[];
    current: boolean;
    revision: number;
    gaps?: string[];
    limitations: string[];
  };
  project_library: {
    id: string;
    title: string;
    rank: number | null;
    eligible: boolean;
    review_state: string;
    score?: number | null;
    reason?: string[];
  }[];
  source: string;
  revision: number;
  file_root: string;
  preview: {
    current: boolean;
    page_count: number;
    revision: number;
    path: string;
    layout?: {
      full_pages: boolean;
      page_count?: number;
      target_pages?: number;
      paper?: string;
      pages: { page: number; fill_percent: number; bottom_blank_mm: number }[];
    };
    ranking?: { section_order: string[]; changes: string[] };
    body_font_pt?: number;
  } | null;
  versions: { revision: number; created_at: string }[];
  captures: { id: string; title: string; kind: string; deleted: boolean }[];
  warnings: string[];
  projects: { id: string; title: string }[];
};
const labels: Record<string, string> = {
  ResumeSummary: "Professional summary",
  CoreSkills: "Core skills · separate with semicolons",
  SkillsLanguages: "Skills · Languages (comma-separated)",
  SkillsData: "Skills · Data Engineering",
  SkillsML: "Skills · Machine Learning and AI",
  SkillsCloud: "Skills · Cloud and Tools",
  SkillsTest: "Skills · Test Automation and Embedded",
  Coursework: "Coursework line (may be removed to fit the page contract)",
  SecondProjectTitle: "Second project name",
  SecondProjectContext: "Second project context and tools",
  SecondProjectBulletOne: "Second project point 1",
  SecondProjectBulletTwo: "Second project point 2",
  SecondProjectBulletThree: "Second project point 3",
  SelectedProjectTitle: "First project name",
  SelectedProjectContext: "Project context and tools",
  SelectedProjectBulletOne: "Project point 1",
  SelectedProjectBulletTwo: "Project point 2",
  SelectedProjectBulletThree: "Project point 3",
};
const contentFields = ["ResumeSummary", "CoreSkills", "SkillsLanguages", "SkillsData", "SkillsML", "SkillsCloud", "SkillsTest", "Coursework"];
const projectFields = [
  "SelectedProjectTitle",
  "SelectedProjectContext",
  "SelectedProjectBulletOne",
  "SelectedProjectBulletTwo",
  "SelectedProjectBulletThree",
  "SecondProjectTitle",
  "SecondProjectContext",
  "SecondProjectBulletOne",
  "SecondProjectBulletTwo",
  "SecondProjectBulletThree",
];
export default function ResumeStudio({
  data,
  jobId,
  onJob,
  onDetails,
  refresh,
}: {
  data: Summary;
  jobId: string | null;
  onJob: (id: string) => void;
  onDetails: (id: string) => void;
  refresh: () => Promise<void>;
}) {
  const job = data.jobs.find((j) => j.id === jobId);
  const market = useMarket(job?.market);
  const [coverLetter, setCoverLetter] = useState<CoverLetter | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverError, setCoverError] = useState("");
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">ONE ROLE AT A TIME</div>
          <h1>Resume Studio</h1>
          <p>
            Understand the role, check your current match, then improve the
            resume with guided AI help.
          </p>
        </div>
        <div className="actions">
          <AskAssistant
            prompts={
              job
                ? [
                    { label: "What is this resume missing?", text: `What does the ${job.company} — ${job.title} posting ask for that this resume does not show yet, and what can I honestly do about it?` },
                    { label: "Is it ready to send?", text: `Is the ${job.company} — ${job.title} resume ready to send? Check the PDF, the scores and the Assurance claims.` },
                    { label: "Change this resume", text: `Change the ${job.company} resume: `, send: false },
                  ]
                : [{ label: "Which jobs still need a resume?", text: "Which saved jobs still have no resume PDF?" }]
            }
          />
          <Badge>Editable {market.resumeShape} draft</Badge>
        </div>
      </div>
      {job && (
      <div className="studio-job-bar">
        <Field label="Company and role">
          <select value={jobId || ""} onChange={(e) => onJob(e.target.value)}>
            <option value="" disabled>
              Choose a saved job
            </option>
            {data.jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.company} · {j.title}
              </option>
            ))}
          </select>
        </Field>
        {job && (
          <div className="actions">
            <button
              className="primary"
              disabled={coverBusy || job.record_source === "gmail"}
              title={
                job.record_source === "gmail"
                  ? "Add the full job description first"
                  : `Generate a cover letter for ${job.company}`
              }
              onClick={async () => {
                setCoverBusy(true);
                setCoverError("");
                try {
                  const result = await api<CoverLetter>(
                    "/v2/jobs/" + job.id + "/cover-letter",
                    "POST",
                  );
                  setCoverLetter(result);
                  await refresh();
                } catch (e) {
                  setCoverError((e as Error).message);
                } finally {
                  setCoverBusy(false);
                }
              }}
            >
              <FileText size={16} />
              {coverBusy ? "Generating…" : "Cover letter"}
            </button>
            <button className="secondary" onClick={() => onDetails(job.id)}>
              Job details & progress
            </button>
          </div>
        )}
      </div>
      )}
      {coverError && (
        <div className="callout warning" role="alert">
          {coverError}
        </div>
      )}
      {job ? (
        <Editor
          key={job.id}
          job={job}
          data={data}
          refresh={refresh}
        />
      ) : (
        <section className="card spaced">
          <h2>Choose the role you’re preparing for</h2>
          <JobList jobs={data.jobs} onSelect={onJob} />
        </section>
      )}
      {coverLetter && (
        <Modal
          title={`${coverLetter.company} · Cover letter`}
          onClose={() => setCoverLetter(null)}
        >
          <div className="callout warning">
            Draft only. Review it against the job description before sending.
          </div>
          <div className="cover-letter-preview">{coverLetter.content}</div>
          <div className="actions">
            <button
              className="secondary"
              onClick={() => navigator.clipboard.writeText(coverLetter.content)}
            >
              Copy letter
            </button>
            <a
              className="primary"
              href={fileUrl(coverLetter.path)}
              target="_blank"
              rel="noreferrer"
            >
              Open saved file ↗
            </a>
          </div>
        </Modal>
      )}
    </>
  );
}
function Editor({
  job,
  data,
  refresh,
}: {
  job: Job;
  data: Summary;
  refresh: () => Promise<void>;
}) {
  const jobId = job.id;
  const company = job.company;
  const market = useMarket(job.market);
  const [draft, setDraft] = useState<Draft>();
  const [source, setSource] = useState("");
  const [step, setStep] = useState<"analyse" | "match" | "improve">(
    "analyse",
  );
  const [mode, setMode] = useState("content");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [auto, setAuto] = useState(true);
  const [restoring, setRestoring] = useState("");
  const [addingProject, setAddingProject] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");
  const [buildWatch, setBuildWatch] = useState<string>();
  const alive = useRef(true);
  const latestSource = useRef(source);
  latestSource.current = source;
  const key = "resume-studio:" + jobId;
  const base = "/v2/studio/" + jobId;
  const ask = useContext(AskContext);
  const researchRun = data.runs.find(
    (r) => r.kind === "research" && r.job_id === jobId,
  );
  const buildRun = buildWatch
    ? data.runs.find((r) => r.id === buildWatch)
    : data.runs.find(
        (r) =>
          r.kind === "resume_build" &&
          r.job_id === jobId &&
          ["queued", "running"].includes(r.state),
      );
  const reviewRun = data.runs.find(
    (r) => r.kind === "resume_match" && r.job_id === jobId,
  );
  const dirty = !!draft && source !== draft.source;
  const studyRun = data.runs.find(
    (r) => r.kind === "study_plan" && r.job_id === jobId,
  );
  const studyActive = !!studyRun && ["queued", "running"].includes(studyRun.state);
  const advisorRun = data.runs.find(
    (r) => r.kind === "resume_advisor" && r.job_id === jobId,
  );
  const advisorActive = !!advisorRun && ["queued", "running"].includes(advisorRun.state);
  function edit(value: string) {
    setSource(value);
    sessionStorage.setItem(key, value);
    setError("");
  }
  async function open() {
    setBusy("Opening your company draft…");
    setError("");
    try {
      const d = await api<Draft>(base + "/open", "POST");
      if (!alive.current) return;
      setDraft(d);
      setSource(sessionStorage.getItem(key) ?? d.source);
      refresh().catch(() => {});
      if (!d.preview && !sessionStorage.getItem(key)) {
        setBusy(`Fitting to ${market.resumeShape}: ranking registered content and balancing the layout…`);
        const p = await api<Draft>(base + "/fill", "POST", {
          revision: d.revision,
        });
        if (alive.current) {
          setDraft(p);
          setSource(p.source);
        }
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy("");
    }
  }
  useEffect(() => {
    alive.current = true;
    open();
    return () => {
      alive.current = false;
    };
  }, [jobId]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  async function save(compile = true, extra: Record<string, unknown> = {}) {
    if (!draft || busy) return;
    const snapshot = source;
    setBusy("Saving your edits…");
    setError("");
    try {
      let d = await api<Draft>(base, "PUT", {
        revision: draft.revision,
        source: snapshot,
        ...extra,
      });
      if (!alive.current) return;
      setDraft(d);
      if (Object.keys(extra).length) {
        setSource(d.source);
        sessionStorage.setItem(key, d.source);
        latestSource.current = d.source;
      } else if (latestSource.current === snapshot)
        sessionStorage.removeItem(key);
      refresh().catch(() => {});
      if (compile) {
        setBusy("Updating the preview…");
        d = await api<Draft>(base + "/preview", "POST", {
          revision: d.revision,
        });
        if (alive.current) setDraft(d);
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function download(format: "pdf" | "tex") {
    // A plain link navigated the whole app to a raw JSON error page when the
    // server refused, so fetch the file and surface any refusal as a message.
    try {
      const response = await fetch(API_BASE + base + "/download?format=" + format);
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(
          typeof detail.detail === "string"
            ? detail.detail
            : "The download could not be prepared.",
        );
      }
      const blob = await response.blob();
      const name =
        /filename="?([^"]+)"?/.exec(
          response.headers.get("content-disposition") || "",
        )?.[1] || "resume." + format;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoke on the next tick: revoking synchronously cancels the download
      // in Firefox and Safari.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function fillPages() {
    if (!draft || busy) return;
    setBusy(`Fitting to ${market.resumeShape}: ranking registered content and balancing the layout…`);
    setError("");
    try {
      let saved = draft;
      if (dirty) {
        saved = await api<Draft>(base, "PUT", {
          revision: draft.revision,
          source,
        });
        if (alive.current) setDraft(saved);
      }
      const fitted = await api<Draft>(base + "/fill", "POST", {
        revision: saved.revision,
      });
      if (!alive.current) return;
      setDraft(fitted);
      setSource(fitted.source);
      sessionStorage.removeItem(key);
      refresh().catch(() => {});
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function reloadDraft() {
    const next = await api<Draft>(base);
    if (!alive.current) return;
    setDraft(next);
    setSource(next.source);
    sessionStorage.removeItem(key);
    await refresh();
  }
  async function startBuild() {
    if (!draft || busy || buildRun) return;
    setError("");
    try {
      if (dirty) {
        setBusy("Saving your edits…");
        const saved = await api<Draft>(base, "PUT", {
          revision: draft.revision,
          source,
        });
        if (!alive.current) return;
        setDraft(saved);
        setSource(saved.source);
        sessionStorage.removeItem(key);
        setBusy("");
      }
      const result = await api<{ id: string }>("/v2/agents/run", "POST", {
        kind: "resume_build",
        job_id: jobId,
      });
      setBuildWatch(result.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      setBusy("");
    }
  }
  useEffect(() => {
    if (!buildWatch) return;
    const watched = data.runs.find((item) => item.id === buildWatch);
    if (!watched || ["queued", "running"].includes(watched.state)) return;
    setBuildWatch(undefined);
    if (watched.state === "completed") reloadDraft().catch((e) => {
      if (alive.current) setError((e as Error).message);
    });
    else setError(watched.error || "The resume build needs attention.");
  }, [buildWatch, data.runs]);
  useEffect(() => {
    if (!auto || !dirty || busy || error) return;
    const timer = setTimeout(() => save(true), 1400);
    return () => clearTimeout(timer);
  }, [source, draft?.revision, busy, auto, error]);
  if (!draft)
    return (
      <section className="card spaced">
        <p role="status">{busy || "Unable to open this draft."}</p>
        {error && (
          <>
            <p role="alert">{error}</p>
            <div className="actions">
              {error.includes("Profile") && (
                <a className="primary" href="#profile" style={{ textDecoration: "none" }}>
                  Open Profile
                </a>
              )}
              <button className="secondary" disabled={!!busy} onClick={open}>
                Retry
              </button>
            </div>
          </>
        )}
      </section>
    );
  const current = !dirty && !!draft.preview?.current;
  return (
    <>
      <div className="studio-steps" aria-label="Resume workflow">
        <button
          className={`${step === "analyse" ? "active" : ""} ${researchRun?.state === "completed" ? "complete" : ""}`}
          aria-current={step === "analyse" ? "step" : undefined}
          onClick={() => setStep("analyse")}
        >
          <b>1</b><span>Analyse role</span>
        </button>
        <button
          className={`${step === "match" ? "active" : ""} ${draft.match?.current && !dirty ? "complete" : ""}`}
          aria-current={step === "match" ? "step" : undefined}
          onClick={() => setStep("match")}
        >
          <b>2</b><span>Check match</span>
        </button>
        <button
          className={step === "improve" ? "active" : ""}
          aria-current={step === "improve" ? "step" : undefined}
          onClick={() => setStep("improve")}
        >
          <b>3</b><span>Improve resume</span>
        </button>
      </div>
      {error && step !== "improve" && (
        <div className="callout warning" role="alert">
          {error}
        </div>
      )}
      {step === "analyse" && (
        <AnalyseRole
          job={job}
          run={researchRun}
          onRun={async () => {
            setError("");
            try {
              await api("/v2/agents/run", "POST", {
                kind: "research",
                job_id: jobId,
              });
              await refresh();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
          onNext={() => setStep("match")}
        />
      )}
      {step === "match" && (
        <MatchCheck
          jobId={jobId}
          draft={draft}
          dirty={dirty}
          run={buildRun}
          review={reviewRun}
          onBuild={startBuild}
          onReview={async () => {
            setError("");
            try {
              // The AI in Settings reviews it; the review sees only the PDF text and the posting.
              await api("/v2/agents/run", "POST", { kind: "resume_match", job_id: jobId });
              await refresh();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
          onNext={() => setStep("improve")}
        />
      )}
      {step === "improve" && (
        <>
      <div className="studio-live-score">
        <div>
          <div className="eyebrow">RESUME COVERAGE</div>
          <b>
            {draft.match?.score === null || draft.match?.score === undefined
              ? "Build needed"
              : `${draft.match.score}/100`}
          </b>
          <span>
            {draft.match?.current && !dirty
              ? "Current PDF"
              : "Changes need a new build"}
          </span>
        </div>
        <button className="secondary" onClick={() => setStep("match")}>
          View match details
        </button>
      </div>
      <div className="studio-toolbar">
        <div className="studio-save-state" role="status">
          {busy ? (
            <span className="working-dot" />
          ) : !dirty ? (
            <CheckCircle2 size={18} />
          ) : (
            <span className="unsaved-dot" />
          )}
          <span>
            <b>{busy || (dirty ? "Unsaved changes" : "All changes saved")}</b>
            <small>
              {dirty
                ? "Kept safely in this browser"
                : `Version ${draft.revision}`}
            </small>
          </span>
        </div>
        <div className="actions studio-primary-actions">
          <button className="primary" disabled={!!busy} onClick={() => save()}>
            <Save size={16} />
            Save & preview
          </button>
          <button
            className="secondary"
            disabled={!current}
            title={
              current
                ? "Download the compiled PDF for this revision"
                : "Build the current revision first — the saved PDF is from an older draft"
            }
            onClick={() => download("pdf")}
          >
            <Download size={16} /> Download PDF
          </button>
          <button className="secondary" onClick={() => download("tex")}>
            <Download size={16} /> Download LaTeX
          </button>
          <details className="studio-more-actions">
            <summary className="secondary">
              <Settings2 size={16} /> More tools
            </summary>
            <div className="studio-action-menu">
              <button
                className="text-button"
                disabled={!!busy}
                onClick={fillPages}
              >
                <Sparkles size={16} /> Fit to {market.pageLabel}
              </button>
              <button
                className="text-button"
                disabled={!!busy || dirty}
                onClick={async () => {
                  setBusy("Syncing reviewed profile and ranking the signature and supporting projects…");
                  setError("");
                  try {
                    const d = await api<Draft>(base + "/sync-profile", "POST", {
                      revision: draft.revision,
                    });
                    setDraft(d);
                    setSource(d.source);
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy("");
                  }
                }}
              >
                <RefreshCw size={16} /> Sync profile & rank projects
              </button>
              <button
                className="text-button"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(source);
                    setCopyMessage("Template copied");
                  } catch {
                    setCopyMessage(
                      "Copy unavailable. Download the source instead.",
                    );
                  }
                }}
              >
                <Copy size={16} /> Copy LaTeX template
              </button>
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={auto}
                  onChange={(e) => setAuto(e.target.checked)}
                />
                Auto-save & refresh preview
              </label>
            </div>
          </details>
        </div>
      </div>
      {copyMessage && <p role="status">{copyMessage}</p>}
      {error && (
        <div className="callout warning" role="alert">
          <div>
            <b>Needs attention</b>
            <p className="jd-text">{error}</p>
            <button className="secondary" disabled={!!busy} onClick={open}>
              Reload saved draft
            </button>
          </div>
        </div>
      )}
      {ask && (
        // Wording changes through the assistant show a before/after for confirmation.
        <div className="studio-ask">
          <p>
            Want a bullet reworded, a skill moved or a project swapped? Ask in the chat: it shows the
            change first and applies it only after your yes.
          </p>
          <button className="secondary" onClick={() => ask(`Change the ${company} resume: `, false)}>
            <MessageSquareText size={16} /> Ask the assistant to change this resume
          </button>
        </div>
      )}
      <AgentControl
        jobId={jobId}
        revision={draft.revision}
        locked={!!busy || dirty}
        onBuilding={(active) => setBusy(active ? "Updating the resume, preview and score…" : "")}
        onChanged={reloadDraft}
      />
      <div className="studio-split">
        <section className="studio-panel">
          <div className="studio-panel-heading">
            <h2>
              <FileText size={18} /> Edit resume
            </h2>
            <div className="segmented">
              <button
                className={mode === "content" ? "selected" : ""}
                onClick={() => setMode("content")}
              >
                Content
              </button>
              <button
                className={mode === "projects" ? "selected" : ""}
                onClick={() => setMode("projects")}
              >
                Projects
              </button>
              <button
                className={mode === "source" ? "selected" : ""}
                onClick={() => setMode("source")}
              >
                LaTeX source
              </button>
            </div>
          </div>
          <div className="studio-editor-body">
            <fieldset className="studio-edit-fields" disabled={!!busy}>
              {mode === "source" ? (
                <>
                  <div className="studio-help">
                    <b>Advanced editing</b>
                    <span>
                      Edit every section and the layout. Keep the named fields
                      so additions can still be tracked.
                    </span>
                  </div>
                  <textarea
                    className="source-editor"
                    aria-label="LaTeX resume source"
                    spellCheck={false}
                    value={source}
                    maxLength={150000}
                    onChange={(e) => edit(e.target.value)}
                  />
                </>
              ) : mode === "projects" ? (
                <>
                  <div className="studio-help">
                    <b>One lead project, then one supporting project</b>
                    <span>
                      Both projects must be distinct, registered evidence. They are
                      selected automatically for the role, and you can replace either.
                    </span>
                  </div>
                  <div className="project-picker-grid">
                    <Field label="Replace first project">
                      <select
                        value=""
                        disabled={!!busy || dirty}
                        onChange={(e) =>
                          save(true, { project_id: e.target.value })
                        }
                      >
                        <option value="">Choose from Profile…</option>
                        {draft.projects.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.title}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Replace second project">
                      <select
                        value=""
                        disabled={!!busy || dirty}
                        onChange={(e) =>
                          save(true, { second_project_id: e.target.value })
                        }
                      >
                        <option value="">Choose from Profile…</option>
                        {draft.projects.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.title}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  <button
                    className="secondary"
                    disabled={!!busy}
                    onClick={() => setAddingProject(true)}
                  >
                    ＋ Add my own project
                  </button>
                  <details className="studio-inline-details">
                    <summary>See ranked project library</summary>
                    <ol>
                      {draft.project_library.map((p) => (
                        <li key={p.id}>
                          <b>
                            {p.rank ? `#${p.rank} ` : ""}
                            {p.title}
                          </b>
                          <br />
                          <small>
                            {p.id} ·{" "}
                            {p.eligible
                              ? "Ready to select"
                              : p.review_state + " · evidence review needed"}
                            {p.rank && p.reason?.length ? (
                              <>
                                <br />
                                Ranked for this role on: {p.reason.slice(0, 6).join(", ")}
                              </>
                            ) : null}
                          </small>
                        </li>
                      ))}
                    </ol>
                    <p className="small">
                      Ranks reflect job-description overlap, not hiring
                      probability. The signature and supporting projects must be distinct.
                    </p>
                  </details>
                  {projectFields.map(
                    (name) =>
                      macroSpan(source, name) && (
                        <Field label={labels[name]} key={name}>
                          <textarea
                            rows={
                              name === "ResumeSummary"
                                ? 7
                                : name.includes("Bullet")
                                  ? 4
                                  : 3
                            }
                            value={readField(source, name)}
                            onChange={(e) =>
                              edit(writeField(source, name, e.target.value))
                            }
                          />
                        </Field>
                      ),
                  )}
                  <p className="small">
                    New projects and skills are captured in Profile for evidence
                    review when you save.
                  </p>
                </>
              ) : (
                <>
                  <div className="studio-help">
                    <b>Lead with the skills this role names</b>
                    <span>
                      These resumes carry no summary: the skills block does
                      that job. Put the strongest matching skills first in each
                      category. Only skills already in your profile belong here.
                    </span>
                  </div>
                  {contentFields.map(
                    (name) =>
                      macroSpan(source, name) && (
                        <Field label={labels[name]} key={name}>
                          <textarea
                            rows={name === "ResumeSummary" ? 8 : 6}
                            value={readField(source, name)}
                            onChange={(e) =>
                              edit(writeField(source, name, e.target.value))
                            }
                          />
                        </Field>
                      ),
                  )}
                  <button
                    className="secondary"
                    onClick={() => setMode("projects")}
                  >
                    Continue to projects →
                  </button>
                </>
              )}
            </fieldset>
          </div>
        </section>
        <section className="studio-panel preview-panel">
          <div className="studio-panel-heading">
            <h2>Resume preview</h2>
            <Badge tone={current ? "green" : "amber"}>
              {current ? "Current draft" : "Preview needs updating"}
            </Badge>
          </div>
          <div className="studio-preview-body">
            {draft.preview ? (
              <>
                <p className="preview-caption">
                  Version {draft.preview.revision} · {draft.preview.page_count}{" "}
                  {draft.preview.page_count === 1 ? "page" : "pages"} ·{" "}
                  {draft.preview.page_count === market.pages
                    ? `${market.resumeShape} target met`
                    : `Use Fit to ${market.pageLabel} (it keeps type within ${market.minBodyPt}–${market.maxBodyPt}pt)`}
                </p>
                {draft.preview.layout && (
                  <div className="layout-meter">
                    <Badge
                      tone={
                        current && draft.preview.layout.full_pages
                          ? "green"
                          : "amber"
                      }
                    >
                      {draft.preview.layout.full_pages
                        ? "One full page"
                        : "Page fill needs attention"}
                    </Badge>
                    <p>
                      {draft.preview.layout.pages
                        .map((p) => `Page ${p.page}: ${p.fill_percent}% filled`)
                        .join(" · ")}
                      {draft.preview.body_font_pt
                        ? ` · ${draft.preview.body_font_pt}pt body`
                        : ""}
                    </p>
                    <small>
                      Measured within normal margins. Content and evidence still
                      need review.
                    </small>
                  </div>
                )}
                {draft.preview.ranking && (
                  <details className="layout-ranking">
                    <summary>Content order and additions</summary>
                    <p>{draft.preview.ranking.section_order.join(" → ")}</p>
                    <ul>
                      {draft.preview.ranking.changes.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {!current && (
                  <p className="callout warning">
                    Showing the last successful preview. Your newest edits are
                    not shown yet.
                  </p>
                )}
                {Array.from({ length: draft.preview.page_count }, (_, i) => (
                  <img
                    key={i}
                    className="resume-page"
                    alt={company + " resume preview, page " + (i + 1)}
                    src={fileUrl(
                      draft.preview!.path +
                        "/page-" +
                        String(i + 1).padStart(2, "0") +
                        ".png",
                    )}
                  />
                ))}
              </>
            ) : (
              <div className="studio-empty">
                <FileText size={42} />
                <h3>Your preview will appear here</h3>
                <p>Save and preview to compile your resume.</p>
              </div>
            )}
          </div>
        </section>
      </div>
      {addingProject && (
        <NewProject
          onClose={() => setAddingProject(false)}
          onAdd={(values) => {
            let next = source;
            const names = [
              "SelectedProjectID",
              "SelectedProjectTitle",
              "SelectedProjectContext",
              "SelectedProjectBulletOne",
              "SelectedProjectBulletTwo",
              "SelectedProjectBulletThree",
            ];
            for (const name of names) {
              if (!macroSpan(next, name))
                next = next.replace(
                  "\\begin{document}",
                  "\\newcommand{\\" + name + "}{}\n\\begin{document}",
                );
              next = writeField(next, name, values[name] || "");
            }
            const start = next.indexOf("% SELECTED_PROJECT_BLOCK_START"),
              end = next.indexOf("% SELECTED_PROJECT_BLOCK_END");
            if (start < 0 || end < start) {
              setError(
                "Restore the selected-project block markers in LaTeX source before adding a project.",
              );
              return;
            }
            const block =
              "% SELECTED_PROJECT_BLOCK_START\n\\textbf{\\SelectedProjectTitle} \\hfill \\textit{\\SelectedProjectContext}\n\\begin{resumeitems}\n" +
              ["One", "Two", "Three"]
                .filter((n) => values["SelectedProjectBullet" + n])
                .map((n) => "\\item \\SelectedProjectBullet" + n)
                .join("\n") +
              "\n\\end{resumeitems}\n";
            next = next.slice(0, start) + block + next.slice(end);
            if (values.skills) {
              const target = macroSpan(next, "CoreSkills") ? "CoreSkills" : "SkillsLanguages";
              const sep = target === "CoreSkills" ? "; " : ", ";
              next = writeField(next, target, readField(next, target) + sep + values.skills);
            }
            edit(next);
            setAddingProject(false);
            setMode("projects");
          }}
        />
      )}
      <details className="studio-advanced">
        <summary>
          <span>
            <b>Review details & advanced tools</b>
            <small>
              Match breakdown, study plan, Profile captures and version
              history
            </small>
          </span>
        </summary>
        <div className="studio-advanced-body">
          {draft.match && (
            <section className="card">
              <div className="section-head">
                <div>
                  <div className="eyebrow">
                    INDEPENDENT MATCHER · PDF + JD ONLY
                  </div>
                  <h2>
                    Resume coverage:{" "}
                    {draft.match.score === null
                      ? "Not scored"
                      : `${draft.match.score}/100`}
                  </h2>
                </div>
                <Badge tone={draft.match.current && !dirty ? "green" : "amber"}>
                  {draft.match.current && !dirty
                    ? "Current PDF"
                    : "Stale · rebuild"}
                </Badge>
              </div>
              <p>
                Version {draft.match.revision} · no profile access · zero AI
                calls.{" "}
                {(draft.match.missing_supported || []).length
                  ? "Supported by your evidence but absent from the PDF: " + (draft.match.missing_supported || []).join(", ") + ". "
                  : ""}
                {(draft.match.missing_unsupported || []).length
                  ? "Unsupported by your evidence, do not add: " + (draft.match.missing_unsupported || []).join(", ") + "."
                  : ""}
                {!(draft.match.missing_supported || []).length &&
                !(draft.match.missing_unsupported || []).length
                  ? "No grounded requirement is missing from the PDF."
                  : ""}
              </p>
              <details>
                <summary>Evidence and scoring limits</summary>
                {draft.match.limitations.map((l) => (
                  <p className="small" key={l}>
                    {l}
                  </p>
                ))}
              </details>
            </section>
          )}
          <div className="studio-agents">
            <section className="card">
              <div className="section-head">
                <div>
                  <div className="eyebrow">INDEPENDENT RESUME ADVISOR</div>
                  <h2>What this company wants to see</h2>
                </div>
                <Badge>No profile access</Badge>
              </div>
              <p>
                The advisor reads this posting and public company research. Any project ideas
                stay as suggestions until you build them and add supporting evidence.
              </p>
              <button
                className="secondary"
                disabled={advisorActive}
                onClick={async () => {
                  try {
                    await api("/v2/agents/run", "POST", { kind: "resume_advisor", job_id: jobId });
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                <RefreshCw size={15} />
                {advisorActive ? "Research in progress…" : advisorRun ? "Refresh advice" : "Research this role"}
              </button>
              {advisorRun && <Running run={advisorRun} />}
              {advisorRun?.result?.advice && <ReportView report={advisorRun.result.advice} />}
              {advisorRun?.result?.research && (
                <details className="report-section">
                  <summary>Company research and sources</summary>
                  <ReportView report={advisorRun.result.research} />
                </details>
              )}
            </section>
            <section className="card">
              <div className="eyebrow">FIGHT 2 · WIN THE INTERVIEW</div>
              <h2>Study plan for this company</h2>
              <p>
                What they will probe and what to learn in the 3–6 weeks before
                they call. Everything in it is a skill you do not have yet: it
                never goes on the resume until you have learned it and it is
                written into your context files.
              </p>
              <button
                className="secondary"
                disabled={studyActive}
                onClick={async () => {
                  try {
                    await api("/v2/agents/run", "POST", {
                      kind: "study_plan",
                      job_id: jobId,
                    });
                    await refresh();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                <RefreshCw size={15} />
                {studyActive
                  ? "Writing the study plan…"
                  : studyRun
                    ? "Write study plan again"
                    : "Write study plan"}
              </button>
              {studyRun && <Running run={studyRun} />}
              {studyRun?.result?.plan && <ReportView report={studyRun.result.plan} />}
              {studyRun?.result?.path && (
                <p className="small muted">Saved as {studyRun.result.path}</p>
              )}
            </section>
            <section className="card">
              <div className="eyebrow">VERSIONS & PROFILE CAPTURES</div>
              <h2>Keep your experience with you</h2>
              <p>
                Runs on each save. Tracks project fields and skills in this
                template, keeps version history and captures new entries in
                Profile.
              </p>
              {draft.warnings.map((w) => (
                <p className="tracker-note" key={w}>
                  {w}
                </p>
              ))}
              <h3>Captured in Profile</h3>
              {draft.captures.length ? (
                <ul>
                  {draft.captures.map((c) => (
                    <li key={c.id + c.title}>
                      {c.title}{" "}
                      <Badge>
                        {c.deleted
                          ? "Removed from Profile"
                          : "Saved for evidence review"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  New projects and skills you add will appear here.
                </p>
              )}
              <h3>Version history</h3>
              <Field label="Restore an earlier version">
                <select
                  value={restoring}
                  onChange={(e) => setRestoring(e.target.value)}
                >
                  <option value="">Choose a saved version…</option>
                  {draft.versions.map((v) => (
                    <option key={v.revision} value={v.revision}>
                      Version {v.revision} ·{" "}
                      {new Date(v.created_at).toLocaleString()}
                    </option>
                  ))}
                </select>
              </Field>
              <button
                className="secondary"
                disabled={!!busy || dirty || !restoring}
                onClick={() =>
                  save(true, { restore_revision: Number(restoring) })
                }
              >
                Restore as a new version
              </button>
              <p className="small">
                Save your current edits first. Restoring a resume keeps its
                history and previously captured Profile entries.
              </p>
            </section>
          </div>
        </div>
      </details>
        </>
      )}
    </>
  );
}

function AnalyseRole({
  job,
  run,
  onRun,
  onNext,
}: {
  job: Job;
  run?: Run;
  onRun: () => Promise<void>;
  onNext: () => void;
}) {
  const running = !!run && ["queued", "running"].includes(run.state);
  return (
    <section className="studio-stage" aria-labelledby="analyse-role-title">
      <div className="studio-stage-head">
        <div>
          <div className="eyebrow">STEP 1 · UNDERSTAND THE DECISION</div>
          <h2 id="analyse-role-title">What is {job.company} hiring for?</h2>
          <p>
            Review the saved posting, public company research and independent
            hiring-manager expectations before changing your resume.
          </p>
        </div>
        <button className="primary" disabled={running} onClick={() => void onRun()}>
          <Search size={16} />
          {running ? "Analysis in progress…" : run ? "Refresh analysis" : "Analyse role"}
        </button>
      </div>
      <div className="analysis-grid">
        <article className="card job-description-card">
          <div className="eyebrow">SAVED JOB DESCRIPTION</div>
          <h3>{job.title}</h3>
          <p className="small">
            {job.location || "Location not stated"} · saved {job.created_at.slice(0, 10)}
          </p>
          <div className="jd-text analysis-scroll">{job.description}</div>
        </article>
        <div className="analysis-results">
          {!run ? (
            <div className="studio-empty compact">
              <Search size={36} />
              <h3>Run the role analysis</h3>
              <p>
                You’ll get sourced company context and hiring-manager comments.
              </p>
            </div>
          ) : (
            <>
              <Running run={run} />
              {run.error && <p className="callout warning">{run.error}</p>}
              {run.result?.research && (
                <details className="report-section" open>
                  <summary>Company & role analysis</summary>
                  <ReportView report={run.result.research} />
                </details>
              )}
              {run.result?.hiring && (
                <details className="report-section" open>
                  <summary>
                    Hiring-manager comments <Badge tone="green">No profile access</Badge>
                  </summary>
                  <ReportView report={run.result.hiring} />
                </details>
              )}
              {run.result?.comparison && (
                <details className="report-section">
                  <summary>Your evidence comparison</summary>
                  <ReportView report={run.result.comparison} />
                </details>
              )}
            </>
          )}
        </div>
      </div>
      <div className="studio-stage-footer">
        <p className="small">
          Hiring analysis is isolated from your profile. It receives only the
          posting and public company research.
        </p>
        <button className="primary" onClick={onNext}>
          Check my current resume <ArrowRight size={16} />
        </button>
      </div>
    </section>
  );
}

function MatchCheck({
  jobId,
  draft,
  dirty,
  run,
  review,
  onBuild,
  onReview,
  onNext,
}: {
  jobId: string;
  draft: Draft;
  dirty: boolean;
  run?: Run;
  /** The latest independent review of this job's PDF (a resume_match run). */
  review?: Run;
  onBuild: () => Promise<void>;
  onReview: () => Promise<void>;
  onNext: () => void;
}) {
  const match = draft.match;
  const current = !!match?.current && !dirty;
  const running = !!run && ["queued", "running"].includes(run.state);
  const reviewing = !!review && ["queued", "running"].includes(review.state);
  return (
    <section className="studio-stage" aria-labelledby="match-title">
      <div className="studio-stage-head">
        <div>
          <div className="eyebrow">STEP 2 · CHECK THE CURRENT PDF</div>
          <h2 id="match-title">Current resume match</h2>
          <p>
            Build the PDF, then compare its supported wording with the saved job
            description.
          </p>
        </div>
        <button className="primary" disabled={running} onClick={() => void onBuild()}>
          <RefreshCw size={16} className={running ? "spin" : ""} />
          {running ? "Building & scoring…" : match ? "Rebuild & score" : "Build & score"}
        </button>
      </div>
      {run && <Running run={run} />}
      <div className="match-overview">
        <div className={`match-score ${current ? "current" : ""}`}>
          <strong>{match?.ats_readiness?.score ?? "—"}</strong>
          <span>/ 100</span>
          <small>ATS readiness</small>
        </div>
        <div className={`match-score ${current ? "current" : ""}`}>
          <strong>{match?.resume_coverage?.score ?? match?.score ?? "—"}</strong>
          <span>/ 100</span>
          <small>Resume coverage</small>
        </div>
        <div className={`match-score ${current ? "current" : ""}`}>
          <strong>{match?.opportunity_fit?.label ?? "—"}</strong>
          <small>Opportunity fit</small>
        </div>
        <div className="match-summary">
          <h3>Three separate checks</h3>
          {!match && <p>Build the current draft to calculate coverage.</p>}
          {match && !(match.missing_supported || []).length && !(match.missing_unsupported || []).length && (
            <p>No grounded requirement is missing from the PDF.</p>
          )}
          {!!(match?.missing_supported || []).length && (
            <p>
              <b>Safe to add</b> — your evidence supports these, the PDF does
              not mention them: {(match?.missing_supported || []).join(", ")}.
            </p>
          )}
          {!!(match?.missing_unsupported || []).length && (
            <p>
              <b>Genuine gaps</b> — nothing in your registered evidence supports
              these, so do not add them:{" "}
              {(match?.missing_unsupported || []).join(", ")}.
            </p>
          )}
          <p className="small">
            ATS readiness checks machine readability. Resume coverage compares
            the PDF with grounded JD excerpts. Opportunity fit uses candidate
            evidence and hard blockers. None is an interview probability.
          </p>
        </div>
      </div>
      {match?.keywords?.length ? (
        <div className="requirements-list">
          <h3>Grounded keywords and gaps</h3>
          {match.keywords.map((requirement) => (
            <details key={requirement.category + requirement.requirement}>
              <summary>
                <Badge tone={requirement.status === "found_in_pdf" ? "green" : requirement.status === "supported_missing_from_pdf" ? "amber" : "neutral"}>
                  {requirement.status.replaceAll("_", " ")}
                </Badge>{" "}
                {requirement.requirement}
              </summary>
              <p><b>{requirement.category} excerpt:</b> {requirement.excerpt}</p>
            </details>
          ))}
        </div>
      ) : null}
      <FitCheck jobId={jobId} />
      <div className="card studio-review">
        <div className="section-head">
          <div>
            <div className="eyebrow">INDEPENDENT REVIEW · PDF + JOB DESCRIPTION ONLY</div>
            <h3>A second opinion on this PDF</h3>
            <p className="small">
              An AI reads only the built PDF's text and the posting, never your profile, and says what a
              reader would miss. It is tied to the PDF version saved when it started.
            </p>
          </div>
          <button
            className="secondary"
            disabled={reviewing || running || !current}
            title={current ? "Review the current PDF" : "Build the current draft first"}
            onClick={() => void onReview()}
          >
            <Search size={16} />
            {reviewing ? "Reviewing…" : review ? "Review again" : "Review the PDF"}
          </button>
        </div>
        {review && <Running run={review} />}
        {review?.result?.review && <ReportView report={review.result.review} />}
      </div>
      <div className="studio-stage-footer">
        <p className="small">
          Next, edit the wording or ask the assistant for changes, and watch the
          preview and score update.
        </p>
        <button className="primary" onClick={onNext}>
          Improve this resume <ArrowRight size={16} />
        </button>
      </div>
    </section>
  );
}

function NewProject({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (values: Record<string, string>) => void;
}) {
  const [form, setForm] = useState<Record<string, string>>({
    SelectedProjectID: "USER-PROJECT",
    SelectedProjectTitle: "",
    SelectedProjectContext: "",
    SelectedProjectBulletOne: "",
    SelectedProjectBulletTwo: "",
    SelectedProjectBulletThree: "",
    skills: "",
  });
  return (
    <Modal title="Add your project" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onAdd(form);
        }}
      >
        <p>
          This replaces the selected resume project. Your other projects stay in
          Profile. Describe work you have actually done; proposed company
          projects stay in the advisor.
        </p>
        {[
          "SelectedProjectTitle",
          "SelectedProjectContext",
          "SelectedProjectBulletOne",
          "SelectedProjectBulletTwo",
          "SelectedProjectBulletThree",
          "skills",
        ].map((name) => (
          <Field
            key={name}
            label={
              name === "skills"
                ? "New skills used · optional, separate with semicolons"
                : labels[name] +
                  (name === "SelectedProjectBulletThree" ? " · optional" : "")
            }
          >
            <textarea
              required={
                !["SelectedProjectBulletThree", "skills"].includes(name)
              }
              rows={3}
              maxLength={name === "SelectedProjectTitle" ? 250 : 2000}
              value={form[name]}
              onChange={(e) => setForm({ ...form, [name]: e.target.value })}
            />
          </Field>
        ))}
        <button className="primary">Add to resume</button>
        <p className="small">
          On save, Agent 2 also captures this project and any new skills in
          Profile for evidence review.
        </p>
      </form>
    </Modal>
  );
}
