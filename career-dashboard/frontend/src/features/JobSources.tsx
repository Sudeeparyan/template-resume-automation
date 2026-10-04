import { useCallback, useEffect, useState } from "react";
import { api, safeUrl } from "../api";
import { Badge } from "../components/UI";
import type { JobSourceStatus } from "../types";

const KEY_LABELS: Record<string, string> = {
  CAREERJET_API_KEY: "Careerjet API key",
  CAREERJET_USER_IP: "Your public IP address, as registered with Careerjet",
  JOOBLE_API_KEY: "Jooble API key (Irish site)",
};

/** Where jobs are read from, and the optional aggregator keys (market/policy.py). */
export default function JobSources({ notify }: { notify: (text: string, error?: boolean) => void }) {
  const [sources, setSources] = useState<JobSourceStatus[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setSources((await api<{ sources: JobSourceStatus[] }>("/v2/sources")).sources);
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const save = async (name: string) => {
    setBusy(name);
    try {
      const result = await api<{ detail: string; sources: JobSourceStatus[] }>(`/v2/sources/keys/${name}`, "PUT", { value: values[name] || "" });
      setValues((before) => ({ ...before, [name]: "" }));
      setSources(result.sources);
      notify(result.detail);
    } catch (cause) {
      notify((cause as Error).message, true);
    } finally {
      setBusy("");
    }
  };
  const remove = async (name: string) => {
    if (!window.confirm(`Remove ${KEY_LABELS[name] || name} from this computer?`)) return;
    setBusy(name);
    try {
      const result = await api<{ detail: string; sources: JobSourceStatus[] }>(`/v2/sources/keys/${name}`, "DELETE");
      setSources(result.sources);
      notify(result.detail);
    } catch (cause) {
      notify((cause as Error).message, true);
    } finally {
      setBusy("");
    }
  };
  const keyed = sources.filter((source) => source.keys.length);
  const free = sources.filter((source) => !source.keys.length);
  return <section className="card spaced">
    <div className="section-title"><h2>Job sources</h2></div>
    <p className="small">
      Searches read employers' own careers boards (your tracked companies, the employer directory and the boards of
      employers in DETE's permit statistics), EURES / JobsIreland and Irish job boards, with no key. Two aggregators are
      optional and need your own free key; their results are leads that the app looks up on the employer's own board,
      and their links are left for you to open.
    </p>
    {error && <p role="alert">{error}</p>}
    <ul className="assistant-output-list">
      {free.map((source) => <li key={source.id} style={{ display: "block" }}>
        <strong>{source.label}</strong> <Badge tone="green">On</Badge>
        {source.personal_use_only && <Badge tone="neutral">Personal use only</Badge>}
        <p className="small muted">{source.terms}</p>
      </li>)}
    </ul>
    {keyed.map((source) => {
      const page = safeUrl(source.key_page || "");
      return <div key={source.id} className="key-row" style={{ display: "block", margin: "14px 0" }}>
        <p>
          <strong>{source.label}</strong>{" "}
          <Badge tone={source.ready ? "green" : "neutral"}>{source.ready ? "Ready" : "Optional · not set up"}</Badge>
          {source.used && source.budget && <span className="small muted"> · {source.used.today}/{source.budget.per_day} searches today{source.budget.lifetime ? ` · ${source.used.lifetime}/${source.budget.lifetime} for the key's life` : ""}</span>}
        </p>
        <p className="small muted">{source.terms}{page !== "#" && <> <a href={page} target="_blank" rel="noreferrer">Get a key ↗</a></>}</p>
        {source.keys.map((key) => <div key={key.name} className="key-form" style={{ display: "flex", gap: 8, alignItems: "center", margin: "6px 0" }}>
          <label className="small" style={{ minWidth: 220 }}>{KEY_LABELS[key.name] || key.name}{key.saved ? ` · saved${key.saved_in ? ` (${key.saved_in})` : ""}` : ""}</label>
          <input type={key.name.endsWith("_IP") ? "text" : "password"} autoComplete="off" value={values[key.name] || ""}
            placeholder={key.saved ? "Saved; paste a new value to replace it" : ""}
            onChange={(event) => setValues((before) => ({ ...before, [key.name]: event.target.value }))} aria-label={KEY_LABELS[key.name] || key.name} />
          <button type="button" className="secondary" disabled={busy === key.name || !(values[key.name] || "").trim()} onClick={() => void save(key.name)}>Save</button>
          {key.saved_in === "saved in the app" && <button type="button" className="text-button" disabled={busy === key.name} onClick={() => void remove(key.name)}>Remove</button>}
        </div>)}
      </div>;
    })}
  </section>;
}
