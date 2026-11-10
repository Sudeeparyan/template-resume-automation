import { useState } from "react";
import type { Summary } from "../types";

export default function ApplicationFunnel({ funnel, onJob }: {
  funnel: NonNullable<Summary["funnel"]>;
  onJob: (jobId: string) => void;
}) {
  const [period, setPeriod] = useState<"30" | "90" | "all">("30");
  const counts = funnel.periods[period];
  const maximum = Math.max(counts.saved, 1);
  const rows = [
    ["saved", "Saved"], ["applied", "Applications"], ["interviews", "Interviews"], ["offers", "Offers"],
  ] as const;
  return (
    <section className="card application-funnel">
      <div className="section-title">
        <div><h2>Application funnel</h2><p className="muted">Recorded activity, not a prediction of interview chances.</p></div>
        <select aria-label="Funnel period" value={period} onChange={(event) => setPeriod(event.target.value as typeof period)}>
          <option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="all">All time</option>
        </select>
      </div>
      <div className="funnel-bars">
        {rows.map(([key, label]) => <div className="funnel-row" key={key}>
          <span>{label}</span><div className="funnel-track"><div style={{ width: `${Math.max(counts[key] ? 4 : 0, counts[key] / maximum * 100)}%` }} /></div><strong>{counts[key]}</strong>
        </div>)}
      </div>
      {!!funnel.next_actions.length && <div className="funnel-next">
        <h3>Worth a look next</h3>
        <ul>{funnel.next_actions.map((item) => <li key={item.job_id}>
          <button type="button" className="text-button" onClick={() => onJob(item.job_id)}><b>{item.company} · {item.title}</b> — {item.text}</button>
        </li>)}</ul>
      </div>}
    </section>
  );
}
