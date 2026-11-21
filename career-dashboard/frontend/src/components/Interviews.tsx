/** Interviews the person records: a form, the list for one job, and the Dashboard's
 * upcoming-interviews card with a week strip. Times are the profile's local time (Dublin by
 * default); the .ics links give the person a file for their own calendar. Nothing is sent. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, CalendarPlus, Download, MapPin, Pencil, Trash2 } from "lucide-react";
import { API_BASE, api } from "../api";
import { AskAssistant, Empty, Field, Modal } from "./UI";
import type { Notify } from "./UI";
import type { Interview, Job } from "../types";

export const STAGES: [Interview["stage"], string][] = [
  ["screen", "Phone or video screen"],
  ["technical", "Technical interview"],
  ["onsite", "On-site interview"],
  ["final", "Final interview"],
  ["other", "Other interview"],
];

const ZONE = "Europe/Dublin";

/** "Tue 25 Nov · 14:00" in the profile's time zone. */
export function formatWhen(startsAt: string, zone = ZONE): string {
  const moment = new Date(startsAt);
  if (Number.isNaN(moment.getTime())) return startsAt;
  const day = new Intl.DateTimeFormat("en-IE", { weekday: "short", day: "numeric", month: "short", timeZone: zone }).format(moment);
  const time = new Intl.DateTimeFormat("en-IE", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: zone }).format(moment);
  return `${day} · ${time}`;
}

/** YYYY-MM-DD of an instant in the profile's time zone. */
export function localDay(startsAt: string | Date, zone = ZONE): string {
  const moment = typeof startsAt === "string" ? new Date(startsAt) : startsAt;
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: zone }).format(moment);
}

/** The seven days (Mon–Sun) of the week holding `today` (YYYY-MM-DD). */
export function weekDays(today: string): string[] {
  const base = new Date(today + "T12:00:00Z");
  const monday = new Date(base);
  monday.setUTCDate(base.getUTCDate() - ((base.getUTCDay() + 6) % 7));
  return Array.from({ length: 7 }, (_, i) => {
    const day = new Date(monday);
    day.setUTCDate(monday.getUTCDate() + i);
    return day.toISOString().slice(0, 10);
  });
}

/** The next interview per job, formatted, for board cards. */
export function nextInterviewByJob(items: Interview[], zone = ZONE): Record<string, string> {
  const out: Record<string, string> = {};
  for (const item of items) if (!out[item.job_id]) out[item.job_id] = formatWhen(item.starts_at, zone);
  return out;
}

export const calendarUrl = (id?: string) =>
  `${API_BASE}/v2/interviews/${id ? encodeURIComponent(id) + "/" : ""}calendar.ics`;

export function InterviewForm({ job, interview, notify, onSaved, onCancel }: {
  job: Pick<Job, "id" | "company" | "title" | "status">;
  interview?: Interview;
  notify: Notify;
  onSaved: (saved: Interview) => void;
  onCancel: () => void;
}) {
  const [stage, setStage] = useState<Interview["stage"]>(interview?.stage || "screen");
  const [when, setWhen] = useState(interview?.starts_at.slice(0, 16) || "");
  const [duration, setDuration] = useState(interview?.duration_min || 45);
  const [where, setWhere] = useState(interview?.where_text || "");
  const [notes, setNotes] = useState(interview?.notes || "");
  const canMove = !interview && ["saved", "prepared", "applied"].includes(job.status);
  const [move, setMove] = useState(canMove);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="interview-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          const body = { stage, starts_at: when, duration_min: duration, where_text: where, notes, move_to_interview: canMove && move };
          const saved = interview
            ? await api<Interview>(`/v2/interviews/${encodeURIComponent(interview.id)}`, "PUT", body)
            : await api<Interview>(`/v2/jobs/${encodeURIComponent(job.id)}/interviews`, "POST", body);
          notify(interview ? "Interview updated." : `Interview with ${job.company} saved. Download it for your calendar from the interview list.`);
          onSaved(saved);
        } catch (e) {
          notify((e as Error).message, true);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="form-grid">
        <Field label="Stage">
          <select value={stage} onChange={(e) => setStage(e.target.value as Interview["stage"])}>
            {STAGES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </Field>
        <Field label="Date and time (Irish time)">
          <input type="datetime-local" required value={when} onChange={(e) => setWhen(e.target.value)} />
        </Field>
        <Field label="Length">
          <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
            {[15, 30, 45, 60, 90, 120, 180, 240].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : `${m / 60} hour${m === 60 ? "" : "s"}`}</option>)}
          </select>
        </Field>
        <Field label="Where (address or video link)">
          <input value={where} maxLength={500} onChange={(e) => setWhere(e.target.value)} placeholder="Microsoft Teams, or the office address" />
        </Field>
      </div>
      <Field label="Your notes">
        <textarea rows={3} maxLength={5000} value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="Who you are meeting, what to prepare" />
      </Field>
      {canMove && (
        <label className="check-line">
          <input type="checkbox" checked={move} onChange={(e) => setMove(e.target.checked)} /> Also move {job.company} to the Interview stage
        </label>
      )}
      <div className="actions">
        <button type="submit" className="primary" disabled={busy || !when}>{busy ? "Saving…" : interview ? "Save interview" : "Add interview"}</button>
        <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

/** The Dashboard's prompt after a card moves to Interview. */
export function InterviewDialog({ job, notify, onClose, onSaved }: {
  job: Job;
  notify: Notify;
  onClose: () => void;
  onSaved: () => void;
}) {
  return (
    <Modal title={`Add the interview with ${job.company}`} onClose={onClose}>
      <p className="small">{job.title}. Optional: add it now and it shows on your Dashboard, on the card and in a calendar file.</p>
      <InterviewForm job={job} notify={notify} onCancel={onClose} onSaved={() => { onSaved(); onClose(); }} />
    </Modal>
  );
}

/** Every interview for one job, with add, edit, remove and calendar files. */
export function JobInterviews({ job, notify, refresh }: { job: Job; notify: Notify; refresh: () => Promise<void> }) {
  const [items, setItems] = useState<Interview[] | null>(null);
  const [editing, setEditing] = useState<Interview | "new" | null>(null);
  const load = useCallback(async () => {
    try {
      setItems((await api<{ interviews: Interview[] }>(`/v2/interviews?job_id=${encodeURIComponent(job.id)}`)).interviews);
    } catch (e) {
      notify((e as Error).message, true);
    }
  }, [job.id, notify]);
  useEffect(() => { void load(); }, [load]);
  const saved = async () => {
    setEditing(null);
    await load();
    await refresh();
  };
  if (editing) {
    return <InterviewForm job={job} interview={editing === "new" ? undefined : editing} notify={notify}
      onSaved={() => void saved()} onCancel={() => setEditing(null)} />;
  }
  return (
    <div className="job-interviews">
      <div className="actions">
        <button type="button" className="primary" onClick={() => setEditing("new")}><CalendarPlus size={16} /> Add interview</button>
        <AskAssistant label="Prepare with the assistant" prompts={[
          { label: "Prepare me for this interview", text: `Prepare me for my interview at ${job.company} for ${job.title}.`, send: false },
          { label: "Mock interview", text: `Run a mock interview for ${job.company} — ${job.title}, one question at a time.`, send: false },
        ]} />
      </div>
      {items === null ? <p className="small muted">Loading interviews…</p> : !items.length ? (
        <Empty title="No interviews recorded">Add one when it is booked; it appears on your Dashboard and the board.</Empty>
      ) : (
        <ul className="interview-list">
          {items.map((item) => (
            <li key={item.id}>
              <CalendarClock size={18} aria-hidden="true" />
              <span>
                <b>{formatWhen(item.starts_at)}</b> · {item.stage_label} · {item.duration_min} min
                {item.where_text && <small><MapPin size={12} aria-hidden="true" /> {item.where_text}</small>}
                {item.notes && <small className="preserve">{item.notes}</small>}
              </span>
              <span className="actions">
                <a className="icon-button" href={calendarUrl(item.id)} download title="Download for your calendar (.ics)" aria-label={`Download ${item.stage_label} for your calendar`}><Download size={16} /></a>
                <button type="button" className="icon-button" aria-label={`Edit ${item.stage_label}`} onClick={() => setEditing(item)}><Pencil size={16} /></button>
                <button type="button" className="icon-button danger-icon" aria-label={`Remove ${item.stage_label}`}
                  onClick={async () => {
                    try {
                      await api(`/v2/interviews/${encodeURIComponent(item.id)}`, "DELETE");
                      await load();
                      await refresh();
                      notify("Interview removed.");
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}><Trash2 size={16} /></button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The Dashboard card: this week as a strip, then the next two weeks as a list. */
export function UpcomingInterviews({ items, today, onOpen }: { items: Interview[]; today: string; onOpen: (jobId: string) => void }) {
  const days = useMemo(() => weekDays(today), [today]);
  const byDay = useMemo(() => {
    const map: Record<string, Interview[]> = {};
    for (const item of items) (map[localDay(item.starts_at)] ||= []).push(item);
    return map;
  }, [items]);
  return (
    <section className="card upcoming-interviews" aria-label="Upcoming interviews">
      <div className="section-title">
        <h2><CalendarClock size={19} /> Upcoming interviews</h2>
        {!!items.length && <a className="text-button" href={calendarUrl()} download><Download size={15} /> Calendar file</a>}
      </div>
      <ol className="week-strip">
        {days.map((day) => {
          const count = byDay[day]?.length || 0;
          const label = new Intl.DateTimeFormat("en-IE", { weekday: "short", timeZone: "UTC" }).format(new Date(day + "T12:00:00Z"));
          return (
            <li key={day} className={(day === today ? "today " : "") + (count ? "busy" : "")}
              aria-label={`${label} ${day.slice(8)}: ${count ? `${count} interview${count === 1 ? "" : "s"}` : "no interviews"}`}>
              <small>{label}</small>
              <b>{Number(day.slice(8))}</b>
              <i aria-hidden="true">{count ? "•".repeat(Math.min(count, 3)) : ""}</i>
            </li>
          );
        })}
      </ol>
      {items.length ? (
        <ul className="upcoming-list">
          {items.slice(0, 5).map((item) => (
            <li key={item.id}>
              <button type="button" className="link-button" onClick={() => onOpen(item.job_id)}>
                <b>{formatWhen(item.starts_at)}</b>
                <span>{item.company} · {item.title}</span>
                <small>{item.stage_label}{item.where_text ? ` · ${item.where_text}` : ""}</small>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small muted">No interviews in the next two weeks. Drag a card to Interview, or add one from a job's details.</p>
      )}
    </section>
  );
}
