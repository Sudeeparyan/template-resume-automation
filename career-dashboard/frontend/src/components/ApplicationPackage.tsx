import { useEffect, useState } from "react";
import { CheckCircle2, Copy, Download, ExternalLink, Mail, Send } from "lucide-react";
import { API_BASE, api, fileUrl, safeUrl } from "../api";
import { Badge, Field } from "./UI";
import type { Job, Summary } from "../types";

export function applicationEmail(job: Job) {
  return {
    subject: `Application: ${job.title} at ${job.company}`,
    body: `Dear ${job.company} hiring team,\n\nI am applying for the ${job.title} role. I have attached my tailored resume for your review.\n\nThank you for your consideration.\n`,
  };
}

export default function ApplicationPackage({ job, data, refresh, notify }: {
  job: Job;
  data: Summary;
  refresh: () => Promise<void>;
  notify: (text: string, error?: boolean) => void;
}) {
  const documents = data.documents.find((item) => item.job_id === job.id);
  const reviewRun = data.runs.find((run) => run.kind === "resume_match" && run.job_id === job.id && run.state === "completed");
  const review = reviewRun?.result?.review;
  const initial = applicationEmail(job);
  const [recipient, setRecipient] = useState("");
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);
  const [busy, setBusy] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [confirmApplied, setConfirmApplied] = useState(false);
  const [emailOpen, setEmailOpen] = useState(false);

  useEffect(() => {
    if (reviewRun) setPreparing(false);
  }, [reviewRun]);

  async function prepare() {
    setBusy("prepare");
    try {
      await api(`/v2/jobs/${job.id}/prepare-application`, "POST", {
        count: 1, source: "default", steps: { cover_letter: true },
      });
      setPreparing(true);
      notify("Application preparation started. The existing pipeline will check, tailor, compile and review it.");
      await refresh();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  async function generateLetter() {
    setBusy("letter");
    try {
      await api(`/v2/jobs/${job.id}/cover-letter`, "POST");
      await refresh();
      notify("Cover letter generated. Read it before using it.");
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  async function markApplied() {
    if (!confirmApplied) {
      setConfirmApplied(true);
      return;
    }
    setBusy("applied");
    try {
      await api("/jobs/" + job.id, "PATCH", {
        status: "applied", application_date: job.application_date || data.goals.date, notes: job.notes,
      });
      setConfirmApplied(false);
      await refresh();
      notify("Application recorded on your confirmation. Nothing was submitted by the app.");
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  const emailText = `To: ${recipient}\nSubject: ${subject}\n\n${body}`;
  const mailto = `mailto:${encodeURIComponent(recipient)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  return (
    <section className="card application-package" aria-label="Application package">
      <div className="section-title">
        <div><h2>Application package</h2><p className="muted">Review each item, then apply through the posting yourself.</p></div>
        <Badge tone={review?.verdict === "pass" ? "green" : "amber"}>
          {review?.verdict === "pass" ? "Independent review passed" : "Review needed"}
        </Badge>
      </div>
      <ul className="application-checklist">
        <li>Posting: {(job.posting_state || "active").replaceAll("_", " ")}</li>
        <li>Fit: {job.fit_score == null ? "not checked yet" : `${job.fit_score}/100`}</li>
        <li>Files: {documents?.resumes.length ? `${documents.resumes.length} resume file${documents.resumes.length === 1 ? "" : "s"}` : "not generated yet"}</li>
        <li>Review: {review?.summary || "Run preparation to create the current independent review."}</li>
      </ul>
      {job.fit_rationale && <p className="small"><b>Fit and gaps:</b> {job.fit_rationale}</p>}
      <div className="actions">
        {["saved", "prepared"].includes(job.status) && <button className="primary" disabled={!!busy || preparing} onClick={() => void prepare()}>
          {busy === "prepare" || preparing ? "Preparing…" : documents ? "Refresh application" : "Prepare application"}
        </button>}
        <a className="secondary" href={safeUrl(job.url)} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Open posting / apply</a>
        {documents?.resumes.map((resume) => <a className="secondary" key={resume.path} href={fileUrl(resume.path)} target="_blank" rel="noreferrer"><Download size={15} /> {resume.label} PDF</a>)}
        {documents?.resumes.length ? <a className="secondary" href={`${API_BASE}/v2/studio/${job.id}/download?format=docx`}><Download size={15} /> Word resume</a> : null}
        {documents?.cover_letter ? <>
          <a className="secondary" href={fileUrl(documents.cover_letter.path)} target="_blank" rel="noreferrer">Cover letter</a>
          <a className="secondary" href={`${API_BASE}/v2/jobs/${job.id}/cover-letter/download?format=docx`}>Word letter</a>
        </> : documents?.resumes.length ? <button className="secondary" disabled={!!busy} onClick={() => void generateLetter()}>{busy === "letter" ? "Writing…" : "Generate cover letter"}</button> : null}
      </div>
      {documents?.resumes.length ? <div className="actions application-email-actions">
        <button className="secondary" onClick={() => setEmailOpen(!emailOpen)}><Mail size={15} /> Application email</button>
        <button className="secondary" onClick={() => { void navigator.clipboard?.writeText(emailText); notify("Email text copied. Attach the files yourself."); }}><Copy size={15} /> Copy email</button>
        <button className="secondary" onClick={() => { window.location.href = mailto; }}><Send size={15} /> Open in mail app</button>
      </div> : null}
      {emailOpen && <div className="application-email-editor">
        <Field label="Recipient (optional; check the posting)"><input type="email" value={recipient} onChange={(event) => setRecipient(event.target.value)} /></Field>
        <Field label="Subject"><input value={subject} onChange={(event) => setSubject(event.target.value)} /></Field>
        <Field label="Email draft"><textarea rows={7} value={body} onChange={(event) => setBody(event.target.value)} /></Field>
        <p className="small muted">Opening a draft does not send it. Attach the resume and letter yourself.</p>
      </div>}
      {!job.application_date && !["applied", "interview", "offer"].includes(job.status) && <div className="actions application-confirm">
        <button className={confirmApplied ? "primary" : "secondary"} disabled={!!busy} onClick={() => void markApplied()}>
          <CheckCircle2 size={15} /> {confirmApplied ? "Confirm: I submitted this application" : "Mark as applied"}
        </button>
        {confirmApplied && <button className="secondary" onClick={() => setConfirmApplied(false)}>Cancel</button>}
        <small className="muted">Uses {job.application_date || data.goals.date} as the application date.</small>
      </div>}
    </section>
  );
}
