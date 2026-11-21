/** The application board's columns and move rules, kept free of React so every rule is tested.
 *
 * The rules mirror the server's (career.py move_status), so the board explains a refusal
 * before sending anything: "Applied" needs the real submission date, a recorded application
 * never moves back to Discovered or Tailored, and Tailored needs a prepared package.
 */
import type { Job } from "../types";

export type ColumnId = "saved" | "prepared" | "applied" | "interview" | "offer" | "closed";

export type Column = { id: ColumnId; label: string; hint: string; statuses: string[] };

export const COLUMNS: Column[] = [
  { id: "saved", label: "Discovered", hint: "Saved roles worth a look", statuses: ["saved"] },
  { id: "prepared", label: "Tailored", hint: "A resume package is ready", statuses: ["prepared"] },
  { id: "applied", label: "Applied", hint: "Sent, waiting for a reply", statuses: ["applied"] },
  { id: "interview", label: "Interview", hint: "In the interview process", statuses: ["interview"] },
  { id: "offer", label: "Offer", hint: "An offer is on the table", statuses: ["offer"] },
  { id: "closed", label: "Closed", hint: "Rejected, withdrawn or no reply", statuses: ["rejected", "withdrawn", "ghosted"] },
];

/** What a card in Closed was closed as; "ghosted" reads as "No reply". */
export const CLOSED_CHOICES = [
  { status: "rejected", label: "Rejected", hint: "They said no" },
  { status: "withdrawn", label: "Withdrawn", hint: "You pulled out" },
  { status: "ghosted", label: "No reply", hint: "You decided to stop waiting" },
] as const;

export const CLOSED_LABEL: Record<string, string> = Object.fromEntries(CLOSED_CHOICES.map((c) => [c.status, c.label]));

/** Statuses that mean an application was sent; they stay in the history. */
export const APPLIED_STATUSES = new Set(["applied", "interview", "offer", "rejected", "withdrawn", "ghosted"]);

export function columnFor(status: string): ColumnId {
  return COLUMNS.find((column) => column.statuses.includes(status))?.id ?? "saved";
}

export function columnLabel(id: ColumnId): string {
  return COLUMNS.find((column) => column.id === id)?.label ?? id;
}

export type MoveCheck =
  | { kind: "same" }
  | { kind: "ok"; status: string }
  /** Ask for the submission date first; `required` for Applied, optional later on. */
  | { kind: "needs_date"; status: string; required: boolean }
  | { kind: "choose_closed" }
  | { kind: "blocked"; reason: string; action?: "prepare" };

/** Whether this job can move to that column, and what to ask first. */
export function checkMove(job: Pick<Job, "status" | "application_date" | "folder">, target: ColumnId, hasEvidence: boolean): MoveCheck {
  if (columnFor(job.status) === target) return { kind: "same" };
  const recorded = APPLIED_STATUSES.has(job.status) || !!job.application_date || hasEvidence;
  if ((target === "saved" || target === "prepared") && recorded) {
    return {
      kind: "blocked",
      reason: "This application is already recorded, so it stays in your history. Use Withdrawn if you pulled out, or Undo straight after a move.",
    };
  }
  if (target === "prepared" && !job.folder) {
    return {
      kind: "blocked",
      reason: "Prepare the application first: Tailored means a resume package exists for this role.",
      action: "prepare",
    };
  }
  if (target === "closed") return { kind: "choose_closed" };
  if ((target === "applied" || target === "interview" || target === "offer") && !job.application_date && !hasEvidence) {
    return { kind: "needs_date", status: target, required: target === "applied" };
  }
  return { kind: "ok", status: target };
}

/** A local copy of a move, shown at once while the server records it. */
export type PendingMove = { status: string; application_date: string | null };

export function withPending(jobs: Job[], pending: Record<string, PendingMove>): Job[] {
  if (!Object.keys(pending).length) return jobs;
  return jobs.map((job) => (pending[job.id] ? { ...job, ...pending[job.id] } : job));
}

/** Jobs per column: best fit first in the early columns, most recent first after applying. */
export function groupByColumn(jobs: Job[]): Record<ColumnId, Job[]> {
  const groups = Object.fromEntries(COLUMNS.map((column) => [column.id, [] as Job[]])) as Record<ColumnId, Job[]>;
  for (const job of jobs) groups[columnFor(job.status)].push(job);
  const recent = (job: Job) => job.application_date || job.updated_at || job.created_at || "";
  for (const id of ["saved", "prepared"] as ColumnId[]) {
    groups[id].sort((a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1) || b.created_at.localeCompare(a.created_at));
  }
  for (const id of ["applied", "interview", "offer", "closed"] as ColumnId[]) {
    groups[id].sort((a, b) => recent(b).localeCompare(recent(a)));
  }
  return groups;
}

/** Whole days between an ISO date (or timestamp) and today, or null without a date. */
export function daysSince(stamp: string | null | undefined, today: string): number | null {
  if (!stamp) return null;
  const from = Date.parse(stamp.slice(0, 10) + "T00:00:00Z");
  const to = Date.parse(today.slice(0, 10) + "T00:00:00Z");
  if (Number.isNaN(from) || Number.isNaN(to)) return null;
  return Math.max(0, Math.round((to - from) / 86400000));
}

/** A short line about time in this stage: "Applied 12 days ago", "Saved today". */
export function stageAge(job: Job, today: string): string {
  if (job.status === "applied") {
    const days = daysSince(job.application_date, today);
    return days === null ? "Applied · date not recorded" : days === 0 ? "Applied today" : `Applied ${days} day${days === 1 ? "" : "s"} ago`;
  }
  const days = daysSince(job.updated_at || job.created_at, today);
  if (days === null) return "";
  const word = job.status === "saved" ? "Saved" : "Updated";
  return days === 0 ? `${word} today` : `${word} ${days} day${days === 1 ? "" : "s"} ago`;
}

/** A card's visible content, so an unchanged card is not re-rendered on every poll. */
export function cardSignature(job: Job, extra = ""): string {
  return [job.id, job.status, job.application_date, job.title, job.company, job.location, job.fit_score,
    job.sponsor_tier, job.posting_state, job.folder, job.record_source, job.updated_at, extra].join("|");
}
