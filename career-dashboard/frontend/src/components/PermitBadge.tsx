import { safeUrl } from "../api";
import type { Job, TrackerRow } from "../types";
import { Badge } from "./UI";

/** Official pages behind the permit facts the app shows (countries/ie/permit-rules.yml). */
export const PERMIT_SOURCES = {
  statistics: "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/employment-permits/statistics/",
  gep: "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/employment-permits/permit-types/general-employment-permit/",
  csep: "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/employment-permits/permit-types/critical-skills-employment-permit/",
  lmnt: "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/employment-permits/employment-permit-eligibility/labour-market-needs-test/",
};

const STATEMENT = {
  supports: { label: "Posting mentions permit support", tone: "green" },
  silent: { label: "Posting is silent on permits", tone: "neutral" },
  ambiguous: { label: "Permit wording unclear", tone: "amber" },
  refuses: { label: "Posting says no sponsorship", tone: "red" },
} as Record<string, { label: string; tone: string }>;

export function PermitBadge({ job }: { job: Job }) {
  if (!job.opportunity) return null;
  const { permit, permit_path: path } = job.opportunity;
  const title = path ? `${permit.summary} Permit-path evidence ${path.score}/100: ${path.label}.` : permit.summary;
  return <Badge tone={permit.state === "obstacle" ? "red" : "amber"} title={title}>
    {permit.state === "obstacle" ? "Permit obstacle" : path ? `Permit path ${path.score}/100` : permit.state === "criteria_checked" ? "Permit criteria checked" : "Permit checks pending"}
  </Badge>;
}

/** The posting's own permit statement, as a badge whose title quotes it. */
export function StatementBadge({ statement, quote }: { statement: string; quote: string }) {
  const known = STATEMENT[statement] || STATEMENT.silent;
  return <Badge tone={known.tone} title={quote ? `“${quote}”` : "The posting says nothing about permits or sponsorship."}>{known.label}</Badge>;
}

export function DeteBadge({ permits }: { permits: TrackerRow["permits"] }) {
  const count = permits.permits_24m || 0;
  return count > 0
    ? <Badge tone="green" title={`DETE employment permit statistics: ${count} permits issued to ${permits.legal_names.join("; ")} in the last 24 complete months.`}>DETE: {count.toLocaleString("en-IE")} permits</Badge>
    : <Badge tone="neutral" title="No permits found in DETE's statistics under this employer's name. A different legal name is possible.">No DETE record found</Badge>;
}

function Source({ href, children }: { href: string; children: string }) {
  const url = safeUrl(href);
  return url === "#" ? <span>{children}</span> : <a href={url} target="_blank" rel="noreferrer">{children} ↗</a>;
}

const euro = (value: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);

/** The permit evidence for one posting: its quoted sentence, DETE's numbers, EURES and the pay threshold. */
export function PermitFacts({ row, floor }: { row: TrackerRow; floor?: number }) {
  const years = Object.entries(row.permits.by_year || {}).sort(([a], [b]) => a.localeCompare(b));
  const pay = row.salary;
  const top = pay.max ?? pay.min;
  return <div className="permit-facts">
    <p><strong>What the posting says:</strong> {row.statement_quote ? <q>{row.statement_quote}</q> : "Nothing about permits or sponsorship."}</p>
    <p>
      <strong>DETE permit statistics:</strong>{" "}
      {row.permits.permits_24m
        ? <>{row.permits.permits_24m.toLocaleString("en-IE")} permits in the last 24 complete months to {row.permits.legal_names.join("; ")}.</>
        : "No permits found under this employer's name (its legal name may differ)."}
      {!!years.length && <span className="small muted"> By year: {years.map(([year, count]) => `${year}: ${count.toLocaleString("en-IE")}`).join(" · ")}</span>}
    </p>
    {row.on_eures && <p>Advertised on EURES / JobsIreland: the channel an employer uses for the Labour Market Needs Test (this does not show the test was done).</p>}
    <p>
      <strong>Pay:</strong>{" "}
      {pay.kind === "advertised" && top
        ? <>{pay.min && pay.max && pay.min !== pay.max ? `${euro(pay.min)}–${euro(pay.max)}` : euro(top)} a year, advertised{floor ? (top >= floor ? `; reaches your threshold of ${euro(floor)}` : `; below your threshold of ${euro(floor)}`) : ""}.</>
        : <>Not advertised. Confirm base pay with the recruiter{floor ? ` (your threshold: ${euro(floor)} a year)` : ""}.</>}
    </p>
    <p className="small">
      <Source href={PERMIT_SOURCES.statistics}>DETE permit statistics</Source> · <Source href={PERMIT_SOURCES.gep}>General Employment Permit</Source> · <Source href={PERMIT_SOURCES.csep}>Critical Skills Employment Permit</Source> · <Source href={PERMIT_SOURCES.lmnt}>Labour Market Needs Test</Source>
    </p>
    <p className="small muted">Published facts with their sources, not immigration advice.</p>
  </div>;
}
