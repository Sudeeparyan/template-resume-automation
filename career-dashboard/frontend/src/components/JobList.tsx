import { ArrowUpRight, FileText, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Empty } from "./UI";
import { useMarket } from "../profiles";
import type { Job } from "../types";
export const statuses = [
  "saved",
  "prepared",
  "applied",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
];
// The pipeline reads as stages; the stored status codes never change.
export const statusLabel: Record<string, string> = {
  saved: "Discovered",
  prepared: "Tailored",
  applied: "Applied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};
export function FitBadge({ job }: { job: Job }) {
  if (typeof job.fit_score !== "number") return null;
  return (
    <Badge
      tone={job.fit_score >= 70 ? "green" : job.fit_score >= 50 ? "amber" : "neutral"}
      title={job.fit_rationale || "Fit score against your profile"}
    >
      Fit {job.fit_score}
    </Badge>
  );
}
export const tierRank: Record<string, number> = { S: 1, A: 2, B: 3, C: 4 };
export const tierTitle: Record<string, string> = {
  S: "Cap-exempt employer: files H-1B year-round, no lottery",
  A: "Posting says it sponsors",
  B: "Proven H-1B sponsor; posting is silent",
  C: "Posting is silent, no H-1B record; still worth applying",
};
/** The tier wording for this tab's profile: its country's own labels, else the US ones above. */
export function useTierTitles(): Record<string, string> {
  const { tierLabels } = useMarket();
  return { ...tierTitle, ...tierLabels };
}
export function TierBadge({ job, long = false }: { job: Job; long?: boolean }) {
  const tier = job.sponsor_tier || "C";
  const titles = useTierTitles();
  const label = job.sponsor_evidence?.label || titles[tier];
  return (
    <Badge tone={tier === "S" ? "green" : tier === "A" ? "lime" : "neutral"} title={label}>
      {long ? `Tier ${tier} · ${label}` : `Tier ${tier}`}
    </Badge>
  );
}
export function JobList({
  jobs,
  onSelect,
  onCoverLetter,
  onRemove,
  compact = false,
}: {
  jobs: Job[];
  onSelect: (id: string) => void;
  onCoverLetter?: (job: Job) => void;
  onRemove?: (job: Job) => void;
  compact?: boolean;
}) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [tier, setTier] = useState("all");
  const shown = jobs
    .filter(
      (j) =>
        (status === "all" || j.status === status) &&
        (tier === "all" || (j.sponsor_tier || "C") === tier) &&
        `${j.company} ${j.title} ${j.location}`
          .toLowerCase()
          .includes(q.toLowerCase()),
    )
    // Best sponsorship odds first (S cap-exempt → A says yes → B history → C silent), newest inside a tier.
    .sort(
      (a, b) =>
        (tierRank[a.sponsor_tier || "C"] ?? 4) - (tierRank[b.sponsor_tier || "C"] ?? 4) ||
        b.created_at.localeCompare(a.created_at),
    );
  return (
    <>
      <div className="list-toolbar">
        <div className="search-input">
          <Search size={18} />
          <input
            aria-label="Search companies and roles"
            placeholder="Search companies or roles…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select
          aria-label="Filter application status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="all">All statuses</option>
          {statuses.map((s) => (
            <option key={s} value={s}>
              {statusLabel[s] || s}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter sponsorship tier"
          value={tier}
          onChange={(e) => setTier(e.target.value)}
        >
          <option value="all">All tiers</option>
          <option value="S">S · cap-exempt</option>
          <option value="A">A · says it sponsors</option>
          <option value="B">B · proven sponsor</option>
          <option value="C">C · silent</option>
        </select>
        <span className="muted">{shown.length} jobs</span>
      </div>
      {!shown.length ? (
        <Empty title="No jobs in this view">
          Try another filter or save a new opportunity.
        </Empty>
      ) : (
        <div className="job-list">
          {shown.map((j) => (
            <div key={j.id} className="job-row">
              <button
                type="button"
                className="job-open"
                onClick={() => onSelect(j.id)}
              >
                <span className="company-avatar">
                  {j.company.slice(0, 2).toUpperCase()}
                </span>
                <span className="job-main">
                  <strong>{j.title}</strong>
                  <span>
                    {j.company}{" "}
                    <span className="job-location">· {j.location}</span>
                  </span>
                </span>
                <span className="job-meta">
                  <Badge
                    tone={
                      ["applied", "offer", "interview"].includes(j.status)
                        ? "green"
                        : j.status === "rejected"
                          ? "red"
                          : "neutral"
                    }
                  >
                    {statusLabel[j.status] || j.status}
                  </Badge>
                  <TierBadge job={j} />
                  <FitBadge job={j} />
                  {j.posting_state === "expired" && (
                    <Badge tone="red">posting closed</Badge>
                  )}
                  {j.posting_state === "needs_review" && (
                    <Badge tone="amber">check posting</Badge>
                  )}
                  {!compact && (
                    <small>
                      {j.record_source === "gmail"
                        ? "Tracked from Gmail · add the posting to create documents"
                        : j.application_date
                          ? "Applied " + j.application_date
                          : "Date not recorded"}
                    </small>
                  )}
                </span>
                <ArrowUpRight size={18} />
              </button>
              {(onCoverLetter ||
                (onRemove && ["saved", "prepared"].includes(j.status))) && (
                <span className="job-row-actions">
                  {onCoverLetter && (
                    <button
                      type="button"
                      className="secondary"
                      disabled={j.record_source === "gmail"}
                      title={
                        j.record_source === "gmail"
                          ? "Add the full job description first"
                          : `Generate a cover letter for ${j.company}`
                      }
                      onClick={() => onCoverLetter(j)}
                    >
                      <FileText size={15} /> Cover letter
                    </button>
                  )}
                  {onRemove && ["saved", "prepared"].includes(j.status) && (
                    <button
                      type="button"
                      className="icon-button danger-icon"
                      aria-label={`Remove ${j.company} ${j.title}`}
                      title="Remove unsuitable role"
                      onClick={() => onRemove(j)}
                    >
                      <Trash2 size={16} />
                    </button>
                  )}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
