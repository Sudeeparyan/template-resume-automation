import { useEffect, useState } from "react";
import { ExternalLink, Search, FileText } from "lucide-react";
import { api, fileUrl, safeUrl } from "../api";
import { AskAssistant, Badge, Field, Modal, ReportView, Running } from "./UI";
import { statuses, statusLabel, TierBadge, FitBadge } from "./JobList";
import type { Summary, Job } from "../types";
export default function JobDetail({
  job,
  data,
  onClose,
  refresh,
  notify,
}: {
  job: Job;
  data: Summary;
  onClose: () => void;
  refresh: () => Promise<void>;
  notify: (s: string, e?: boolean) => void;
}) {
  const [status, setStatus] = useState(job.status);
  const [date, setDate] = useState(job.application_date || "");
  const [notes, setNotes] = useState(job.notes);
  const [section, setSection] = useState("progress");
  const [detail, setDetail] = useState<any>();
  const [project, setProject] = useState(job.selected_project_id || "");
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState<any>();
  const runs = data.runs.filter(
    (r) => r.kind === "research" && r.job_id === job.id,
  );
  const latest = runs[0];
  const running = runs.some((r) => ["queued", "running"].includes(r.state));
  useEffect(() => {
    api("/jobs/" + job.id)
      .then((d) => {
        setDetail(d);
        setProject((p) => p || d.projects[0]?.id || "");
      })
      .catch((e) => notify(e.message, true));
  }, [job.id]);
  async function action(kind: string) {
    setBusy(kind);
    try {
      const r = await api(
        "/jobs/" + job.id + "/" + kind,
        "POST",
        kind === "prepare" ? { project_id: project } : undefined,
      );
      setResult(r);
      await refresh();
      setDetail(await api("/jobs/" + job.id));
      notify(
        kind === "prepare"
          ? "Draft prepared. Tailoring and review are still required."
          : kind === "preview"
            ? "PDF preview compiled."
            : "Validation finished. Check the results below.",
      );
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy("");
    }
  }
  return (
    <Modal title={job.company + " · " + job.title} onClose={onClose} wide>
      <div className="job-meta">
        <Badge tone={job.status === "applied" ? "green" : "neutral"}>
          {statusLabel[job.status] || job.status}
        </Badge>
        <FitBadge job={job} />
        <span>{job.location}</span>
        <a href={safeUrl(job.url)} target="_blank" rel="noreferrer">
          Open posting <ExternalLink size={14} />
        </a>
        <AskAssistant
          prompts={[
            { label: "What is left before I apply?", text: `What is left to do for ${job.company} — ${job.title} before I apply? Check the resume, the research, the study plan and the Assurance claims.` },
            { label: "Research and study plan", text: `Research ${job.company} and then write the study plan for ${job.company} — ${job.title}`, send: false },
            { label: "I applied to this job", text: `I applied to ${job.company} today`, send: false },
          ]}
        />
        {job.record_source !== "gmail" && (
          <button
            className="secondary"
            disabled={!!busy}
            onClick={async () => {
              setBusy("verify");
              try {
                const checked = await api<any>(
                  "/v2/jobs/" + job.id + "/verify",
                  "POST",
                );
                await refresh();
                notify(
                  "Posting check: " +
                    checked.state.replaceAll("_", " ") +
                    ". " +
                    checked.evidence.join(" "),
                );
              } catch (e) {
                notify((e as Error).message, true);
              } finally {
                setBusy("");
              }
            }}
          >
            {busy === "verify" ? "Checking…" : "Check posting now"}
          </button>
        )}
        <Badge tone={(job.posting_state || "active") === "active" ? "green" : "amber"}>
          {(job.posting_state || "active").replaceAll("_", " ")}
        </Badge>
        <button
          className="secondary"
          disabled={!!busy}
          onClick={async () => {
            setBusy("company");
            try {
              const checked = await api<any>(
                "/v2/companies/" + job.id + "/check",
                "POST",
              );
              await refresh();
              const flags = (checked.red_flags || []).length
                ? " Red flags: " + checked.red_flags.join("; ")
                : "";
              notify(
                "Company check: " +
                  String(checked.state).replaceAll("_", " ") +
                  "." +
                  flags,
                checked.state === "blocked",
              );
            } catch (e) {
              notify((e as Error).message, true);
            } finally {
              setBusy("");
            }
          }}
        >
          {busy === "company" ? "Checking…" : "Check company"}
        </button>
        {job.legitimacy_state && (
          <Badge
            tone={
              job.legitimacy_state === "verified"
                ? "green"
                : job.legitimacy_state === "blocked"
                  ? "red"
                  : "amber"
            }
          >
            employer {job.legitimacy_state.replaceAll("_", " ")}
          </Badge>
        )}
        <TierBadge job={job} long />
        <button
          className="secondary"
          disabled={!!busy}
          title="Re-read the saved posting for sponsorship, citizenship and clearance wording"
          onClick={async () => {
            setBusy("sponsorship");
            try {
              const result = await api<any>("/v2/jobs/" + job.id + "/sponsorship", "POST");
              await refresh();
              if (result.excluded) {
                notify(
                  `This posting now excludes itself: “${result.sentence}”. It moved to Excluded roles on the Dashboard.`,
                  true,
                );
                onClose();
              } else {
                notify("Sponsorship re-checked: tier " + result.job.sponsor_tier + " · " + (result.job.sponsor_evidence?.label || ""));
              }
            } catch (e) {
              notify((e as Error).message, true);
            } finally {
              setBusy("");
            }
          }}
        >
          {busy === "sponsorship" ? "Checking…" : "Re-check sponsorship"}
        </button>
      </div>
      {job.sponsor_evidence?.sentence && (
        <p className="small muted">
          Sponsorship wording found in the posting: “{job.sponsor_evidence.sentence}”
          {job.sponsor_evidence.everify ? " · E-Verify employer (STEM OPT extension possible)" : ""}
        </p>
      )}
      <div className="segmented detail-tabs">
        {[
          ["progress", "Progress"],
          ["research", "Company & hiring review"],
          ["resume", "Original prepared pack"],
          ["description", "Job description"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={section === id ? "selected" : ""}
            onClick={() => setSection(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {section === "progress" && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy("save");
            try {
              await api("/jobs/" + job.id, "PATCH", {
                status,
                notes,
                application_date: date || null,
              });
              await refresh();
              notify("Application progress saved.");
            } catch (e) {
              notify((e as Error).message, true);
            } finally {
              setBusy("");
            }
          }}
        >
          <div className="form-grid">
            <Field label="Application status">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel[s] || s}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Actual submission date (if known)">
              <input
                type="date"
                max={data.goals.date}
                value={date}
                onChange={(e) => setDate(e.target.value)}
                required={
                  status === "applied" &&
                  !data.mail.messages.some(
                    (m) =>
                      m.job_id === job.id &&
                      m.state === "confirmed" &&
                      m.kind === "applied",
                  )
                }
              />
            </Field>
          </div>
          <Field label="Your notes & next action">
            <textarea
              rows={6}
              maxLength={10000}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </Field>
          <div className="actions">
            <button className="primary" disabled={!!busy}>
              Save progress
            </button>
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                try {
                  await api(
                    "/search-runs/" + data.goals.date + "/jobs/" + job.id,
                    "POST",
                  );
                  await refresh();
                  notify("Added to today’s search list.");
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            >
              Add to today’s list
            </button>
          </div>
          {data.mail.messages
            .filter((m) => m.job_id === job.id && m.state === "confirmed")
            .map((m) => (
              <div className="callout spaced" key={m.id}>
                <div>
                  <Badge tone="green">Email confirmed · {m.kind}</Badge>
                  <p>{m.excerpt}</p>
                  <a
                    href={safeUrl(
                      "https://mail.google.com/mail/u/0/#all/" + m.id,
                    )}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Original email · {m.received_at.slice(0, 10)} ↗
                  </a>
                </div>
              </div>
            ))}
        </form>
      )}
      {section === "description" && (
        <>
          <h3>Saved job description</h3>
          <p className="small">
            Snapshot saved {job.created_at.slice(0, 10)}. Check the posting for
            current availability.
          </p>
          <div className="jd-text">{job.description}</div>
        </>
      )}
      {section === "research" && (
        <>
          <div className="callout">
            <div>
              <b>Three separate reviews</b>
              <p>
                Company research → independent hiring expectations → your
                profile comparison. The hiring manager receives only the job and
                public research, with no profile or chat history.
              </p>
            </div>
          </div>
          <button
            className="primary spaced"
            disabled={!!busy || running}
            onClick={async () => {
              setBusy("research");
              try {
                await api("/v2/agents/run", "POST", {
                  kind: "research",
                  job_id: job.id,
                });
                await refresh();
                notify(
                  "Research started. Each review is saved as it finishes.",
                );
              } catch (e) {
                notify((e as Error).message, true);
              } finally {
                setBusy("");
              }
            }}
          >
            <Search size={16} />
            {running
              ? "Review in progress…"
              : latest
                ? "Run a fresh review"
                : "Research this role"}
          </button>
          {latest && (
            <>
              <Running run={latest} />
              {[
                ["research", "Company & role research"],
                ["hiring", "Independent hiring expectations"],
                ["comparison", "Your profile comparison"],
              ].map(
                ([key, title]) =>
                  latest.result?.[key] && (
                    <details className="report-section" open key={key}>
                      <summary>
                        {title}
                        {key === "hiring" && (
                          <Badge tone="green">No profile access</Badge>
                        )}
                      </summary>
                      <ReportView report={latest.result[key]} />
                    </details>
                  ),
              )}
            </>
          )}
          {runs.length > 1 && (
            <details>
              <summary>Previous runs · {runs.length - 1}</summary>
              {runs.slice(1).map((r) => (
                <details key={r.id}>
                  <summary>
                    {new Date(r.created_at).toLocaleString("en-US")} · {r.state}
                  </summary>
                  {r.error && <p role="alert">{r.error}</p>}
                  {["research", "hiring", "comparison"].map(
                    (k) =>
                      r.result?.[k] && (
                        <ReportView key={k} report={r.result[k]} />
                      ),
                  )}
                </details>
              ))}
            </details>
          )}
        </>
      )}
      {section === "resume" && (
        <>
          <div className="callout warning">
            <p>
              These tools manage the original prepared pack. Your Resume Studio
              edits and versioned previews are saved separately in its Studio
              folder.
            </p>
          </div>
          <p>
            Drafts select approved project wording. Company-specific tailoring
            and visual review remain separate checks.
          </p>
          {data.profile_dirty && (
            <div className="callout warning">
              Reconcile your edited profile with the evidence registry before
              creating a new draft.
            </div>
          )}
          <Field label="Selected project">
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              {detail?.projects.map((p: any) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </Field>
          {detail?.projects.find((p: any) => p.id === project) && (
            <p className="small">
              {detail.projects.find((p: any) => p.id === project).context}
            </p>
          )}
          <div className="actions">
            <button
              className="primary"
              disabled={!!busy || data.profile_dirty || !project}
              onClick={() => action("prepare")}
            >
              <FileText size={17} />
              {busy === "prepare" ? "Preparing…" : "Prepare a new draft"}
            </button>
            <button
              className="secondary"
              disabled={!!busy || !job.folder}
              onClick={() => action("preview")}
            >
              {busy === "preview" ? "Compiling…" : "Compile preview"}
            </button>
            <button
              className="secondary"
              disabled={!!busy || !job.folder}
              onClick={() => action("validate")}
            >
              {busy === "validate" ? "Checking…" : "Validate evidence"}
            </button>
          </div>
          {job.folder && (
            <p>
              {detail?.artifacts?.some((p: string) =>
                p.endsWith("/resume.pdf"),
              ) ? (
                <a
                  href={fileUrl(
                    job.folder.replace(/^output\//, "") + "/resume.pdf",
                  )}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open current PDF ↗
                </a>
              ) : (
                <span>Compile preview to create the PDF.</span>
              )}
              <br />
              <small>{job.folder}</small>
            </p>
          )}
          {result && (
            <details open>
              <summary>Latest result</summary>
              <pre>{JSON.stringify(result, null, 2)}</pre>
            </details>
          )}
        </>
      )}
    </Modal>
  );
}
