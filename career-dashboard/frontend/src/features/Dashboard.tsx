import { useState } from "react";
import {
  Mail as MailIcon,
  RefreshCw,
  ArrowRight,
  Target,
  CalendarCheck,
  BriefcaseBusiness,
  MessageCircle,
  Settings2,
  FileText,
  RotateCcw,
} from "lucide-react";
import { api, fileUrl, safeUrl } from "../api";
import { AskAssistant, Badge, Empty, Modal, Field, Running } from "../components/UI";
import { JobList } from "../components/JobList";
import { PlanEditor, WeekCard } from "../components/WeekPlan";
import type { Summary, Mail, Job, CoverLetter, ExcludedJob } from "../types";
type Props = {
  data: Summary;
  refresh: () => Promise<void>;
  notify: (text: string, error?: boolean) => void;
  onJob: (id: string) => void;
  onDaily: () => void;
  onAdd: () => void;
};
export default function Dashboard({
  data,
  refresh,
  notify,
  onJob,
  onDaily,
  onAdd,
}: Props) {
  const [mail, setMail] = useState<Mail | null>(null);
  const [section, setSection] = useState("applications");
  const [busy, setBusy] = useState(false);
  const [schedule, setSchedule] = useState<any>(null);
  const [coverLetter, setCoverLetter] = useState<CoverLetter | null>(null);
  const [editingPlan, setEditingPlan] = useState(false);
  const running = data.runs.find(
    (r) => r.kind === "email" && ["queued", "running"].includes(r.state),
  );
  const latestMailRun = data.runs.find((r) => r.kind === "email");
  const mailNeedsAttention =
    data.mail.connection.status === "needs_attention" ||
    latestMailRun?.state === "failed";
  const pending = data.mail.messages.filter((m) => m.state === "pending");
  async function sync() {
    setBusy(true);
    try {
      await api("/v2/agents/run", "POST", { kind: "email" });
      await refresh();
      notify("Gmail sync started. Only job-related messages are read.");
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  async function restoreExcluded(job: ExcludedJob) {
    try {
      const result = await api("/v2/excluded/" + job.id + "/restore", "POST");
      await refresh();
      notify(
        result.blocked
          ? "Not restored: " + result.note
          : "Restored. It is now a normal job (tier C) in your list.",
        !!result.blocked,
      );
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  async function generateCoverLetter(job: Job) {
    setBusy(true);
    try {
      const letter = await api<CoverLetter>(
        "/v2/jobs/" + job.id + "/cover-letter",
        "POST",
      );
      setCoverLetter(letter);
      await refresh();
      notify(
        `Cover letter generated for ${job.company}. Review it before sending.`,
      );
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  async function removeJob(job: Job) {
    if (
      !window.confirm(
        `Remove ${job.company} · ${job.title} from your active job list? Its history and files will be kept.`,
      )
    )
      return;
    try {
      await api("/v2/jobs/" + job.id, "DELETE", {
        reason: "Marked not suitable by user",
      });
      await refresh();
      notify("Unsuitable role removed. You can restore it from Removed roles.");
    } catch (e) {
      notify((e as Error).message, true);
    }
  }
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">YOUR APPLICATIONS, IN ONE PLACE</div>
          <h1>Dashboard</h1>
          <p>Small steps today. More opportunities tomorrow.</p>
        </div>
        <div className="actions">
          <AskAssistant
            prompts={[
              { label: "What should I do today?", text: "What should I do today? Look at my goals, my saved jobs and anything waiting on me, and give me the next steps in order." },
              { label: "What did I apply to this week?", text: "What did I apply to this week, and which resumes are still waiting to be sent?" },
              { label: "Anything to follow up on?", text: "Which applications have gone quiet or need a follow-up, and are any emails waiting for me?" },
            ]}
          />
          <button className="secondary" onClick={() => setEditingPlan(true)}>
            <Settings2 size={17} /> Edit plan
          </button>
          <button className="primary" onClick={onAdd}>
            ＋ Save a job
          </button>
        </div>
      </div>
      <div className="metrics">
        {[
          [BriefcaseBusiness, data.counts.applied, "Applications recorded"],
          [MessageCircle, data.counts.interviews, "Interview stage"],
          [CalendarCheck, data.counts.offers, "Offers"],
          [Target, data.goals.remaining_today, "Left for today"],
        ].map(([Icon, value, label]: any) => (
          <section className="card metric" key={label}>
            <span className="metric-icon">
              <Icon size={20} />
            </span>
            <strong>{value}</strong>
            <span>{label}</span>
          </section>
        ))}
      </div>
      <div className="dashboard-top">
        <section className="focus-card">
          <div>
            <Badge tone="lime">TODAY’S FOCUS</Badge>
            <h2>
              {data.goals.remaining_today
                ? `${data.goals.remaining_today} applications to move forward.`
                : "Your daily target is complete."}
            </h2>
            <p>
              {data.goals.carryover
                ? `${data.goals.daily_base} planned + ${data.goals.carryover} carried forward. Your unfinished work stays with you.`
                : "Your progress starts with one well-matched role."}
            </p>
            <button onClick={onDaily}>
              Open Daily Search <ArrowRight size={17} />
            </button>
          </div>
          <div className="progress-dial">
            <strong>
              {data.goals.today_completed}
              <small>/{data.goals.today_target}</small>
            </strong>
            <span>today</span>
          </div>
        </section>
        {data.mail.available === false ? (
        <section className="card email-card">
          <div className="section-title">
            <h2>
              <MailIcon size={20} /> Application emails
            </h2>
            <Badge>Not for this profile</Badge>
          </div>
          <p>{data.mail.note}</p>
        </section>
        ) : (
        <section className="card email-card">
          <div className="section-title">
            <h2>
              <MailIcon size={20} /> Gmail connection
            </h2>
            <Badge tone={data.mail.connection.connected ? "green" : "amber"}>
              {data.mail.connection.connected
                ? "Ready"
                : mailNeedsAttention
                  ? "Needs connection"
                  : "Not connected"}
            </Badge>
          </div>
          <strong>
            {data.mail.connection.email || "Use your connected Gmail account"}
          </strong>
          <p>
            {data.mail.connection.last_synced_at
              ? "Last synced " +
                new Date(data.mail.connection.last_synced_at).toLocaleString(
                  "en-US",
                )
              : "Connect Gmail, then scan your job-related mail history for applications and status updates."}
          </p>
          <div className="actions">
            <button
              className="secondary"
              disabled={busy || !!running}
              onClick={sync}
            >
              <RefreshCw size={16} className={running ? "spin" : ""} />
              {running
                ? "Checking Gmail…"
                : mailNeedsAttention
                  ? "Retry Gmail sync"
                  : "Sync Gmail"}
            </button>
            <button
              className="icon-button"
              aria-label="Email sync schedule"
              onClick={async () => {
                try {
                  setSchedule(await api("/v2/email-schedule"));
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            >
              <Settings2 size={18} />
            </button>
          </div>
          {data.mail.connection.coverage && (
            <details>
              <summary>Sync coverage</summary>
              <p>{data.mail.connection.coverage}</p>
            </details>
          )}
          {mailNeedsAttention && !running && (
            <div className="mail-sync-alert" role="alert">
              <b>Gmail needs attention</b>
              <p>
                {data.mail.connection.last_error ||
                  latestMailRun?.error ||
                  "Gmail sync is optional. Sign in to Codex on this machine and save its Gmail connector id in Settings, then retry. Your saved email evidence is unchanged."}
              </p>
            </div>
          )}
          {running && (
            <small className="muted">
              This may take a few minutes. You can keep working.
            </small>
          )}
        </section>
        )}
      </div>
      <WeekCard goals={data.goals} />
      {editingPlan && (
        <PlanEditor goals={data.goals} onClose={() => setEditingPlan(false)} onSaved={refresh} notify={notify} />
      )}
      <section className="card workspace-panel">
        <div className="section-title">
          <div className="segmented">
            <button
              className={section === "applications" ? "selected" : ""}
              onClick={() => setSection("applications")}
            >
              Applications <span>{data.jobs.length}</span>
            </button>
            <button
              className={section === "email" ? "selected" : ""}
              onClick={() => setSection("email")}
            >
              Email evidence <span>{pending.length} to review</span>
            </button>
            <button
              className={section === "documents" ? "selected" : ""}
              onClick={() => setSection("documents")}
            >
              Resumes & letters <span>{data.documents.length}</span>
            </button>
            <button
              className={section === "activity" ? "selected" : ""}
              onClick={() => setSection("activity")}
            >
              Activity
            </button>
          </div>
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const result = await api<{ checked: number }>("/v2/jobs/verify-due", "POST");
                await refresh();
                notify("Checked " + result.checked + " due postings.");
              } catch (e) {
                notify((e as Error).message, true);
              } finally {
                setBusy(false);
              }
            }}
          >
            <RefreshCw size={16} /> Check due postings
          </button>
        </div>
        {section === "applications" ? (
          <>
            <JobList
              jobs={data.jobs}
              onSelect={onJob}
              onCoverLetter={generateCoverLetter}
              onRemove={removeJob}
            />
            {!!data.removed_jobs.length && (
              <details className="removed-jobs">
                <summary>Removed roles · {data.removed_jobs.length}</summary>
                {data.removed_jobs.map((job) => (
                  <div className="removed-job" key={job.id}>
                    <span>
                      <b>{job.company}</b> · {job.title}
                      <small>{job.deletion_reason}</small>
                    </span>
                    <button
                      className="secondary"
                      onClick={async () => {
                        try {
                          await api("/v2/jobs/" + job.id + "/restore", "POST");
                          await refresh();
                          notify("Role restored to your active list.");
                        } catch (e) {
                          notify((e as Error).message, true);
                        }
                      }}
                    >
                      <RotateCcw size={15} /> Restore
                    </button>
                  </div>
                ))}
              </details>
            )}
            {!!data.expired_jobs?.length && (
              <details className="removed-jobs">
                <summary>Expired roles · {data.expired_jobs.length}</summary>
                {data.expired_jobs.map((job) => (
                  <div className="removed-job" key={job.id}>
                    <span>
                      <b>{job.company}</b> · {job.title}
                      <small>
                        Closed evidence retained · last checked{" "}
                        {job.last_verified_at
                          ? new Date(job.last_verified_at).toLocaleString("en-US")
                          : "unknown"}
                      </small>
                    </span>
                    <button className="secondary" onClick={() => onJob(job.id)}>
                      View history
                    </button>
                  </div>
                ))}
              </details>
            )}
            <ExcludedRoles jobs={data.excluded_jobs || []} onRestore={restoreExcluded} />
          </>
        ) : section === "email" ? (
          <>
            <p className="muted">
              Exact application confirmations are linked to their original
              email. Reminders and ambiguous matches need review.
            </p>
            {data.mail.messages.length ? (
              <div className="mail-list">
                {data.mail.messages.map((m) => (
                  <button
                    className="mail-row"
                    key={m.id}
                    onClick={() => setMail(m)}
                  >
                    <MailIcon size={18} />
                    <span>
                      <strong>{m.company || m.sender}</strong>
                      <span>{m.subject}</span>
                      <small>
                        {m.received_at.slice(0, 10)} ·{" "}
                        {m.role || "Role needs review"}
                      </small>
                    </span>
                    <Badge
                      tone={
                        m.state === "confirmed"
                          ? "green"
                          : m.kind === "reminder"
                            ? "neutral"
                            : "amber"
                      }
                    >
                      {m.state === "pending" ? m.kind : m.state}
                    </Badge>
                    <ArrowRight size={17} />
                  </button>
                ))}
              </div>
            ) : (
              <Empty title="Your inbox can fill in the gaps">
                Run a Gmail sync to see evidence here.
              </Empty>
            )}
          </>
        ) : section === "documents" ? (
          <>
            <p className="muted">
              Every company’s latest resume and cover letter is collected here.
              Drafts still need your review before sending.
            </p>
            {data.documents.length ? (
              <div className="document-list">
                {data.documents.map((item) => (
                  <div className="document-row" key={item.job_id}>
                    <FileText size={20} />
                    <span>
                      <strong>{item.company}</strong>
                      <small>{item.title}</small>
                    </span>
                    <span className="actions">
                      {item.resumes.map((resume) => (
                        <a
                          className="secondary"
                          key={resume.path}
                          href={fileUrl(resume.path)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {resume.label}
                        </a>
                      ))}
                      {item.cover_letter ? (
                        <a
                          className="secondary"
                          href={fileUrl(item.cover_letter.path)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Cover letter v{item.cover_letter.version}
                        </a>
                      ) : (
                        <button
                          className="secondary"
                          onClick={() => {
                            const job = data.jobs.find(
                              (candidate) => candidate.id === item.job_id,
                            );
                            if (job) generateCoverLetter(job);
                          }}
                        >
                          Generate cover letter
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty title="Your application documents will appear here">
                Prepare a resume or generate a company-specific cover letter.
              </Empty>
            )}
          </>
        ) : (
          <div className="timeline">
            {data.activity.map((e) => (
              <div key={e.id}>
                <span className="timeline-dot" />
                <div>
                  <strong>{e.action.replaceAll("_", " ")}</strong>
                  <small>
                    {new Date(e.occurred_at).toLocaleString("en-US")}
                  </small>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
      {data.runs
        .filter(
          (r, i, runs) =>
            r.state === "failed" &&
            runs.findIndex(
              (x) => x.kind === r.kind && x.job_id === r.job_id,
            ) === i,
        )
        .slice(0, 1)
        .map((r) => (
          <div key={r.id} className="dashboard-failure">
            <Running run={r} />
            <a className="text-button" href="#agents">
              See what happened in Agents →
            </a>
          </div>
        ))}
      {mail && (
        <MailReview
          mail={mail}
          jobs={data.jobs}
          onClose={() => setMail(null)}
          refresh={refresh}
          notify={notify}
          onAdd={onAdd}
        />
      )}
      {schedule && (
        <Modal title="Email sync schedule" onClose={() => setSchedule(null)}>
          <p>
            Syncs use your signed-in Codex access and run while this app is
            open. Only job-related emails are read.
          </p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await api("/v2/email-schedule", "PUT", schedule);
                notify("Email schedule saved.");
                setSchedule(null);
              } catch (e) {
                notify((e as Error).message, true);
              }
            }}
          >
            <label className="check-line">
              <input
                type="checkbox"
                checked={schedule.enabled}
                onChange={(e) =>
                  setSchedule({ ...schedule, enabled: e.target.checked })
                }
              />
              Sync automatically while the app is running
            </label>
            <Field label="Hours between syncs">
              <input
                type="number"
                min="1"
                max="24"
                value={schedule.hours}
                onChange={(e) =>
                  setSchedule({ ...schedule, hours: Number(e.target.value) })
                }
              />
            </Field>
            <button className="primary">Save schedule</button>
          </form>
        </Modal>
      )}
      {coverLetter && (
        <Modal
          title={`${coverLetter.company} · Cover letter`}
          onClose={() => setCoverLetter(null)}
        >
          <div className="callout warning">
            Draft only. Read it against the job description before sending.
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
function MailReview({
  mail,
  jobs,
  onClose,
  refresh,
  notify,
  onAdd,
}: {
  mail: Mail;
  jobs: Job[];
  onClose: () => void;
  refresh: () => Promise<void>;
  notify: Props["notify"];
  onAdd: () => void;
}) {
  const [id, setId] = useState(mail.job_id || "");
  const [busy, setBusy] = useState(false);
  async function resolve(action: string) {
    setBusy(true);
    try {
      await api("/v2/mail/" + mail.id + "/resolve", "POST", {
        job_id: id || null,
        action,
        create_application: action === "confirm" && !id,
      });
      await refresh();
      notify(
        action === "dismiss"
          ? "Email dismissed."
          : "Application updated with linked email evidence.",
      );
      onClose();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Review email evidence" onClose={onClose}>
      <div className="actions">
        <Badge tone="amber">{mail.kind}</Badge>
        <Badge>{mail.state}</Badge>
      </div>
      <h3>{mail.subject}</h3>
      <p>
        {mail.sender}
        <br />
        {new Date(mail.received_at).toLocaleString("en-US")}
      </p>
      <blockquote>{mail.excerpt}</blockquote>
      <p>{mail.reason}</p>
      <a
        href={safeUrl("https://mail.google.com/mail/u/0/#all/" + mail.id)}
        target="_blank"
        rel="noreferrer"
      >
        Open original email ↗
      </a>
      {mail.state === "pending" && (
        <>
          <Field label="Matching application">
            <select value={id} onChange={(e) => setId(e.target.value)}>
              <option value="">Choose the exact company and role</option>
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.company} · {j.title}
                </option>
              ))}
            </select>
          </Field>
          <p className="small">
            {mail.submission_date
              ? "Email states application date: " + mail.submission_date
              : "No submission date is stated. The confirmation receipt date is kept separately."}
          </p>
          <div className="actions">
            <button
              className="primary"
              disabled={busy || ["reminder", "uncertain"].includes(mail.kind)}
              onClick={() => resolve("confirm")}
            >
              {id ? "Confirm status update" : "Track this application"}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => resolve("dismiss")}
            >
              Dismiss
            </button>
          </div>
          <p className="small">
            If the full posting is available, save it to unlock a tailored
            resume and cover letter. Otherwise this email can create a minimal
            application record now.
          </p>
          <button
            className="text-button"
            onClick={() => {
              onClose();
              onAdd();
            }}
          >
            Add the full posting instead
          </button>
        </>
      )}
    </Modal>
  );
}

/** Postings the sponsorship gate cut, each with the sentence that triggered it. Restore is for a wrong call only. */
export function ExcludedRoles({
  jobs,
  onRestore,
}: {
  jobs: ExcludedJob[];
  onRestore: (job: ExcludedJob) => void;
}) {
  if (!jobs.length) return null;
  return (
    <details className="removed-jobs">
      <summary>
        Excluded roles · {jobs.length}
        <small className="muted"> — cut by the sponsorship gate; the sentence that triggered each one is shown</small>
      </summary>
      {jobs.map((job) => (
        <div className="removed-job" key={job.id}>
          <span>
            <b>{job.company}</b> · {job.title}
            {job.location ? <span className="job-location"> · {job.location}</span> : null}
            <small>
              {job.reason_label} · posting says: “{job.sentence}” · found by {job.source}
              {job.url ? (
                <>
                  {" "}·{" "}
                  <a href={safeUrl(job.url)} target="_blank" rel="noreferrer">
                    open posting
                  </a>
                </>
              ) : null}
            </small>
          </span>
          <button
            className="secondary"
            title="Only if the exclusion is wrong: saves it as a normal tier C job"
            onClick={() => onRestore(job)}
          >
            <RotateCcw size={15} /> Restore
          </button>
        </div>
      ))}
    </details>
  );
}
