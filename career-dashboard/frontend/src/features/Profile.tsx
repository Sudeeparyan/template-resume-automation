import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Award,
  Briefcase,
  CheckCheck,
  FolderKanban,
  Github,
  Globe,
  GraduationCap,
  Info,
  Linkedin,
  Mail,
  Pencil,
  Phone,
  Plus,
  Target,
  Trash2,
  UserRound,
  Wrench,
} from "lucide-react";
import { api, safeUrl } from "../api";
import { AskAssistant, Badge, Field, Loading, Modal, RichText } from "../components/UI";
import type { ProfileData, ProfileEntry, ProfileFieldValue, ProfileSync } from "../types";
import { DataView, Lines, ProfileEditor, Tags, Value } from "./ProfileParts";
import {
  asList,
  asText,
  byDateDesc,
  describeChange,
  describeSync,
  grouped,
  isUrl,
  KIND_LABELS,
  readable,
  statusBadge,
} from "./profileView";

const PERSONAL_ORDER = ["Contact & identity", "Work authorization", "Career snapshot", "How resumes show these", "Other details"];
// Within Basics, rows follow a resume header rather than the alphabet.
const FIELD_ORDER = [
  "full_name", "preferred_name", "email", "phone", "github", "linkedin", "portfolio_url", "location",
  "city_as_inferred", "timezone", "work_authorization", "sponsorship_need", "citizenship", "availability",
  "current_status", "most_recent_role", "education_summary", "dated_professional_experience",
];
const fieldRank = (entry: ProfileEntry) => {
  const index = FIELD_ORDER.indexOf(entry.data?.field);
  return index < 0 ? FIELD_ORDER.length : index;
};
const SKILL_ORDER = ["Strong", "Used it", "Touched it", "Added by you", "Other skills", "Honest gaps"];
const SKILL_HINTS: Record<string, string> = {
  Strong: "Can appear in bullets and in the skills list.",
  "Used it": "Only alongside the experience or project that shows it.",
  "Touched it": "Skills list only, never in a bullet.",
  "Added by you": "Yours, added here. Each job's tailored skills list can use them.",
  "Honest gaps": "Not yours yet. Never on a resume; study-plan material only.",
};
// The page reads top to bottom in the same order a resume is built.
const SECTIONS = [
  { id: "basics", label: "Basics", kind: "personal", icon: UserRound, hint: "Name, contact, work authorization and how resumes show them." },
  { id: "search", label: "Job search", kind: "personal", icon: Target, hint: "The roles and places the job search looks for." },
  { id: "experience", label: "Experience", kind: "experience", icon: Briefcase, hint: "Roles, employers and dates. Agents propose; only you change these." },
  { id: "projects", label: "Projects", kind: "project", icon: FolderKanban, hint: "Real, built projects a resume can draw on." },
  { id: "skills", label: "Skills", kind: "skill", icon: Wrench, hint: "Tools and technologies, grouped by how strongly you can defend them." },
  { id: "education", label: "Education", kind: "education", icon: GraduationCap, hint: "Degrees, schools and dates." },
  { id: "certifications", label: "Certifications", kind: "certification", icon: Award, hint: "Completed certificates only." },
  { id: "facts", label: "Other facts", kind: "fact", icon: Info, hint: "Registered contact details, coursework, languages, metrics and items on hold." },
] as const;
const CONTACTS = [
  ["email", Mail],
  ["phone", Phone],
  ["github", Github],
  ["linkedin", Linkedin],
  ["portfolio_url", Globe],
] as const;

const field = (entry: ProfileEntry, key: string) => asText(entry.fields[key]);
const isStructured = (entry: ProfileEntry) => entry.form.some((spec) => spec.key.startsWith("value."));
const scrollTo = (id: string) =>
  document.getElementById("profile-" + id)?.scrollIntoView({ behavior: "smooth", block: "start" });

export function EntryTools({
  entry,
  onEdit,
  onRemove,
  quiet = false,
}: {
  entry: ProfileEntry;
  onEdit: (e: ProfileEntry) => void;
  onRemove: (e: ProfileEntry) => void;
  quiet?: boolean;
}) {
  const badge = statusBadge(entry);
  const show = !quiet || entry.review_state === "user_updated" || ["missing", "hold"].includes(entry.status || "");
  return (
    <div className="entry-tools">
      {show && (
        <Badge tone={badge.tone} title={badge.title}>
          {badge.label}
        </Badge>
      )}
      <button className="icon-button" aria-label={"Edit " + entry.label} onClick={() => onEdit(entry)}>
        <Pencil size={15} />
      </button>
      {!entry.locked && (
        <button className="icon-button" aria-label={"Remove " + entry.label} onClick={() => onRemove(entry)}>
          <Trash2 size={15} />
        </button>
      )}
    </div>
  );
}

/** Registry notes for an entry: how resumes may use it, extra details and where it came from. */
function About({ entry }: { entry: ProfileEntry }) {
  return (
    <details className="entry-about">
      <summary>About this entry</summary>
      {entry.usage && (
        <p>
          <b>How resumes may use it:</b> {entry.usage}
        </p>
      )}
      {entry.extra.length > 0 && (
        <dl className="data-view">
          {entry.extra.map((row) => (
            <div key={row.key}>
              <dt>{row.label}</dt>
              <dd>
                <DataView value={row.value} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {entry.sources.length > 0 && (
        <>
          <p>
            <b>Comes from</b>
          </p>
          <ul className="source-refs">
            {entry.sources.map((source) => (
              <li key={source}>{source}</li>
            ))}
          </ul>
        </>
      )}
      <small className="muted">
        {entry.id} · revision {entry.revision} · {entry.source}
      </small>
    </details>
  );
}

/** A Basics value: a mapping as labelled rows, a list as tags, anything else as text. */
function PersonalValue({ entry }: { entry: ProfileEntry }) {
  if (isStructured(entry))
    return (
      <dl className="data-view spacious">
        {entry.form
          .filter((spec) => spec.key.startsWith("value."))
          .map((spec) => (
            <div key={spec.key}>
              <dt>{spec.label}</dt>
              <dd>
                {spec.type === "list" ? (
                  <Lines items={asList(entry.fields[spec.key])} />
                ) : (
                  <Value text={field(entry, spec.key)} />
                )}
              </dd>
            </div>
          ))}
      </dl>
    );
  if (Array.isArray(entry.fields.value)) return <Tags items={entry.fields.value} />;
  const text = field(entry, "value");
  // Only policy rows hold config tokens; emails and links keep their underscores.
  return <Value text={entry.group === "How resumes show these" ? readable(text) : text} />;
}

/** Missing and held entries say why, in the registry's own words. */
const isHeld = (entry: ProfileEntry) => ["missing", "hold"].includes(entry.status || "");

function HeldNote({ entry }: { entry: ProfileEntry }) {
  if (!isHeld(entry) || !entry.usage) return null;
  return <p className="entry-note">{entry.usage}</p>;
}

function Section({
  id,
  title,
  count,
  hint,
  onAdd,
  children,
}: {
  id: string;
  title: string;
  count: number;
  hint: string;
  onAdd?: () => void;
  children: ReactNode;
}) {
  return (
    <section className="card spaced profile-section" id={"profile-" + id} aria-labelledby={"profile-" + id + "-title"}>
      <div className="section-title">
        <h2 id={"profile-" + id + "-title"}>
          {title} <span className="muted small">{count}</span>
        </h2>
        {onAdd && (
          <button className="secondary" onClick={onAdd}>
            <Plus size={15} /> Add
          </button>
        )}
      </div>
      <p className="muted small">{hint}</p>
      {count === 0 ? <p className="muted">Nothing here yet.</p> : children}
    </section>
  );
}

export default function Profile({
  notify,
  refresh,
}: {
  notify: (s: string, e?: boolean) => void;
  refresh: () => Promise<void>;
}) {
  const [data, setData] = useState<ProfileData>();
  const [error, setError] = useState("");
  const [edit, setEdit] = useState<{ entry: ProfileEntry | null; kind: string } | null>(null);
  const [remove, setRemove] = useState<ProfileEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const [chat, setChat] = useState("");
  const [chatPreview, setChatPreview] = useState<any>(null);
  const [chatRequest, setChatRequest] = useState("");
  async function load() {
    try {
      const profile = await api<ProfileData>("/v2/profile");
      // A dashboard started before this page was updated sends rows without the readable view.
      if (!profile.schema || profile.items.some((i) => !i.form))
        throw new Error(
          "The dashboard is still running older code. Close its window and start it again (Start Dashboard) to open the updated Profile.",
        );
      setData(profile);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, []);
  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      const result = (await action()) as { synced?: ProfileSync } | undefined;
      await load();
      await refresh();
      notify(describeSync(done, result?.synced));
      return true;
    } catch (e) {
      notify((e as Error).message, true);
      return false;
    } finally {
      setBusy(false);
    }
  }
  if (error)
    return (
      <div className="callout warning" role="alert">
        {error}
        <button className="secondary" onClick={load}>
          Retry
        </button>
      </div>
    );
  if (!data) return <Loading label="Loading your knowledge library" />;

  const items = data.items;
  const labels = Object.fromEntries(items.map((i) => [i.id, i.label]));
  const personal = items.filter((i) => i.kind === "personal");
  const search = personal.filter((i) => i.group === "Job search");
  const basics = personal
    .filter((i) => i.group !== "Job search")
    .sort((a, b) => fieldRank(a) - fieldRank(b) || a.label.localeCompare(b.label));
  const ofKind = (kind: string) => items.filter((i) => i.kind === kind);
  const byField = (name: string) => personal.find((i) => i.data?.field === name);
  const name = byField("full_name")?.summary || "Your profile";
  const status = byField("current_status")?.summary || "";
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();
  const counts: Record<string, number> = {
    basics: basics.length,
    search: search.length,
    experience: ofKind("experience").length,
    projects: ofKind("project").length,
    skills: ofKind("skill").length,
    education: ofKind("education").length,
    certifications: ofKind("certification").length,
    facts: ofKind("fact").length,
  };
  const openEdit = (entry: ProfileEntry) => setEdit({ entry, kind: entry.kind });
  const openAdd = (kind: string) => setEdit({ entry: null, kind });
  const tools = { onEdit: openEdit, onRemove: setRemove };

  async function save(kind: string, fields: Record<string, ProfileFieldValue>) {
    const entry = edit?.entry;
    const saved = await run(
      () =>
        api(
          "/v2/profile/items" + (entry ? "/" + encodeURIComponent(entry.id) : ""),
          entry ? "PUT" : "POST",
          {
            kind,
            title: asText(fields.title).trim() || entry?.title || kind,
            summary: "",
            revision: entry?.revision,
            fields,
          },
        ),
      "Saved.",
    );
    if (saved) setEdit(null);
  }

  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">YOUR INFORMATION, YOUR CONTROL</div>
          <h1>Profile</h1>
          <p>
            Everything the agents know about you, in plain words. Saving an entry updates it
            everywhere at once: the evidence registry, your resumes and open drafts, the job search
            and the agents.
          </p>
        </div>
        <div className="actions">
          <AskAssistant
            prompts={[
              { label: "Tell it what changed", text: "I finished ", send: false },
              { label: "My open questions", text: "Show my open profile questions." },
              { label: "What is waiting for my review?", text: "Which profile entries are waiting for my review, and why does that matter for new resumes?" },
            ]}
          />
          <button className="primary" onClick={() => openAdd("experience")}>
            <Plus size={17} />
            Add entry
          </button>
        </div>
      </div>

      <section className="card profile-hero" aria-label="Profile overview">
        <div className="profile-avatar" aria-hidden="true">
          {initials || "?"}
        </div>
        <div className="profile-hero-main">
          <h2>{name}</h2>
          {status && <p>{status}</p>}
          <div className="contact-row">
            {CONTACTS.map(([key, Icon]) => {
              const value = byField(key)?.summary.trim();
              if (!value) return null;
              return isUrl(value) ? (
                <a key={key} className="contact-chip" href={safeUrl(value)} target="_blank" rel="noreferrer">
                  <Icon size={14} />
                  {value.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
                </a>
              ) : (
                <span key={key} className="contact-chip">
                  <Icon size={14} />
                  {value}
                </span>
              );
            })}
          </div>
        </div>
        <dl className="profile-stats">
          <div>
            <dt>Active entries</dt>
            <dd>{items.length}</dd>
          </div>
          <div>
            <dt>Waiting for review</dt>
            <dd>{data.pending.length}</dd>
          </div>
          <div>
            <dt>Removed</dt>
            <dd>{data.removed}</dd>
          </div>
        </dl>
      </section>

      {data.pending.length > 0 && (
        <section className="card spaced profile-pending" role="status">
          <h2>
            {data.pending.length} suggested change{data.pending.length === 1 ? "" : "s"} waiting for you
          </h2>
          <p>
            These came from the Profile chat, the assistant or Resume Studio. Check each one; confirming
            applies it everywhere, like your own edits. New resumes are paused until then.
          </p>
          <ul className="pending-list">
            {data.pending.map((entry) => (
              <li key={entry.id}>
                <div>
                  <b>{entry.label}</b>
                  <small className="muted">
                    {" "}
                    · {KIND_LABELS[entry.kind] || entry.kind}
                    {entry.deleted ? " · suggested for removal" : ""}
                  </small>
                </div>
                <div className="actions">
                  {entry.deleted && (
                    <button
                      className="text-button"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => api("/v2/profile/items/" + encodeURIComponent(entry.id) + "/restore", "POST"),
                          `${entry.label} kept.`,
                        )
                      }
                    >
                      Keep it
                    </button>
                  )}
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () => api("/v2/profile/reconcile", "POST", { ids: [entry.id] }),
                        entry.deleted ? `${entry.label} removed.` : `${entry.label} confirmed.`,
                      )
                    }
                  >
                    <CheckCheck size={15} /> {entry.deleted ? "Remove" : "Confirm"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
          <div className="actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                run(
                  () => api("/v2/profile/reconcile", "POST", {}),
                  "Suggested changes confirmed. New resumes can be created again.",
                )
              }
            >
              <CheckCheck size={17} />
              {busy ? "Confirming…" : "Confirm all"}
            </button>
          </div>
        </section>
      )}

      {/* A div, not <nav>: the sidebar's global nav styles must not apply here. */}
      <div className="profile-nav" role="navigation" aria-label="Profile sections">
        {SECTIONS.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" onClick={() => scrollTo(id)}>
            <Icon size={14} />
            {label}
            <span>{counts[id]}</span>
          </button>
        ))}
      </div>

      <Section id="basics" title="Basics" count={basics.length} hint={SECTIONS[0].hint} onAdd={() => openAdd("personal")}>
        {grouped(basics, PERSONAL_ORDER).map((group) => (
          <div className="profile-group" key={group.name}>
            <h3>{group.name}</h3>
            <dl className="fact-list">
              {group.entries.map((entry) => (
                <div className="fact-row" key={entry.id}>
                  <dt>{entry.label}</dt>
                  <dd>
                    <PersonalValue entry={entry} />
                  </dd>
                  <EntryTools entry={entry} {...tools} quiet />
                </div>
              ))}
            </dl>
          </div>
        ))}
      </Section>

      <Section id="search" title="Job search" count={search.length} hint={SECTIONS[1].hint}>
        <div className="entry-list">
          {search.map((entry) => (
            <article className="entry-card" key={entry.id}>
              <div className="entry-head">
                <h3>{entry.label}</h3>
                <EntryTools entry={entry} {...tools} quiet />
              </div>
              <PersonalValue entry={entry} />
            </article>
          ))}
        </div>
      </Section>

      <Section id="experience" title="Experience" count={counts.experience} hint={SECTIONS[2].hint} onAdd={() => openAdd("experience")}>
        <div className="entry-list">
          {[...ofKind("experience")].sort(byDateDesc).map((entry) => (
            <article className={"entry-card" + (entry.status === "hold" ? " entry-held" : "")} key={entry.id}>
              <div className="entry-head">
                <div>
                  <h3>{entry.label}</h3>
                  <p className="entry-sub">
                    {[field(entry, "employer"), field(entry, "location")].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="entry-side">
                  {field(entry, "dates") && <span className="entry-dates">{field(entry, "dates")}</span>}
                  <EntryTools entry={entry} {...tools} />
                </div>
              </div>
              <HeldNote entry={entry} />
              <Lines items={asList(entry.fields.bullets)} />
              <About entry={entry} />
            </article>
          ))}
        </div>
      </Section>

      <Section id="projects" title="Projects" count={counts.projects} hint={SECTIONS[3].hint} onAdd={() => openAdd("project")}>
        <div className="knowledge-grid">
          {ofKind("project").map((entry) => (
            <article className="entry-card" key={entry.id}>
              <div className="entry-head">
                <h3>{entry.label}</h3>
                <EntryTools entry={entry} {...tools} />
              </div>
              <p className="entry-sub">
                {[field(entry, "timeframe"), field(entry, "ownership")].filter(Boolean).join(" · ")}
              </p>
              {asList(entry.fields.technologies).length > 0 && <Tags items={asList(entry.fields.technologies)} />}
              <HeldNote entry={entry} />
              {entry.off_resumes && <p className="entry-note">Not on resumes yet. {entry.off_resumes}</p>}
              <Lines items={asList(entry.fields.bullets)} />
              <About entry={entry} />
            </article>
          ))}
        </div>
      </Section>

      <Section id="skills" title="Skills" count={counts.skills} hint={SECTIONS[4].hint} onAdd={() => openAdd("skill")}>
        {grouped(ofKind("skill"), SKILL_ORDER).map((group) => (
          <div className="profile-group" key={group.name}>
            <h3>{group.name}</h3>
            {SKILL_HINTS[group.name] && <p className="muted small">{SKILL_HINTS[group.name]}</p>}
            <div className="knowledge-grid">
              {group.entries.map((entry) => (
                <article className="entry-card compact" key={entry.id}>
                  <div className="entry-head">
                    <h4>{entry.label}</h4>
                    <EntryTools entry={entry} {...tools} quiet />
                  </div>
                  <Lines items={asList(entry.fields.skills)} tone={group.name === "Honest gaps" ? "red" : ""} />
                  <About entry={entry} />
                </article>
              ))}
            </div>
          </div>
        ))}
      </Section>

      <Section id="education" title="Education" count={counts.education} hint={SECTIONS[5].hint} onAdd={() => openAdd("education")}>
        <div className="entry-list">
          {[...ofKind("education")].sort(byDateDesc).map((entry) => (
            <article className="entry-card" key={entry.id}>
              <div className="entry-head">
                <div>
                  <h3>{entry.label}</h3>
                  <p className="entry-sub">
                    {[field(entry, "degree"), field(entry, "location")].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <div className="entry-side">
                  {field(entry, "dates") && <span className="entry-dates">{field(entry, "dates")}</span>}
                  <EntryTools entry={entry} {...tools} />
                </div>
              </div>
              {field(entry, "status") && <p className="entry-line">{field(entry, "status")}</p>}
              {!entry.in_sync && <p className="entry-note preserve">{entry.summary}</p>}
              <HeldNote entry={entry} />
              <About entry={entry} />
            </article>
          ))}
        </div>
      </Section>

      <Section
        id="certifications"
        title="Certifications"
        count={counts.certifications}
        hint={SECTIONS[6].hint}
        onAdd={() => openAdd("certification")}
      >
        <div className="entry-list">
          {ofKind("certification").map((entry) => (
            <article className="entry-card" key={entry.id}>
              <div className="entry-head">
                <div>
                  <h3>{entry.label}</h3>
                  <p className="entry-sub">
                    {[field(entry, "issuer"), field(entry, "date")].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <EntryTools entry={entry} {...tools} />
              </div>
              {field(entry, "credential") && (
                <p className="entry-line">
                  <Value text={field(entry, "credential")} />
                </p>
              )}
              {!entry.in_sync && <p className="entry-note preserve">{entry.summary}</p>}
              <HeldNote entry={entry} />
              <About entry={entry} />
            </article>
          ))}
        </div>
      </Section>

      <Section id="facts" title="Other facts" count={counts.facts} hint={SECTIONS[7].hint} onAdd={() => openAdd("fact")}>
        <dl className="fact-list">
          {[...ofKind("fact")]
            .sort((a, b) => (a.group || "").localeCompare(b.group || "") || a.label.localeCompare(b.label))
            .map((entry) => (
              <div className="fact-row" key={entry.id}>
                <dt>
                  {entry.label}
                  {entry.group && entry.group !== entry.label && <small className="muted">{entry.group}</small>}
                </dt>
                <dd>
                  {asList(entry.fields.details).length > 1 ? (
                    <Lines items={asList(entry.fields.details)} />
                  ) : field(entry, "details") || !isHeld(entry) ? (
                    <Value text={field(entry, "details")} />
                  ) : null}
                  <HeldNote entry={entry} />
                </dd>
                <EntryTools entry={entry} {...tools} quiet />
              </div>
            ))}
        </dl>
      </Section>

      <details className="card spaced">
        <summary>
          <b>Chat to update Profile</b> — propose a change, review it, confirm
        </summary>
        <p>
          Describe an addition, correction or removal in plain words. Nothing changes until you
          review the proposed change and confirm it.
        </p>
        <Field label="Profile request">
          <textarea
            rows={3}
            value={chat}
            onChange={(e) => setChat(e.target.value)}
            placeholder="Examples: add skill: Apache Airflow | Orchestrated the InsOps pipelines; I finished the AWS Cloud Practitioner course in September"
          />
        </Field>
        <div className="actions">
          <button
            className="secondary"
            disabled={busy || !chat.trim()}
            onClick={async () => {
              setBusy(true);
              try {
                const requestId = crypto.randomUUID();
                const preview = await api<any>("/v2/profile/chat/preview", "POST", {
                  message: chat,
                  request_id: requestId,
                  expected_revision: data.revision,
                });
                setChatRequest(requestId);
                setChatPreview(preview);
              } catch (e) {
                notify((e as Error).message, true);
              } finally {
                setBusy(false);
              }
            }}
          >
            Preview changes
          </button>
        </div>
        {chatPreview && (
          <div className="callout warning chat-preview">
            <div>
              <b>Confirm these changes</b>
              {chatPreview.proposed_changes.ai_note && <p>{chatPreview.proposed_changes.ai_note}</p>}
              <ul className="change-list">
                {(chatPreview.proposed_changes.changes || []).map((change: any, i: number) => {
                  const line = describeChange(change, labels);
                  return (
                    <li key={i}>
                      <b>{line.action}</b> {line.name}
                      {line.text && <p className="preserve">{line.text}</p>}
                      {change.rationale && <small className="muted">Why: {change.rationale}</small>}
                    </li>
                  );
                })}
              </ul>
              <div className="actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={async () => {
                    const applied = await run(
                      () =>
                        api("/v2/profile/chat/apply", "POST", {
                          change_set_id: chatPreview.id,
                          request_id: chatRequest,
                          expected_revision: data.revision,
                        }),
                      "Profile change applied. Confirm it when you have checked it.",
                    );
                    if (applied) {
                      setChat("");
                      setChatPreview(null);
                    }
                  }}
                >
                  Confirm & apply
                </button>
                <button className="secondary" onClick={() => setChatPreview(null)}>
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
      </details>

      <details className="card spaced">
        <summary>
          <b>Advanced</b> — agents using this profile, original sources, full registry
        </summary>
        <h2>Agents working for you</h2>
        <div className="agent-grid">
          {data.agents.map((a) => (
            <article key={a.id}>
              <Badge tone={a.profile_access ? "neutral" : "green"}>
                {a.profile_access ? "Uses active profile" : "No candidate profile"}
              </Badge>
              <h3>{a.name}</h3>
              <p>{a.does}</p>
              <small>
                <b>Inputs:</b> {a.reads}
                <br />
                <b>Runs as:</b> {a.implementation}
              </small>
            </article>
          ))}
        </div>
        <details>
          <summary>Skills & workflow instructions</summary>
          {data.skills.map((s) => (
            <p key={s.name}>
              <b>{s.name}</b> — {s.purpose}
              <br />
              <code>{s.path}</code>
            </p>
          ))}
        </details>
        <h2>Original evidence & source documents</h2>
        <p>
          These preserved sources explain where your profile came from. Agents use the active entries
          above; removed entries remain only in the audit history and original documents.
        </p>
        {Object.entries(data.sources).map(([source, text]) => (
          <details key={source}>
            <summary>{source}</summary>
            <RichText text={text} />
          </details>
        ))}
        <details>
          <summary>Profile settings (profile.yml)</summary>
          <DataView value={data.configuration} />
        </details>
        <details>
          <summary>Complete evidence registry (evidence.yml)</summary>
          <DataView value={data.registry} />
        </details>
      </details>

      {edit && (
        <ProfileEditor
          key={edit.entry?.id || "new"}
          entry={edit.entry}
          initialKind={edit.kind}
          schema={data.schema}
          busy={busy}
          onSave={save}
          onClose={() => setEdit(null)}
        />
      )}
      {remove && (
        <Modal title="Remove profile entry" onClose={() => setRemove(null)}>
          <p>
            Remove <b>{remove.label}</b>? It comes off your base resume and open drafts now, and the
            job search and agents stop using it. The evidence registry keeps it on hold as a record,
            and resumes you have already sent are not changed.
          </p>
          <div className="actions">
            <button
              className="danger"
              disabled={busy}
              onClick={async () => {
                const removed = await run(
                  () => api("/v2/profile/items/" + encodeURIComponent(remove.id), "DELETE"),
                  "Removed.",
                );
                if (removed) setRemove(null);
              }}
            >
              Remove entry
            </button>
            <button className="secondary" onClick={() => setRemove(null)}>
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
