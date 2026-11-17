import { safeUrl } from "../api";
import type { Job, Opportunity } from "../types";
import { PermitBadge } from "./PermitBadge";
import { Badge } from "./UI";

function money(value: number, currency: string) {
  if (!/^[A-Z]{3}$/.test(currency)) return `${new Intl.NumberFormat("en-IE", { maximumFractionDigits: 0 }).format(value)} (${currency || "currency unconfirmed"})`;
  return new Intl.NumberFormat("en-IE", { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
}

export function salaryAmount(salary: Opportunity["salary"]) {
  const { currency } = salary;
  const minimum = salary.kind === "researched" ? salary.annual_min : salary.minimum;
  const maximum = salary.kind === "researched" ? salary.annual_max : salary.maximum;
  const period = salary.kind === "researched" ? "year" : salary.period?.toLowerCase();
  const range = minimum != null && maximum != null
    ? minimum === maximum ? money(minimum, currency) : `${money(minimum, currency)}–${money(maximum, currency)}`
    : minimum != null ? `from ${money(minimum, currency)}` : maximum != null ? `up to ${money(maximum, currency)}` : "Amount not confirmed";
  return minimum != null || maximum != null ? `${range}${period && period !== "unknown" ? ` / ${period}` : " · pay period unconfirmed"}` : range;
}

export function SalaryBadge({ job }: { job: Job }) {
  const opportunity = job.opportunity;
  if (!opportunity) return job.market === "ie" ? <Badge tone="amber">Salary not checked</Badge> : null;
  const { salary, salary_state } = opportunity;
  const label = salary.kind === "advertised" ? "Advertised salary" : salary.kind === "researched" ? "Researched estimate" : "Salary unknown";
  return <Badge tone={salary_state === "below_floor" ? "red" : salary.kind === "advertised" && salary_state === "meets_floor" ? "green" : "amber"}
    title={`${salaryAmount(salary)}. ${salary_state === "below_floor" ? "Below your salary floor." : salary_state !== "meets_floor" || salary.kind !== "advertised" ? "Employer confirmation needed." : "Advertised salary meets your floor; permit checks are separate."}`}>
    {label}{salary_state === "below_floor" ? " · below floor" : salary_state === "needs_confirmation" ? " · confirm amount" : ""}
  </Badge>;
}

// The permit badge lives with the other permit evidence components; kept importable from here.
export { PermitBadge } from "./PermitBadge";

function EvidenceLink({ url, title }: { url: string; title: string }) {
  const href = safeUrl(url);
  return href === "#" ? <span>{title || "Source URL unavailable"}</span> : <a href={href} target="_blank" rel="noreferrer">{title || "Read source"} ↗</a>;
}

export function SalaryEvidence({ job }: { job: Job }) {
  const opportunity = job.opportunity;
  if (!opportunity) return job.market === "ie" ? <div className="callout warning"><p>Salary and permit evidence have not been checked for this saved job.</p></div> : null;
  const { salary, salary_state, permit, floor, sponsorship } = opportunity;
  return <section className="card spaced" aria-label="Salary and permit evidence">
    <div className="section-title"><h2>Salary &amp; permit evidence</h2><SalaryBadge job={job} /></div>
    <p><strong>{salaryAmount(salary)}</strong></p>
    <p className="small">Your salary floor: {money(floor, "EUR")} per year. {salary_state === "below_floor"
      ? "The recorded salary is below your floor. This role stays outside the salary shortlist."
      : salary.kind === "researched" ? "This is a researched estimate, not an advertised offer. Confirm the salary with the employer."
      : salary.kind === "unknown" || salary_state !== "meets_floor" ? "The salary needs confirmation before this role can enter the salary shortlist."
      : "The advertised salary meets your floor. Salary alone does not establish permit eligibility."}</p>
    {salary.quote && <blockquote>{salary.quote}</blockquote>}
    {opportunity.estimate && <div className="callout">
      <p><strong>Market estimate: {money(opportunity.estimate.p25, "EUR")}–{money(opportunity.estimate.p75, "EUR")} a year</strong> (median {money(opportunity.estimate.median, "EUR")})</p>
      <p className="small">{opportunity.estimate.label} Advertised in the last {opportunity.estimate.window_days} days for similar {opportunity.estimate.level}-level roles.
        Confirm with the recruiter that the base salary is at least {money(floor, "EUR")} before you apply.</p>
    </div>}
    {salary.url && <p className="small"><EvidenceLink url={salary.url} title="Salary evidence" /></p>}
    {salary.observed_at && <p className="small muted">Salary checked: {salary.observed_at}</p>}
    {salary.kind === "researched" && <p className="small muted">Sources reflect the dates shown. Published ranges may have changed since they were checked.</p>}
    {!!salary.sources.length && <ul>{salary.sources.map((source, index) => <li key={`${source.url}-${index}`}>
      <EvidenceLink url={source.url} title={source.title} />
      {source.quote && <blockquote>{source.quote}</blockquote>}
      {source.observed_at && <small>Checked: {source.observed_at}</small>}
    </li>)}</ul>}
    <div className="section-title"><h3>Permit checks</h3><PermitBadge job={job} /></div>
    <p>{permit.summary}</p>
    <p className="small">Not immigration advice. These checks record evidence and missing information; they are not a permit approval.</p>
    {opportunity.permit_path && <div className="permit-path">
      <p><strong>Permit-path evidence: {opportunity.permit_path.score}/100</strong> <span className="small muted">({opportunity.permit_path.label})</span></p>
      <ul>{opportunity.permit_path.parts.map((part) => <li key={part.id} className="small">{part.points}/{part.max} · {part.reason}</li>)}</ul>
    </div>}
    {!!permit.checks.length && <ul>{permit.checks.map((check) => <li key={check.id}>
      <strong>{check.label}</strong> — {check.state === "pass" ? "Evidence found" : check.state === "fail" ? "Obstacle" : "Needs confirmation"}
      {check.note && <p className="small">{check.note}</p>}
      {check.url && <p className="small"><EvidenceLink url={check.url} title="Official page" /></p>}
    </li>)}</ul>}
    {!!permit.timeline?.events.length && <><h3>Permit dates</h3><ul>{permit.timeline.events.map((event) => <li key={event.id}>
      <strong>{event.label}</strong>{event.date && `: ${event.date}`}
      {event.days_left != null && event.days_left >= 0 && ` (${event.days_left} days from today)`}
      {event.note && <p className="small">{event.note}</p>}
      {event.url && <EvidenceLink url={event.url} title="Official guidance" />}
    </li>)}</ul></>}
    {!!permit.sources.length && <p className="small">Reference sources: {permit.sources.map((source, index) => <span key={`${source.url}-${index}`}>
      {index > 0 && " · "}<EvidenceLink url={source.url} title={source.title} />
    </span>)}</p>}
    {sponsorship.quote && <><h3>Posting’s sponsorship wording</h3><blockquote>{sponsorship.quote}</blockquote></>}
  </section>;
}
