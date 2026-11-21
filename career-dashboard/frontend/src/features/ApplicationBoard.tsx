/** The application board: one column per stage; drag a card (or use its Move menu) to change
 * the status. Every rule is in boardModel.ts and is checked again by the server; a move is
 * shown at once, recorded, and can be undone from the toast. */
import { memo, useCallback, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import type { Announcements, DragEndEvent, DragStartEvent } from "@dnd-kit/core";
import { ArrowLeftRight, CalendarClock, ChevronDown, ChevronRight, FileText, GripVertical, Info, MailQuestion, Trash2 } from "lucide-react";
import { api } from "../api";
import { Badge, Field, Modal } from "../components/UI";
import type { Notify } from "../components/UI";
import { FitBadge, TierBadge } from "../components/JobList";
import { SalaryBadge } from "../components/SalaryEvidence";
import type { Job } from "../types";
import {
  CLOSED_CHOICES,
  CLOSED_LABEL,
  COLUMNS,
  cardSignature,
  checkMove,
  columnFor,
  columnLabel,
  groupByColumn,
  stageAge,
  withPending,
} from "./boardModel";
import type { ColumnId, MoveCheck, PendingMove } from "./boardModel";

type Props = {
  jobs: Job[];
  /** Job ids with a confirmed application email: they count as applied without a typed date. */
  evidence: Set<string>;
  today: string;
  /** Next interview per job id, already formatted ("Tue 25 Nov · 14:00"). */
  nextInterview?: Record<string, string>;
  /** Job ids with a follow-up or thank-you note due. */
  followUpDue?: Set<string>;
  notify: Notify;
  refresh: () => Promise<void>;
  onOpen: (id: string) => void;
  onDetails?: (id: string) => void;
  onCoverLetter?: (job: Job) => void;
  onRemove?: (job: Job) => void;
  onInterviewAdded?: (job: Job) => void;
};

type Ask = { job: Job; check: MoveCheck };

export default function ApplicationBoard(props: Props) {
  const { jobs, evidence, today } = props;
  const [pending, setPending] = useState<Record<string, PendingMove>>({});
  const [active, setActive] = useState<Job | null>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [openClosed, setOpenClosed] = useState(false);
  const busy = useRef(new Set<string>());
  // The parent's handlers change on every render; cards get stable wrappers that call the
  // latest ones, so a card re-renders only when what it shows changes.
  const latest = useRef(props);
  latest.current = props;
  const handlers = useMemo(() => ({
    notify: ((...args) => latest.current.notify(...args)) as Notify,
    refresh: () => latest.current.refresh(),
    onOpen: (id: string) => latest.current.onOpen(id),
    onDetails: (id: string) => latest.current.onDetails?.(id),
    onCoverLetter: (job: Job) => latest.current.onCoverLetter?.(job),
    onRemove: (job: Job) => latest.current.onRemove?.(job),
  }), []);
  const { notify, refresh, onOpen } = handlers;

  const shown = useMemo(() => withPending(jobs, pending), [jobs, pending]);
  const groups = useMemo(() => groupByColumn(shown), [shown]);
  const byId = useMemo(() => new Map(shown.map((job) => [job.id, job])), [shown]);

  const sensors = useSensors(
    // A few pixels of movement before a drag starts, so a click still opens the card.
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    // On a phone, press and hold to pick a card up; a swipe still scrolls the board.
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor),
  );

  const send = useCallback(
    async (job: Job, status: string, applicationDate: string | null) => {
      if (busy.current.has(job.id)) return;
      busy.current.add(job.id);
      const before = job.status;
      setPending((was) => ({ ...was, [job.id]: { status, application_date: applicationDate || job.application_date } }));
      try {
        const result = await api<{ job: Job; event_id: number | null }>(`/v2/jobs/${encodeURIComponent(job.id)}/status`, "POST", {
          status,
          application_date: applicationDate || null,
          expected_status: before,
        });
        // The move is recorded: say so (with Undo) at once; the card already shows it, and the
        // fresh summary below only confirms it.
        const label = CLOSED_LABEL[status] || columnLabel(columnFor(status));
        if (result.event_id) {
          const eventId = result.event_id;
          notify(`${job.company} moved to ${label}.`, false, {
            label: "Undo",
            run: () => {
              void (async () => {
                try {
                  await api(`/v2/jobs/${encodeURIComponent(job.id)}/status/undo`, "POST", { event_id: eventId });
                  notify(`Move undone: ${job.company} is back where it was.`);
                  await refresh().catch(() => {});
                } catch (e) {
                  notify((e as Error).message, true);
                }
              })();
            },
          });
        }
        if (status === "interview") latest.current.onInterviewAdded?.(result.job);
        await refresh().catch(() => {});
      } catch (e) {
        notify((e as Error).message, true);
        await refresh().catch(() => {});
      } finally {
        busy.current.delete(job.id);
        setPending((was) => {
          const next = { ...was };
          delete next[job.id];
          return next;
        });
      }
    },
    [notify, refresh],
  );

  const attempt = useCallback(
    (job: Job, target: ColumnId) => {
      const check = checkMove(job, target, evidence.has(job.id));
      if (check.kind === "same") return;
      if (check.kind === "blocked") {
        notify(check.reason, true, check.action === "prepare" ? { label: "Open Resume Studio", run: () => onOpen(job.id) } : undefined);
        return;
      }
      if (check.kind === "ok") {
        void send(job, check.status, null);
        return;
      }
      setAsk({ job, check });
    },
    [evidence, notify, onOpen, send],
  );

  const onDragStart = (event: DragStartEvent) => setActive(byId.get(String(event.active.id)) ?? null);
  const onDragEnd = (event: DragEndEvent) => {
    setActive(null);
    const job = byId.get(String(event.active.id));
    if (job && event.over) attempt(job, event.over.id as ColumnId);
  };

  const announcements: Announcements = {
    onDragStart: ({ active: a }) => `Picked up ${describe(byId.get(String(a.id)))}. Use the arrow keys to choose a column, Space to drop.`,
    onDragOver: ({ active: a, over }) => (over ? `${describe(byId.get(String(a.id)))} is over ${columnLabel(over.id as ColumnId)}.` : `Not over a column.`),
    onDragEnd: ({ active: a, over }) => (over ? `Dropped ${describe(byId.get(String(a.id)))} on ${columnLabel(over.id as ColumnId)}.` : "Dropped outside the board; nothing changed."),
    onDragCancel: ({ active: a }) => `Move cancelled. ${describe(byId.get(String(a.id)))} stays where it was.`,
  };

  return (
    <>
      <DndContext
        sensors={sensors}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActive(null)}
        accessibility={{
          announcements,
          screenReaderInstructions: {
            draggable: "To move this application, press Space, use the arrow keys to choose a column, then press Space again. Escape cancels.",
          },
        }}
      >
        <div className={"board" + (openClosed ? " closed-open" : "")} aria-label="Applications by stage">
          {COLUMNS.map((column) => {
            const items = groups[column.id];
            // Closed stays folded while dragging (no layout jump); the folded column still takes a drop.
            const collapsed = column.id === "closed" && !openClosed;
            const verdict = active ? checkMove(active, column.id, evidence.has(active.id)).kind : null;
            return (
              <BoardColumn key={column.id} id={column.id} label={column.label} hint={column.hint} count={items.length}
                verdict={verdict} collapsed={collapsed}
                onToggle={column.id === "closed" ? () => setOpenClosed((open) => !open) : undefined}>
                {!collapsed && items.map((job) => (
                  <BoardCard key={job.id} job={job} today={today}
                    signature={cardSignature(job, `${props.nextInterview?.[job.id] || ""}|${props.followUpDue?.has(job.id) ? 1 : 0}|${pending[job.id] ? 1 : 0}`)}
                    nextInterview={props.nextInterview?.[job.id]} followUp={!!props.followUpDue?.has(job.id)}
                    saving={!!pending[job.id]} dragging={active?.id === job.id}
                    onOpen={onOpen}
                    onDetails={props.onDetails ? handlers.onDetails : undefined}
                    onCoverLetter={props.onCoverLetter ? handlers.onCoverLetter : undefined}
                    onRemove={props.onRemove ? handlers.onRemove : undefined}
                    onMove={attempt} />
                ))}
                {!collapsed && !items.length && <p className="board-empty">{active ? "Drop here" : "Nothing here yet"}</p>}
              </BoardColumn>
            );
          })}
        </div>
        <DragOverlay dropAnimation={null}>
          {active ? <CardBody job={active} today={today} nextInterview={props.nextInterview?.[active.id]} followUp={!!props.followUpDue?.has(active.id)} /> : null}
        </DragOverlay>
      </DndContext>
      {ask && <MoveDialog ask={ask} today={today} onCancel={() => setAsk(null)}
        onConfirm={(status, date) => {
          const { job } = ask;
          setAsk(null);
          void send(job, status, date);
        }} />}
    </>
  );
}

function describe(job?: Job) {
  return job ? `${job.title} at ${job.company}` : "the application";
}

function BoardColumn({ id, label, hint, count, verdict, collapsed, onToggle, children }: {
  id: ColumnId;
  label: string;
  hint: string;
  count: number;
  verdict: MoveCheck["kind"] | null;
  collapsed: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id });
  const state = verdict === null || verdict === "same" ? "" : verdict === "blocked" ? " no-drop" : " can-drop";
  return (
    <section ref={setNodeRef} className={`board-column col-${id}${state}${isOver ? " over" : ""}${collapsed ? " collapsed" : ""}`}
      aria-label={`${label}: ${count} ${count === 1 ? "application" : "applications"}`}>
      <header className="board-column-head">
        {onToggle ? (
          <button type="button" className="board-toggle" aria-expanded={!collapsed} onClick={onToggle}>
            {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
            <span>{label}</span>
          </button>
        ) : <span>{label}</span>}
        <b className="board-count">{count}</b>
      </header>
      <p className="board-hint">{hint}</p>
      <div className="board-cards">{children}</div>
    </section>
  );
}

type CardProps = {
  job: Job;
  today: string;
  signature: string;
  nextInterview?: string;
  followUp: boolean;
  saving: boolean;
  dragging: boolean;
  onOpen: (id: string) => void;
  onDetails?: (id: string) => void;
  onCoverLetter?: (job: Job) => void;
  onRemove?: (job: Job) => void;
  onMove: (job: Job, target: ColumnId) => void;
};

/** One card. Re-rendered only when what it shows changes (its signature), not on every poll. */
const BoardCard = memo(function BoardCard({ job, today, nextInterview, followUp, saving, dragging, onOpen, onDetails, onCoverLetter, onRemove, onMove }: CardProps) {
  const { setNodeRef, listeners, attributes } = useDraggable({ id: job.id });
  // Mouse and touch pick the card up anywhere; the keyboard uses the grip, so Enter on the
  // card's own buttons still opens or acts instead of starting a drag.
  const { onKeyDown, ...pointer } = (listeners || {}) as Record<string, (event: unknown) => void>;
  const column = columnFor(job.status);
  const removable = !!onRemove && ["saved", "prepared"].includes(job.status);
  return (
    <article ref={setNodeRef} {...pointer} className={"board-card" + (dragging ? " dragging" : "") + (saving ? " saving" : "")}>
      <button type="button" className="board-grip" {...attributes} onKeyDown={onKeyDown}
        aria-label={`Move ${job.title} at ${job.company}`} title="Drag to another column">
        <GripVertical size={15} aria-hidden="true" />
      </button>
      <button type="button" className="board-open" onClick={() => onOpen(job.id)} title="Open in Resume Studio">
        <CardBody job={job} today={today} nextInterview={nextInterview} followUp={followUp} />
      </button>
      <footer className="board-card-actions">
        {/* The native menu, shown as an icon: keyboard and phone pickers keep working. */}
        <label className="board-move" title="Move to another stage">
          <ArrowLeftRight size={15} aria-hidden="true" />
          <span className="board-move-word">Move</span>
          <select aria-label={`Move ${job.company} ${job.title} to`} value=""
            onChange={(event) => {
              const value = event.target.value;
              if (!value) return;
              onMove(job, value as ColumnId);
            }}>
            <option value="">Move to…</option>
            {COLUMNS.filter((c) => c.id !== column).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
        </label>
        {onDetails && (
          <button type="button" className="icon-button" aria-label={`Details and progress for ${job.company}`} title="Details, notes and interviews"
            onClick={() => onDetails(job.id)}>
            <Info size={15} />
          </button>
        )}
        {onCoverLetter && job.record_source !== "gmail" && (
          <button type="button" className="icon-button" aria-label={`Cover letter for ${job.company}`} title="Generate a cover letter"
            onClick={() => onCoverLetter(job)}>
            <FileText size={15} />
          </button>
        )}
        {removable && (
          <button type="button" className="icon-button danger-icon" aria-label={`Remove ${job.company} ${job.title}`} title="Remove unsuitable role"
            onClick={() => onRemove!(job)}>
            <Trash2 size={15} />
          </button>
        )}
      </footer>
    </article>
  );
}, (a, b) => a.signature === b.signature && a.dragging === b.dragging && a.onOpen === b.onOpen && a.onMove === b.onMove
  && a.onDetails === b.onDetails && a.onCoverLetter === b.onCoverLetter && a.onRemove === b.onRemove && a.today === b.today);

export function CardBody({ job, today, nextInterview, followUp }: { job: Job; today: string; nextInterview?: string; followUp: boolean }) {
  return (
    <span className="board-card-body">
      <span className="board-card-title">
        <span className="company-avatar small" aria-hidden="true">{job.company.slice(0, 2).toUpperCase()}</span>
        <span>
          <strong>{job.title}</strong>
          <small>{job.company}{job.location ? ` · ${job.location}` : ""}</small>
        </span>
      </span>
      <span className="job-chips">
        {CLOSED_LABEL[job.status] && <Badge tone={job.status === "rejected" ? "red" : "neutral"}>{CLOSED_LABEL[job.status]}</Badge>}
        <FitBadge job={job} />
        <TierBadge job={job} />
        <SalaryBadge job={job} />
        {job.posting_state === "expired" && <Badge tone="red">posting closed</Badge>}
        {job.posting_state === "needs_review" && <Badge tone="amber">check posting</Badge>}
      </span>
      {(nextInterview || followUp) && (
        <span className="board-card-flags">
          {nextInterview && <span className="board-flag interview"><CalendarClock size={13} aria-hidden="true" /> {nextInterview}</span>}
          {followUp && <span className="board-flag follow"><MailQuestion size={13} aria-hidden="true" /> Follow-up due</span>}
        </span>
      )}
      <small className="board-age">{stageAge(job, today)}</small>
    </span>
  );
}

/** The question a move needs answered first: the submission date, or how it closed. */
function MoveDialog({ ask, today, onCancel, onConfirm }: {
  ask: Ask;
  today: string;
  onCancel: () => void;
  onConfirm: (status: string, date: string | null) => void;
}) {
  const [date, setDate] = useState("");
  const { job, check } = ask;
  if (check.kind === "choose_closed") {
    return (
      <Modal title={`How did ${job.company} close?`} onClose={onCancel}>
        <p className="small">{job.title}. This stays in your history, so it is never suggested again.</p>
        <div className="closed-choices">
          {CLOSED_CHOICES.map((choice) => (
            <button key={choice.status} type="button" className="secondary" onClick={() => onConfirm(choice.status, null)}>
              <b>{choice.label}</b>
              <small>{choice.hint}</small>
            </button>
          ))}
        </div>
      </Modal>
    );
  }
  if (check.kind !== "needs_date") return null;
  const label = columnLabel(check.status as ColumnId);
  return (
    <Modal title={`When did you apply to ${job.company}?`} onClose={onCancel}>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (check.required && !date) return;
        onConfirm(check.status, date || null);
      }}>
        <p className="small">
          {check.required
            ? "Moving to Applied records the day you actually submitted. Preparing a resume is not an application, and an email's arrival date is not the submission date."
            : `Moving to ${label}. Add the day you submitted the application if you know it; you can leave it empty.`}
        </p>
        <Field label="Submission date">
          <input type="date" max={today} value={date} required={check.required} autoFocus onChange={(e) => setDate(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" className="primary" disabled={check.required && !date}>Move to {label}</button>
          <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
