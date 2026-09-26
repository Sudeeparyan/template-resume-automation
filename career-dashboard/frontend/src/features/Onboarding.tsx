import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  FileText,
  MessageSquare,
  LoaderCircle,
  Paperclip,
  Sparkles,
  Square,
  Trash2,
  Upload,
} from "lucide-react";
import { shellApi, uploadFile } from "../api";
import { Badge, Field } from "../components/UI";
import { firstName, type ProfileEntry } from "../profiles";

type Step = { label: string; status: "running" | "done" | "failed"; detail: string };
type Draft = {
  contact: Record<string, string>;
  authorization: Record<string, string>;
  targets: { roles?: string[]; countries?: string[]; cities?: string[] };
  country_pack: string;
  counts: Record<string, number>;
  education: { degree: string; field: string; institution: string; start: string; end: string; grade: string }[];
  experience: { title: string; employer: string; start: string; end: string; bullets: number }[];
  projects: { name: string; kind: string; facts: number; metrics: number }[];
  skills: { name: string; skills: string[] }[];
  certifications: { name: string; issuer: string; date: string }[];
  questions: string[];
  coverage: { blocks: number; cited: number; narrative: number; verbatim: number; accounted: number; numbers_not_used?: unknown[] };
};
type Intake = {
  state: "empty" | "uploaded" | "reading" | "review" | "building" | "built" | "failed";
  files: { name: string; bytes: number }[];
  steps: Step[];
  error?: string | null;
  running?: boolean;
  usage?: { calls: number };
  draft?: Draft;
  packs: { code: string; name: string; paper: string }[];
};

const ACCEPT = ".docx,.pdf,.txt,.md";
const size = (n: number) => (n > 1_000_000 ? (n / 1_000_000).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1000)) + " KB");

/** A new profile's first page: upload documents about the person, check what was found, build. */
export default function Onboarding({
  profile,
  notify,
  onUseChat,
}: {
  profile: ProfileEntry;
  notify: (t: string, e?: boolean) => void;
  onUseChat?: () => void;
}) {
  const [intake, setIntake] = useState<Intake | null>(null);
  const [busy, setBusy] = useState("");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const base = `/profiles/${profile.id}/intake`;

  const load = useCallback(async () => {
    try {
      setIntake(await shellApi<Intake>(base));
    } catch (e) {
      notify((e as Error).message, true);
    }
  }, [base, notify]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (intake?.state !== "reading" && intake?.state !== "building") return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [intake?.state, load]);

  async function upload(files: FileList | File[]) {
    setBusy("upload");
    try {
      let latest: Intake | null = null;
      for (const file of Array.from(files)) latest = await uploadFile<Intake>("/api" + base + "/files", file, file.name);
      if (latest) setIntake(latest);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy("");
    }
  }
  async function act(name: string, path: string, method = "POST", body?: unknown) {
    setBusy(name);
    try {
      const next = await shellApi<Intake & { profile?: ProfileEntry }>(path, method, body);
      setIntake(next);
      return next;
    } catch (e) {
      notify((e as Error).message, true);
      return null;
    } finally {
      setBusy("");
    }
  }

  const who = firstName(profile.name) || profile.name;
  if (!intake) return <div className="onboarding"><LoaderCircle className="spin" /> Loading…</div>;
  const reading = intake.state === "reading" || intake.running;
  return (
    <div className="onboarding">
      <div className="page-title">
        <div>
          <div className="eyebrow">NEW PROFILE · STEP {intake.draft ? 2 : 1} OF 2</div>
          <h1>Let’s build {who}’s workspace</h1>
          <p>
            Upload the documents about {who}: resumes, an “about me”, project write-ups, prepared interview answers.
            The AI reads every line, and you check what it found before anything is built. This profile is completely
            separate from the others on this PC.
          </p>
        </div>
        {onUseChat && (
          <button type="button" className="text-button" onClick={onUseChat}>
            <MessageSquare size={15} /> Set up in the chat instead
          </button>
        )}
      </div>

      {(intake.state === "empty" || intake.state === "uploaded" || intake.state === "failed" || intake.state === "review") && (
        <section className="card">
          <div
            className={"drop-zone" + (dragging ? " dragging" : "")}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files.length) upload(e.dataTransfer.files);
            }}
          >
            <Upload size={28} />
            <b>Drop Word or PDF files here</b>
            <span className="muted">or</span>
            <button className="secondary" disabled={busy === "upload"} onClick={() => input.current?.click()}>
              <Paperclip size={15} /> {busy === "upload" ? "Uploading…" : "Choose files"}
            </button>
            <small className="muted">.docx, .pdf, .txt or .md · up to 20 MB each</small>
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              hidden
              onChange={(e) => {
                if (e.target.files?.length) upload(e.target.files);
                e.target.value = "";
              }}
            />
          </div>
          {intake.files.length > 0 && (
            <ul className="file-list">
              {intake.files.map((f) => (
                <li key={f.name}>
                  <FileText size={16} />
                  <span>{f.name}</span>
                  <small className="muted">{size(f.bytes)}</small>
                  <button
                    className="icon-button"
                    aria-label={"Remove " + f.name}
                    onClick={() => act("remove", `${base}/files/${encodeURIComponent(f.name)}`, "DELETE")}
                  >
                    <Trash2 size={15} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {intake.state === "failed" && intake.error && (
            <div className="callout warning" role="alert">
              <CircleAlert size={18} />
              <div>
                <b>Reading stopped.</b>
                <p>{intake.error}</p>
              </div>
            </div>
          )}
          <div className="actions">
            <button className="primary" disabled={!intake.files.length || busy !== ""} onClick={() => act("start", base + "/start")}>
              <Sparkles size={16} /> {intake.state === "failed" ? "Try again" : intake.state === "review" ? "Read again with these documents" : "Read my documents"}
            </button>
          </div>
        </section>
      )}

      {(reading || intake.steps.length > 0) && intake.state !== "review" && intake.state !== "built" && (
        <section className="card spaced intake-steps" aria-live="polite">
          <div className="section-title">
            <h2>Reading {intake.files.length} document{intake.files.length === 1 ? "" : "s"}</h2>
            {reading && (
              <button className="secondary" disabled={busy === "stop"} onClick={() => act("stop", base + "/stop")}>
                <Square size={14} /> Stop
              </button>
            )}
          </div>
          <ol>
            {intake.steps.map((s) => (
              <li key={s.label} className={"step-" + s.status}>
                {s.status === "running" ? <LoaderCircle size={16} className="spin" /> : s.status === "done" ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}
                <div>
                  <b>{s.label}</b>
                  {s.detail && <small>{s.detail}</small>}
                </div>
              </li>
            ))}
          </ol>
          {reading && <p className="muted">This takes a few minutes for a long document. You can leave this page open.</p>}
        </section>
      )}

      {intake.draft && (intake.state === "review" || intake.state === "building") && (
        <Review
          draft={intake.draft}
          packs={intake.packs}
          files={intake.files}
          busy={busy}
          onSave={(changes) => act("save", base + "/draft", "PUT", changes)}
          onBuild={async (changes) => {
            const saved = await act("save", base + "/draft", "PUT", changes);
            if (!saved) return;
            const built = await act("build", base + "/build");
            if (built?.profile?.state === "ready") {
              notify(`${who}'s workspace is ready.`);
              location.reload();
            }
          }}
        />
      )}
    </div>
  );
}

function Review({
  draft,
  packs,
  files,
  busy,
  onSave,
  onBuild,
}: {
  draft: Draft;
  packs: Intake["packs"];
  files: Intake["files"];
  busy: string;
  onSave: (changes: Record<string, unknown>) => void;
  onBuild: (changes: Record<string, unknown>) => void;
}) {
  const [contact, setContact] = useState<Record<string, string>>({ ...draft.contact });
  const [auth, setAuth] = useState<Record<string, string>>({ ...draft.authorization });
  const [pack, setPack] = useState(draft.country_pack);
  const [roles, setRoles] = useState((draft.targets.roles || []).join(", "));
  const changes = () => ({
    ...Object.fromEntries(
      ["full_name", "preferred_name", "email", "phone", "linkedin", "github", "portfolio_url", "city", "country"].map((k) => [k, contact[k] || ""]),
    ),
    country_pack: pack,
    roles: roles.split(",").map((r) => r.trim()).filter(Boolean),
    authorization: {
      status: auth.status || "",
      valid_until: auth.valid_until || "",
      conditions: auth.conditions || "",
      needs_sponsorship_later: auth.needs_sponsorship_later || "unknown",
    },
  });
  const c = draft.coverage;
  const text = (key: string, label: string, type = "text") => (
    <Field label={label}>
      <input type={type} value={contact[key] || ""} onChange={(e) => setContact({ ...contact, [key]: e.target.value })} />
    </Field>
  );
  return (
    <>
      <section className="card spaced">
        <div className="section-title">
          <h2>What the AI found</h2>
          <Badge tone="green">
            {c.accounted} of {c.blocks} parts of your documents accounted for
          </Badge>
        </div>
        <p>
          From {files.map((f) => f.name).join(", ")}: {draft.counts.education} education entr{draft.counts.education === 1 ? "y" : "ies"}, {draft.counts.experience} role(s),{" "}
          {draft.counts.projects} project(s), {draft.counts.skills} skills, {draft.counts.certifications} certification(s) and{" "}
          {draft.counts.interview_answers} prepared interview answer(s). {c.cited} parts are used in profile entries, {c.narrative} are
          background, and {c.verbatim} are kept word for word in “Anything else”, so nothing is lost.
        </p>
        <div className="review-grid">
          <div>
            <h3>Education</h3>
            <ul>
              {draft.education.map((e, i) => (
                <li key={i}>
                  <b>{e.degree}{e.field ? ` in ${e.field}` : ""}</b>{e.institution ? ` — ${e.institution}` : ""} <small className="muted">{[e.start, e.end].filter(Boolean).join(" – ")}{e.grade ? ` · ${e.grade}` : ""}</small>
                </li>
              ))}
            </ul>
            <h3>Experience</h3>
            <ul>
              {draft.experience.length ? draft.experience.map((r, i) => (
                <li key={i}>
                  <b>{r.title}</b> — {r.employer} <small className="muted">{[r.start, r.end].filter(Boolean).join(" – ")} · {r.bullets} points</small>
                </li>
              )) : <li className="muted">No employment described.</li>}
            </ul>
            <h3>Certifications</h3>
            <ul>
              {draft.certifications.length ? draft.certifications.map((x, i) => (
                <li key={i}>{x.name}{x.issuer ? ` — ${x.issuer}` : ""} <small className="muted">{x.date}</small></li>
              )) : <li className="muted">None.</li>}
            </ul>
          </div>
          <div>
            <h3>Projects</h3>
            <ul>
              {draft.projects.map((p, i) => (
                <li key={i}>
                  <b>{p.name}</b> <small className="muted">{p.kind !== "unspecified" ? p.kind + " · " : ""}{p.facts} facts, {p.metrics} numbers</small>
                </li>
              ))}
            </ul>
            <h3>Skills</h3>
            {draft.skills.map((g) => (
              <p key={g.name} className="skill-line"><b>{g.name}:</b> {g.skills.join(", ")}</p>
            ))}
          </div>
        </div>
        {draft.questions.length > 0 && (
          <div className="callout warning spaced">
            <CircleAlert size={18} />
            <div>
              <b>Questions to answer later (they are saved to the profile):</b>
              <ul>{draft.questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </div>
          </div>
        )}
      </section>

      <section className="card spaced">
        <h2>Check the essentials</h2>
        <p>These go on every resume and decide where the job search looks. Correct anything the documents did not say.</p>
        <div className="form-grid">
          {text("full_name", "Full name")}
          {text("preferred_name", "Preferred first name (optional)")}
          {text("email", "Email for resumes", "email")}
          {text("phone", "Phone for resumes")}
          {text("linkedin", "LinkedIn URL (optional)", "url")}
          {text("github", "GitHub URL (optional)", "url")}
          {text("portfolio_url", "Portfolio URL (optional)", "url")}
          {text("city", "City you live in")}
        </div>
        <div className="form-grid">
          <Field label="Search for jobs in">
            <select value={pack} onChange={(e) => setPack(e.target.value)}>
              {packs.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name} ({p.paper} resumes)
                </option>
              ))}
            </select>
          </Field>
          <Field label="Roles you are looking for (comma-separated)">
            <input value={roles} onChange={(e) => setRoles(e.target.value)} />
          </Field>
          <Field label="Your current permission to work">
            <input value={auth.status || ""} placeholder="e.g. Stamp 1G" onChange={(e) => setAuth({ ...auth, status: e.target.value })} />
          </Field>
          <Field label="Valid until">
            <input value={auth.valid_until || ""} onChange={(e) => setAuth({ ...auth, valid_until: e.target.value })} />
          </Field>
          <Field label="Will you need an employer to sponsor a work permit (now or later)?">
            <select value={auth.needs_sponsorship_later || "unknown"} onChange={(e) => setAuth({ ...auth, needs_sponsorship_later: e.target.value })}>
              <option value="yes">Yes</option>
              <option value="unknown">Not sure (treated as yes)</option>
              <option value="no">No, never</option>
            </select>
          </Field>
        </div>
        <p className="muted">
          Postings that refuse to sponsor a permit, or require a citizenship or clearance you do not have, are set aside with the
          sentence that excluded them, and you can restore any wrong call.
        </p>
        <div className="actions">
          <button className="secondary" disabled={busy !== ""} onClick={() => onSave(changes())}>
            Save changes
          </button>
          <button className="primary" disabled={busy !== "" || !(contact.full_name || "").trim()} onClick={() => onBuild(changes())}>
            {busy === "build" ? <><LoaderCircle size={16} className="spin" /> Building…</> : <><Sparkles size={16} /> Build my workspace</>}
          </button>
        </div>
      </section>
    </>
  );
}
