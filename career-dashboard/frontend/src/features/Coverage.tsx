import { useCallback, useEffect, useState } from "react";
import { api, safeUrl } from "../api";
import { Badge } from "../components/UI";
import type { SearchCoverage } from "../types";

const words = (text: string) => text.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
export function coverageSourceLabel(key: string) {
  if (key === "feeds:directory") return "Public feed directory";
  if (key.startsWith("employer:")) {
    const url = safeUrl(key.slice("employer:".length));
    return url === "#" ? "Employer careers source" : `Employer · ${new URL(url).hostname}`;
  }
  const [kind, market, pass, region, place] = key.split(":");
  if (kind === "ai") return ["AI search", market === "ie" ? "Ireland" : market === "us" ? "United States" : words(market || ""), region === "county" ? `County ${words(place || "")}` : words(region || ""), pass ? `pass ${pass}` : ""].filter(Boolean).join(" · ");
  if (kind === "jobs_ie") return `Jobs.ie · ${key.slice("jobs_ie:".length)}`;
  if (kind === "workday") return `Workday · ${key.slice("workday:".length).replaceAll(":", " · ")}`;
  return key.split(":").map(words).join(" · ");
}

const stateLabel = {
  complete: "Checked to source boundary",
  partial: "More to check",
  failed: "Check failed",
  blocked: "Access blocked",
  attempted: "Search attempted",
};

export function CoverageReport({ data }: { data: SearchCoverage }) {
  return <>
    <p className="small">{data.scope || "Configured sources only; coverage is not exhaustive."}</p>
    <p className="small">{data.complete} sources checked to their boundary · {data.partial} partial · {data.failed} failed. A completed source check does not mean every job in Ireland was found.</p>
    {!data.sources.length ? <p>No source checks have been recorded yet.</p> : <ul className="assistant-output-list">
      {data.sources.map((source) => <li key={source.key} style={{ display: "block" }}>
        <strong>{coverageSourceLabel(source.key)}</strong>{" "}
        <Badge tone={source.state === "complete" ? "neutral" : "amber"}>{stateLabel[source.state] || "Not checked"}</Badge>
        <p className="small">{source.found} leads found{source.checked_at ? ` · Last checked: ${source.checked_at}` : " · Not checked yet"}</p>
        {!!Object.keys(source.cursor || {}).length && <p className="small muted">Saved progress: {Object.entries(source.cursor).filter(([, value]) => typeof value === "number" || typeof value === "string").map(([key, value]) => `${words(key)}: ${value}`).join(" · ")}</p>}
        {source.error && <p className="small">{source.error}</p>}
      </li>)}
    </ul>}
  </>;
}

export default function Coverage({ refreshKey }: { refreshKey: string }) {
  const [data, setData] = useState<SearchCoverage | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const result = await api<SearchCoverage>("/v2/search/coverage");
      setData(result);
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);
  return <details className="card spaced">
    <summary><strong>Search coverage</strong>{data ? ` · ${data.sources.length} recorded sources` : ""}</summary>
    {error && <p role="alert">Coverage could not be refreshed. {error} <button type="button" className="text-button" onClick={() => void load()}>Retry</button></p>}
    {data ? <CoverageReport data={data} /> : !error && <p role="status">Loading recorded source checks…</p>}
  </details>;
}
