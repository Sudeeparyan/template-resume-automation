import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { API_BASE, api, safeUrl } from "../api";
import { DeteBadge, PermitFacts, StatementBadge } from "../components/PermitBadge";
import { AskAssistant, Badge, Field } from "../components/UI";
import type { TrackerAlert, TrackerResult, TrackerRow } from "../types";

const TYPE_LABELS: Record<string, string> = {
  graduate_programme: "Graduate programme",
  internship: "Internship",
  entry: "Entry level",
  experienced: "Experienced",
  unspecified: "Not stated",
};
const STATUS_LABELS: Record<string, string> = { saved: "Saved", applied: "Applied", interview: "Interview", offer: "Offer" };
export const DEFAULT_FILTERS: Record<string, string> = { hide_refusing: "1", leads: "1" };

/** Query string for the Tracker API: only the filters that are set. */
export function trackerQuery(filters: Record<string, string>, extra: Record<string, string> = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) if (value) params.set(key, value);
  return params.toString();
}

const euro = (value: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
const day = (stamp: string) => (stamp || "").slice(0, 10);

function PayBadge({ row, floor }: { row: TrackerRow; floor: number }) {
  const top = row.salary.max ?? row.salary.min;
  if (row.salary.kind !== "advertised" || !top) return <Badge tone="neutral" title="The posting states no pay. Confirm base pay with the recruiter.">Pay not advertised</Badge>;
  const range = row.salary.min && row.salary.max && row.salary.min !== row.salary.max ? `${euro(row.salary.min)}–${euro(row.salary.max)}` : euro(top);
  return <Badge tone={floor && top < floor ? "red" : "green"} title={row.salary.quote || undefined}>{range}</Badge>;
}

export function TrackerRowView({ row, floor, onSave, saving }: {
  row: TrackerRow;
  floor: number;
  onSave: (row: TrackerRow) => void;
  saving: boolean;
}) {
  const href = safeUrl(row.url);
  return <li className="tracker-row">
    <div className="tracker-head">
      <div>
        <strong>{href === "#" ? row.title : <a href={href} target="_blank" rel="noreferrer">{row.title} ↗</a>}</strong>
        <p className="small">{row.company} · {row.location || "Location not stated"} · {TYPE_LABELS[row.posting_type] || row.posting_type}</p>
      </div>
      <div className="tracker-actions">
        {row.mine ? <Badge tone="green">{STATUS_LABELS[row.mine.status] || "In your jobs"}</Badge>
          : row.lead ? <span className="small muted">Open the lead, then paste the employer's link in the Assistant</span>
            : <button type="button" className="secondary" disabled={saving} onClick={() => onSave(row)}>{saving ? "Saving…" : "Save to my jobs"}</button>}
      </div>
    </div>
    <div className="chips">
      {row.tags.includes("new") && <Badge tone="green">New</Badge>}
      {row.tags.includes("closing_soon") && <Badge tone="amber">Closes {row.closing_date}</Badge>}
      {row.lead && <Badge tone="amber" title={row.attribution}>Lead · {row.attribution || row.source_label}</Badge>}
      <StatementBadge statement={row.statement} quote={row.statement_quote} />
      <DeteBadge permits={row.permits} />
      <PayBadge row={row} floor={floor} />
      {row.on_eures && <Badge tone="neutral" title="Advertised on EURES / JobsIreland">EURES</Badge>}
    </div>
    <details>
      <summary className="small">Evidence · read from {row.source_label}{row.sources > 1 ? ` and ${row.sources - 1} more` : ""} · first seen {day(row.first_seen)}{row.closing_date && !row.tags.includes("closing_soon") ? ` · closes ${row.closing_date}` : ""}</summary>
      <PermitFacts row={row} floor={floor} />
    </details>
  </li>;
}

function Alerts({ alerts, onApply, onSeen, onDelete }: {
  alerts: TrackerAlert[];
  onApply: (alert: TrackerAlert) => void;
  onSeen: (alert: TrackerAlert) => void;
  onDelete: (alert: TrackerAlert) => void;
}) {
  if (!alerts.length) return null;
  return <div className="card spaced">
    <h2>Your alerts</h2>
    <ul className="assistant-output-list">
      {alerts.map((alert) => <li key={alert.id} style={{ display: "block" }}>
        <strong>{alert.name}</strong> {alert.new ? <Badge tone="green">{alert.new} new</Badge> : <span className="small muted">nothing new</span>}{" "}
        <span className="small muted">· {alert.matches} matching</span>
        {!!alert.examples.length && <p className="small">{alert.examples.map((example) => `${example.title} at ${example.company}`).join(" · ")}</p>}
        <p className="small">
          <button type="button" className="text-button" onClick={() => onApply(alert)}>Show</button>{" · "}
          {!!alert.new && <><button type="button" className="text-button" onClick={() => onSeen(alert)}>Mark as seen</button>{" · "}</>}
          <button type="button" className="text-button" onClick={() => onDelete(alert)}>Delete</button>
        </p>
      </li>)}
    </ul>
  </div>;
}

/** A Dashboard notice when saved Tracker alerts have roles first seen since the person last looked. */
export function TrackerAlertsNotice() {
  const [alerts, setAlerts] = useState<TrackerAlert[]>([]);
  useEffect(() => {
    let live = true;
    api<{ alerts: TrackerAlert[] }>("/v2/tracker/alerts")
      .then((result) => { if (live) setAlerts(result.alerts); })
      .catch(() => { if (live) setAlerts([]); });
    return () => { live = false; };
  }, []);
  const fresh = alerts.filter((alert) => alert.new > 0);
  if (!fresh.length) return null;
  const total = fresh.reduce((sum, alert) => sum + alert.new, 0);
  return <div className="callout" role="status">
    {total} new {total === 1 ? "role matches" : "roles match"} your Tracker alerts ({fresh.map((alert) => `${alert.name}: ${alert.new}`).join(" · ")}).{" "}
    <a href="#tracker">Open the Tracker →</a>
  </div>;
}

export default function Tracker({ notify }: { notify: (text: string, error?: boolean) => void }) {
  const [filters, setFilters] = useState<Record<string, string>>(DEFAULT_FILTERS);
  const [draft, setDraft] = useState<Record<string, string>>(DEFAULT_FILTERS);
  const [data, setData] = useState<TrackerResult | null>(null);
  const [rows, setRows] = useState<TrackerRow[]>([]);
  const [alerts, setAlerts] = useState<TrackerAlert[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState("");
  const [alertName, setAlertName] = useState("");

  const load = useCallback(async (chosen: Record<string, string>, offset = 0) => {
    try {
      const result = await api<TrackerResult>("/v2/tracker?" + trackerQuery(chosen, { offset: String(offset) }));
      setData(result);
      setRows((before) => (offset ? [...before, ...result.rows] : result.rows));
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);
  const loadAlerts = useCallback(async () => {
    try {
      setAlerts((await api<{ alerts: TrackerAlert[] }>("/v2/tracker/alerts")).alerts);
    } catch {
      setAlerts([]);
    }
  }, []);
  useEffect(() => { void load(filters); }, [load, filters]);
  useEffect(() => { void loadAlerts(); }, [loadAlerts]);

  const set = (key: string, value: string) => setDraft((before) => ({ ...before, [key]: value }));
  const apply = (event?: FormEvent) => {
    event?.preventDefault();
    setFilters({ ...draft });
  };
  const counties = useMemo(() => Object.keys(data?.facets.county || {}).sort(), [data]);
  const save = async (row: TrackerRow) => {
    setSaving(row.key);
    try {
      const result = await api<{ saved: boolean; summary: string }>("/v2/tracker/save", "POST", { key: row.key });
      notify(result.summary, !result.saved);
      await load(filters);
    } catch (cause) {
      notify((cause as Error).message, true);
    } finally {
      setSaving("");
    }
  };
  const saveAlert = async (event: FormEvent) => {
    event.preventDefault();
    if (!alertName.trim()) return;
    try {
      await api("/v2/tracker/alerts", "POST", { name: alertName.trim(), filters });
      setAlertName("");
      notify("Alert saved. New matching roles are counted on this page and in the morning list.");
      await loadAlerts();
    } catch (cause) {
      notify((cause as Error).message, true);
    }
  };
  const exportHref = (format: string) => `${API_BASE}/v2/tracker/export?${trackerQuery(filters, { format })}`;
  const floor = data?.floor_eur || 0;

  return <>
    <div className="page-title">
      <div>
        <div className="eyebrow">IRISH ROLES, WITH THE PERMIT FACTS</div>
        <h1>Tracker</h1>
        <p>
          Roles the app has read from employers' own careers boards, EURES / JobsIreland and Irish job boards, one row per role,
          newest first. Each shows what the posting says about permits, DETE's permit numbers for the employer and advertised pay.
        </p>
      </div>
      <div className="actions">
        <AskAssistant prompts={[
          { label: "Which of these fit me?", text: "Look at the Tracker's newest roles and tell me which fit my profile best, with the permit facts for each." },
          { label: "What does a DETE count mean?", text: "What does the DETE permit count on a Tracker row tell me, and what does it not tell me?" },
        ]} />
      </div>
    </div>
    <Alerts alerts={alerts} onApply={(alert) => { const chosen = { ...DEFAULT_FILTERS, ...alert.filters }; setDraft(chosen); setFilters(chosen); }}
      onSeen={async (alert) => { await api(`/v2/tracker/alerts/${alert.id}/seen`, "POST", {}); await loadAlerts(); }}
      onDelete={async (alert) => { await api(`/v2/tracker/alerts/${alert.id}`, "DELETE"); await loadAlerts(); }} />
    <form className="card spaced tracker-filters" onSubmit={apply}>
      <div className="tracker-filter-grid">
        <Field label="Type">
          <select value={draft.type || ""} onChange={(e) => set("type", e.target.value)}>
            <option value="">Any</option>
            {Object.entries(TYPE_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </Field>
        <Field label="County">
          <select value={draft.county || ""} onChange={(e) => set("county", e.target.value)}>
            <option value="">Anywhere in Ireland</option>
            {counties.map((county) => <option key={county} value={county}>{county}</option>)}
          </select>
        </Field>
        <Field label="What the posting says about permits">
          <select value={draft.statement || ""} onChange={(e) => set("statement", e.target.value)}>
            <option value="">Anything</option>
            <option value="supports">Mentions permit support</option>
            <option value="silent">Says nothing</option>
            <option value="ambiguous">Unclear wording</option>
            <option value="refuses">Says no sponsorship</option>
          </select>
        </Field>
        <Field label="DETE permit record">
          <select value={draft.dete || ""} onChange={(e) => set("dete", e.target.value)}>
            <option value="">Any employer</option>
            <option value="yes">Employer has DETE permits (24 months)</option>
            <option value="no">No DETE record found</option>
          </select>
        </Field>
        <Field label="Advertised pay at least (€ a year)">
          <input inputMode="numeric" value={draft.min_salary || ""} placeholder={floor ? String(Math.round(floor)) : ""}
            onChange={(e) => set("min_salary", e.target.value.replace(/[^0-9]/g, ""))} />
        </Field>
        <Field label="Posted within">
          <select value={draft.posted_within || ""} onChange={(e) => set("posted_within", e.target.value)}>
            <option value="">Any time</option>
            {["3", "7", "14", "30"].map((days) => <option key={days} value={days}>{days} days</option>)}
          </select>
        </Field>
        <Field label="Closing within">
          <select value={draft.closing_within || ""} onChange={(e) => set("closing_within", e.target.value)}>
            <option value="">Any</option>
            {["7", "14", "30"].map((days) => <option key={days} value={days}>{days} days</option>)}
          </select>
        </Field>
        <Field label="Employer">
          <input value={draft.employer || ""} onChange={(e) => set("employer", e.target.value)} />
        </Field>
        <Field label="Words in the title or text">
          <input value={draft.q || ""} onChange={(e) => set("q", e.target.value)} placeholder="data analyst, python…" />
        </Field>
      </div>
      <div className="tracker-switches">
        <label><input type="checkbox" checked={draft.hide_refusing === "1"} onChange={(e) => set("hide_refusing", e.target.checked ? "1" : "")} /> Hide roles whose posting says it cannot sponsor</label>
        <label><input type="checkbox" checked={draft.leads === "1"} onChange={(e) => set("leads", e.target.checked ? "1" : "")} /> Include aggregator leads</label>
        <label><input type="checkbox" checked={draft.show_applied === "1"} onChange={(e) => set("show_applied", e.target.checked ? "1" : "")} /> Show roles I applied to</label>
      </div>
      <div className="actions">
        <button type="submit" className="primary">Show roles</button>
        <button type="button" className="secondary" onClick={() => { setDraft(DEFAULT_FILTERS); setFilters(DEFAULT_FILTERS); }}>Clear filters</button>
        <a className="secondary" href={exportHref("csv")} download>Export CSV</a>
        <a className="secondary" href={exportHref("xlsx")} download>Export Excel</a>
      </div>
    </form>
    <form className="tracker-alert-form" onSubmit={saveAlert}>
      <input value={alertName} onChange={(e) => setAlertName(e.target.value)} placeholder="Name these filters, e.g. Graduate data roles in Dublin" aria-label="Alert name" />
      <button type="submit" className="secondary" disabled={!alertName.trim()}>Save as an alert</button>
    </form>
    {error && <p role="alert">The Tracker could not be loaded. {error} <button type="button" className="text-button" onClick={() => void load(filters)}>Retry</button></p>}
    {data && <p className="search-status" role="status">
      <b>{data.total.toLocaleString("en-IE")} of {data.all.toLocaleString("en-IE")} roles</b>
      <span>{data.facets.new} new in 72 hours · {data.facets.closing_soon} closing within a week · {data.facets.dete.yes} at employers with DETE permits</span>
      {data.updated && <span>Last read {day(data.updated)}</span>}
    </p>}
    {data && !data.all && <div className="callout"><p>No roles have been read yet. Run a Daily Search (or let the morning run read the feeds): every posting the app reads is listed here.</p></div>}
    <ul className="tracker-list">
      {rows.map((row) => <TrackerRowView key={row.key} row={row} floor={floor} onSave={save} saving={saving === row.key} />)}
    </ul>
    {data && rows.length < data.total && <p><button type="button" className="secondary" onClick={() => void load(filters, rows.length)}>Show more</button></p>}
    {data && <p className="small muted">{data.note} Leads come from aggregators with their attribution; open them on the aggregator's site.</p>}
  </>;
}
