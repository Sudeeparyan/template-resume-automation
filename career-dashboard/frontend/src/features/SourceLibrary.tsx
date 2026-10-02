import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  FolderOpen,
  LoaderCircle,
  Paperclip,
  RotateCcw,
  Sparkles,
  Square,
  Upload,
} from "lucide-react";
import { shellApi, uploadFile } from "../api";
import { LatestRequest } from "../latestRequest";
import type { ProfileEntry } from "../profiles";

export type SourceVersion = {
  version: number;
  created_at: string;
  bytes: number;
  sha256: string;
  extracted_chars: number;
};
export type SourceRecord = {
  id: string;
  name: string;
  kind: string;
  active: boolean;
  current_version: number;
  preview?: string;
  versions: SourceVersion[];
};
export type BuildRun = {
  id: string;
  status: string;
  phase: string;
  progress: number;
  eta_seconds_low: number | null;
  eta_seconds_high: number | null;
  flags: unknown;
  errors: unknown;
  completed_profile_revision: string | number | null;
  started_at: string;
  updated_at: string;
  profile?: { state?: string };
};
type MarketCode = "ie" | "us";
type Authorization = { status: "authorized" | "needs_sponsorship" | "unknown"; citizenship: "citizen" | "noncitizen" | "unknown"; needs_sponsorship_later: "yes" | "no" | "unknown" };
const UNKNOWN_AUTH: Authorization = { status: "unknown", citizenship: "unknown", needs_sponsorship_later: "unknown" };

type Props = {
  profile: ProfileEntry;
  notify: (text: string, error?: boolean) => void;
  onBuilt?: () => void | Promise<void>;
  compact?: boolean;
};

const ACCEPT = ".docx,.pdf,.txt,.md";
const ACTIVE = new Set(["queued", "running", "building", "processing"]);

export function progressPercent(value: number): number {
  const n = Number.isFinite(value) ? value : 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function minutes(seconds: number): string {
  if (seconds < 60) return "under 1 min";
  const whole = Math.ceil(seconds / 60);
  return whole === 1 ? "1 min" : `${whole} min`;
}

export function etaLabel(low: number | null, high: number | null): string {
  if (low == null && high == null) return "Estimating time…";
  if (low == null) return `About ${minutes(Math.max(0, high!))} left`;
  if (high == null || high <= low) return `About ${minutes(Math.max(0, low))} left`;
  return `${minutes(Math.max(0, low))}–${minutes(Math.max(0, high))} left`;
}

export function marketSelection(values: string[]): ("ie" | "us")[] {
  const unique = new Set(values.filter((value): value is "ie" | "us" => value === "ie" || value === "us"));
  return unique.size ? Array.from(unique) : ["ie"];
}

/** Only persisted build inputs affect synchronization; ordinary profile polling preserves edits. */
export function profileBuildSettings(profile: ProfileEntry) {
  return {
    markets: marketSelection(profile.target_markets || [profile.country]),
    authorization: {
      ie: { ...UNKNOWN_AUTH, ...profile.work_authorization_by_market?.ie },
      us: { ...UNKNOWN_AUTH, ...profile.work_authorization_by_market?.us },
    },
  };
}

function lines(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => typeof item === "string" ? item : JSON.stringify(item));
  if (typeof value === "string" && value) return [value];
  return [];
}

function formatBytes(bytes: number): string {
  return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

function announceChange() {
  window.dispatchEvent(new Event("career-workspace:changed"));
  try {
    localStorage.setItem("career-workspace:changed", String(Date.now()));
  } catch {
    // The current tab still refreshes when browser storage is unavailable.
  }
}

/** The profile's persistent document and note library, usable before and after its first build. */
export default function SourceLibrary({ profile, notify, onBuilt, compact = false }: Props) {
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [runs, setRuns] = useState<BuildRun[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const [dragging, setDragging] = useState(false);
  const [noteName, setNoteName] = useState("");
  const [noteText, setNoteText] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [markets, setMarkets] = useState<MarketCode[]>(() => profileBuildSettings(profile).markets);
  const [authorization, setAuthorization] = useState<Record<MarketCode, Authorization>>(() => profileBuildSettings(profile).authorization);
  const savedSettings = JSON.stringify(profileBuildSettings(profile));
  useEffect(() => {
    const saved = JSON.parse(savedSettings) as ReturnType<typeof profileBuildSettings>;
    setMarkets(saved.markets);
    setAuthorization(saved.authorization);
  }, [profile.id, savedSettings]);
  const [now, setNow] = useState(() => Date.now());
  const uploadRef = useRef<HTMLInputElement>(null);
  const completedRef = useRef<string | null>(null);
  const reads = useRef(new LatestRequest()).current;
  const base = `/profiles/${profile.id}`;

  const load = useCallback(async () => {
    try {
      await reads.run(() => Promise.all([
        shellApi<{ sources: SourceRecord[] }>(base + "/sources"),
        shellApi<{ runs: BuildRun[] }>(base + "/build-runs"),
      ]), ([sourceData, runData]) => {
        setSources(sourceData.sources || []);
        setRuns(runData.runs || []);
        setLoadError("");
        setLoaded(true);
      });
    } catch (error) {
      setLoadError((error as Error).message);
      setLoaded(true);
    }
  }, [base, reads]);

  useEffect(() => { void load(); return () => reads.invalidate(); }, [load, reads]);
  const latest = runs[0];
  const running = latest && ACTIVE.has(latest.status);
  useEffect(() => {
    const delay = running ? 1800 : 12000;
    const timer = window.setInterval(() => { void load(); setNow(Date.now()); }, delay);
    const wake = () => { if (document.visibilityState === "visible") void load(); };
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [load, running]);

  useEffect(() => {
    if (!loaded) return;
    if (!latest) { if (completedRef.current === null) completedRef.current = "none"; return; }
    const key = `${latest.id}:${latest.status}`;
    if (completedRef.current === null) { completedRef.current = key; return; }
    if (latest.status !== "completed" || completedRef.current === key) { completedRef.current = key; return; }
    completedRef.current = key;
    announceChange();
    notify("Profile and agents are ready. Dashboard and profile data have refreshed.");
    void onBuilt?.();
    if (profile.state === "onboarding") window.setTimeout(() => location.reload(), 1000);
  }, [loaded, latest?.id, latest?.status, notify, onBuilt, profile.state]);

  async function upload(files: FileList | File[]) {
    setBusy("upload");
    try {
      for (const file of Array.from(files)) {
        if (file.size > 20_000_000) throw new Error(`${file.name} is larger than 20 MB.`);
        await uploadFile("/api" + base + "/sources", file, file.name);
      }
      await load();
      notify("Source document saved. Its latest version is now in the library.");
      announceChange();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  async function saveNote() {
    if (!noteName.trim() || !noteText.trim()) return;
    setBusy("note");
    try {
      await shellApi(base + "/sources/notes", "POST", { name: noteName.trim(), text: noteText.trim() });
      setNoteText("");
      setNoteName("");
      setNoteOpen(false);
      await load();
      notify("Note saved as a profile source.");
      announceChange();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  async function toggle(source: SourceRecord) {
    setBusy(source.id);
    try {
      await shellApi(base + "/sources/" + encodeURIComponent(source.id), "PUT", { active: !source.active });
      await load();
      announceChange();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  async function runAction(path: string, body?: unknown) {
    setBusy("build");
    try {
      await shellApi<BuildRun>(base + path, "POST", body);
      await load();
      announceChange();
    } catch (error) {
      notify((error as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  function chooseMarket(value: "ie" | "us") {
    setMarkets((current) => current.includes(value)
      ? current.length === 1 ? current : current.filter((item) => item !== value)
      : marketSelection([...current, value]));
  }

  function setAuthorizationField(market: MarketCode, field: keyof Authorization, value: string) {
    setAuthorization((current) => ({ ...current, [market]: { ...current[market], [field]: value } }));
  }

  const activeSources = sources.filter((source) => source.active);
  const flags = lines(latest?.flags);
  const errors = lines(latest?.errors);
  const progress = progressPercent(latest?.progress ?? 0);
  const elapsed = latest?.started_at ? Math.max(0, Math.round((now - new Date(latest.started_at).getTime()) / 60000)) : 0;

  return (
    <section className={"source-library" + (compact ? " compact" : "")} aria-label="Profile source library">
      <div className="source-head">
        <span className="source-head-icon"><FolderOpen size={18} /></span>
        <div>
          <h2>Profile sources</h2>
          <small>{activeSources.length} active · {sources.length} saved</small>
        </div>
      </div>
      {!compact && <p className="source-intro">Keep resumes, project notes, and personal facts here. Each build uses the active versions as this profile’s source of truth.</p>}
      <div
        className={"source-drop" + (dragging ? " dragging" : "")}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) void upload(event.dataTransfer.files);
        }}
      >
        <Upload size={compact ? 17 : 22} />
        <span>{compact ? "Drop a resume or file" : "Drop Word, PDF, text, or Markdown files"}</span>
        <button type="button" className="source-quiet-button" onClick={() => uploadRef.current?.click()} disabled={busy === "upload"}>
          <Paperclip size={14} /> {busy === "upload" ? "Uploading…" : "Choose files"}
        </button>
        <small className="source-drop-limit">DOCX, PDF, TXT or MD · up to 20 MB each</small>
        <input ref={uploadRef} hidden type="file" multiple accept={ACCEPT} onChange={(event) => {
          if (event.target.files?.length) void upload(event.target.files);
          event.target.value = "";
        }} />
      </div>
      <div className="source-add-row">
        <button type="button" className="source-text-button" onClick={() => setNoteOpen((open) => !open)} aria-expanded={noteOpen}>
          <FileText size={14} /> {noteOpen ? "Close note" : "Add personal information"}
        </button>
      </div>
      {noteOpen && <div className="source-note-form">
        <label>Title<input value={noteName} maxLength={120} placeholder="Projects, work history, goals…" onChange={(event) => setNoteName(event.target.value)} /></label>
        <label>Information<textarea value={noteText} rows={compact ? 4 : 6} placeholder="Write the facts you want this profile to remember…" onChange={(event) => setNoteText(event.target.value)} /></label>
        <button type="button" className="source-action" disabled={!noteName.trim() || !noteText.trim() || busy === "note"} onClick={() => void saveNote()}>
          {busy === "note" ? <LoaderCircle className="spin" size={14} /> : <CheckCircle2 size={14} />} Save note
        </button>
      </div>}
      {loadError && <p className="source-error" role="alert"><CircleAlert size={14} /> {loadError}</p>}
      {!loaded && <p className="source-muted"><LoaderCircle className="spin" size={14} /> Loading sources…</p>}
      {loaded && sources.length === 0 && !loadError && <p className="source-empty">Add a document or note to start this profile.</p>}
      {sources.length > 0 && <div className="source-list">
        {sources.map((source) => {
          const currentVersion = source.versions.find((version) => version.version === source.current_version) || source.versions[0];
          return <article key={source.id} className={"source-item" + (source.active ? "" : " inactive")}>
            <div className="source-item-main">
              <FileText size={16} />
              <div><b title={source.name}>{source.name}</b><small>{source.kind === "note" ? "Note" : "Document"} · v{source.current_version}{currentVersion ? ` · ${formatBytes(currentVersion.bytes)}` : ""}</small></div>
              <label className="source-switch" title={source.active ? "Use this source in the next build" : "Exclude this source from the next build"}>
                <input type="checkbox" checked={source.active} disabled={!!busy} onChange={() => void toggle(source)} aria-label={`${source.active ? "Deactivate" : "Activate"} ${source.name}`} />
                <span aria-hidden="true" />
              </label>
            </div>
            {source.preview && <p className="source-preview">{source.preview}</p>}
            {source.versions.length > 0 && <details className="source-versions">
              <summary>{source.versions.length} version{source.versions.length === 1 ? "" : "s"}</summary>
              <ol>{source.versions.map((version) => <li key={version.version}>
                <span>v{version.version}{version.version === source.current_version ? " · current" : ""}</span>
                <small>{new Date(version.created_at).toLocaleDateString()} · {formatBytes(version.bytes)} · {version.extracted_chars.toLocaleString()} characters</small>
              </li>)}</ol>
            </details>}
          </article>;
        })}
      </div>}

      <div className="source-build">
        <div className="source-section-label">BUILD SETTINGS</div>
        <fieldset className="source-markets" disabled={!!running || !!busy}>
          <legend>Job markets</legend>
          <label><input type="checkbox" checked={markets.includes("ie")} onChange={() => chooseMarket("ie")} /> Ireland</label>
          <label><input type="checkbox" checked={markets.includes("us")} onChange={() => chooseMarket("us")} /> United States</label>
        </fieldset>
        <small className="source-muted">{markets.length === 2 ? "Search both markets; each uses its own work rules." : markets[0] === "us" ? "US roles and US resume format." : "Ireland roles and Irish resume format."}</small>
        <div className="source-authorization">
          {markets.map((market) => <fieldset key={market} disabled={!!running || !!busy}>
            <legend>{market === "ie" ? "Ireland" : "United States"} work eligibility</legend>
            <label>Permission to work
              <select value={authorization[market].status} onChange={(event) => setAuthorizationField(market, "status", event.target.value)}>
                <option value="unknown">Not confirmed</option>
                <option value="authorized">Authorized</option>
                <option value="needs_sponsorship">Needs employer sponsorship</option>
              </select>
            </label>
            <label>Citizenship
              <select value={authorization[market].citizenship} onChange={(event) => setAuthorizationField(market, "citizenship", event.target.value)}>
                <option value="unknown">Not confirmed</option>
                <option value="citizen">Citizen</option>
                <option value="noncitizen">Noncitizen</option>
              </select>
            </label>
            <label>Will employer sponsorship be needed later?
              <select value={authorization[market].needs_sponsorship_later} onChange={(event) => setAuthorizationField(market, "needs_sponsorship_later", event.target.value)}>
                <option value="unknown">Not confirmed</option>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </label>
          </fieldset>)}
        </div>
        {latest && <div className="source-run" aria-live="polite">
          <div className="source-run-top">
            {running ? <LoaderCircle className="spin" size={16} /> : latest.status === "completed" ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}
            <b>{latest.phase || latest.status}</b>
            <span>{progress}%</span>
          </div>
          <div className="source-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-label="Profile build progress"><span style={{ width: `${progress}%` }} /></div>
          {running && <small className="source-eta"><Clock3 size={13} /> {etaLabel(latest.eta_seconds_low, latest.eta_seconds_high)} · {elapsed} min elapsed</small>}
          {flags.map((flag, index) => <p className="source-flag" key={index}><CircleAlert size={13} /> {flag}</p>)}
          {errors.map((message, index) => <p className="source-error" key={index}><CircleAlert size={13} /> {message}</p>)}
          <div className="source-run-actions">
            {running && <button type="button" className="source-text-button" disabled={!!busy} onClick={() => void runAction(`/build-runs/${encodeURIComponent(latest.id)}/stop`)}><Square size={12} /> Stop</button>}
            {!running && latest.status !== "completed" && <button type="button" className="source-text-button" disabled={!!busy} onClick={() => void runAction(`/build-runs/${encodeURIComponent(latest.id)}/retry`)}><RotateCcw size={13} /> Retry</button>}
            {latest.completed_profile_revision != null && <small>Profile revision {latest.completed_profile_revision}</small>}
          </div>
        </div>}
      </div>
      {!running && <button type="button" className="source-build-button" disabled={!activeSources.length || !!busy} onClick={() => void runAction("/build-runs", { target_markets: markets, work_authorization_by_market: Object.fromEntries(markets.map((market) => [market, authorization[market]])) })}>
        <Sparkles size={16} /> {profile.state === "ready" ? "Rebuild profile & agents" : "Build Agent for You"}
      </button>}
    </section>
  );
}
