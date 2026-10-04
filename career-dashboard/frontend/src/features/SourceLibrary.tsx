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
import type { EducationPermitFacts, JobSearchPreferences, ProfileEntry } from "../profiles";

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
type Authorization = { status: "authorized" | "needs_sponsorship" | "unknown"; citizenship: "citizen" | "noncitizen" | "unknown"; needs_sponsorship_later: "yes" | "no" | "unknown";
  permission_type?: string; permission_wording?: string; valid_until_raw?: string; valid_until?: string; valid_until_confirmed?: boolean };
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

const MARKET_NAMES: Record<MarketCode, string> = { ie: "Ireland", us: "United States" };

/** The markets this copy offers (backend countries/markets.yml); both when an older server does not say. */
export function offeredMarkets(profile: ProfileEntry): MarketCode[] {
  const offered = (profile.offered_markets || []).filter((value): value is MarketCode => value === "ie" || value === "us");
  return offered.length ? offered : ["ie", "us"];
}

export function marketSelection(values: string[], offered: MarketCode[] = ["ie", "us"]): MarketCode[] {
  const unique = new Set(values.filter((value): value is MarketCode => offered.includes(value as MarketCode)));
  return unique.size ? Array.from(unique) : [offered.includes("ie") ? "ie" : offered[0]];
}

/** Only persisted build inputs affect synchronization; ordinary profile polling preserves edits. */
export function profileBuildSettings(profile: ProfileEntry) {
  const saved = marketSelection(profile.target_markets || [profile.country]);
  const enabled = saved.filter((market) => offeredMarkets(profile).includes(market));
  return {
    // An entirely dormant profile retains its own country; a rebuild must not move it.
    markets: enabled.length ? enabled : saved,
    authorization: {
      ie: { ...UNKNOWN_AUTH, ...profile.work_authorization_by_market?.ie },
      us: { ...UNKNOWN_AUTH, ...profile.work_authorization_by_market?.us },
    },
    education: profile.education_for_permits || {},
    preferences: profile.job_search || {},
  };
}

/** Defaults follow dated permit rules; only an edited amount is sent as the person's own floor. */
export function buildPreferences(settings: JobSearchPreferences): JobSearchPreferences {
  const result = { ...settings };
  if (result.salary_floor_source === "permit_rules") delete result.salary_floor_eur;
  delete result.salary_floor_source;
  return result;
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
  const [education, setEducation] = useState<EducationPermitFacts>(() => profileBuildSettings(profile).education);
  const [preferences, setPreferences] = useState<JobSearchPreferences>(() => profileBuildSettings(profile).preferences);
  const settingsSignature = useRef(JSON.stringify(profileBuildSettings(profile)));
  const savedSettings = JSON.stringify(profileBuildSettings(profile));
  useEffect(() => {
    const saved = JSON.parse(savedSettings) as ReturnType<typeof profileBuildSettings>;
    setMarkets(saved.markets);
    setAuthorization(saved.authorization);
    setEducation(saved.education);
    setPreferences(saved.preferences);
    settingsSignature.current = savedSettings;
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
        shellApi<Pick<ProfileEntry, "work_authorization_by_market" | "education_for_permits" | "job_search">>(base + "/build-settings"),
      ]), ([sourceData, runData, persisted]) => {
        setSources(sourceData.sources || []);
        setRuns(runData.runs || []);
        setLoadError("");
        setLoaded(true);
        const settings = profileBuildSettings({ ...profile, ...persisted });
        const signature = JSON.stringify(settings);
        if (signature !== settingsSignature.current) {
          settingsSignature.current = signature;
          setAuthorization(settings.authorization);
          setEducation(settings.education);
          setPreferences(settings.preferences);
        }
      });
    } catch (error) {
      setLoadError((error as Error).message);
      setLoaded(true);
    }
  }, [base, reads, profile.id, savedSettings]);

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

  const offered = offeredMarkets(profile);
  const switchedOff = (profile.target_markets || []).filter((code) => !offered.includes(code));
  const marketUnavailable = markets.some((code) => !offered.includes(code));

  function chooseMarket(value: MarketCode) {
    setMarkets((current) => current.includes(value)
      ? current.length === 1 ? current : current.filter((item) => item !== value)
      : marketSelection([...current, value], offered));
  }

  function setAuthorizationField(market: MarketCode, field: keyof Authorization, value: string | boolean) {
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
          {offered.length === 1
            ? <span className="source-market-only">Job market: {MARKET_NAMES[offered[0]]}</span>
            : offered.map((code) => <label key={code}><input type="checkbox" checked={markets.includes(code)} onChange={() => chooseMarket(code)} /> {MARKET_NAMES[code]}</label>)}
        </fieldset>
        {switchedOff.length > 0 && <small className="source-muted">
          {switchedOff.map((code) => MARKET_NAMES[code]).join(", ")} is no longer offered in this copy.
          {marketUnavailable ? " Create a separate profile for an offered market, or re-enable this market before rebuilding." : ` A rebuild keeps ${markets.map((code) => MARKET_NAMES[code]).join(" and ")}.`}
        </small>}
        <small className="source-muted">{markets.length === 2 ? "Search both markets; each uses its own work rules." : markets[0] === "us" ? "US roles and US resume format." : "Ireland roles and Irish resume format."}</small>
        <div className="source-authorization">
          {markets.map((market) => <fieldset key={market} disabled={!!running || !!busy}>
            <legend>{market === "ie" ? "Ireland" : "United States"} work eligibility</legend>
            {market === "ie" && <>
              <label>Irish permission type<select value={authorization.ie.permission_type || "unknown"} onChange={(event) => setAuthorizationField("ie", "permission_type", event.target.value)}>
                <option value="unknown">Not confirmed</option><option value="stamp_1g">Stamp 1G</option><option value="stamp_2">Stamp 2</option><option value="stamp_4">Stamp 4</option><option value="irish_or_eea_citizen">Irish, EU/EEA, UK or Swiss citizen</option><option value="csep_holder">Critical Skills permit holder</option><option value="gep_holder">General permit holder</option><option value="stamp_1">Stamp 1</option><option value="stamp_3">Stamp 3</option><option value="other">Other permission</option>
              </select></label>
              <label>Permission in your own words<input value={authorization.ie.permission_wording || ""} onChange={(event) => setAuthorizationField("ie", "permission_wording", event.target.value)} /></label>
              <label>Expiry as supplied<input placeholder="For example DEC2027" value={authorization.ie.valid_until_raw || ""} onChange={(event) => setAuthorization((current) => ({ ...current, ie: { ...current.ie, valid_until_raw: event.target.value, valid_until_confirmed: false } }))} /></label>
              <label>Exact permission expiry<input type="date" value={authorization.ie.valid_until || ""} onChange={(event) => setAuthorization((current) => ({ ...current, ie: { ...current.ie, valid_until: event.target.value, valid_until_confirmed: false } }))} /></label>
              <label><input type="checkbox" checked={authorization.ie.valid_until_confirmed === true} disabled={!authorization.ie.valid_until} onChange={(event) => setAuthorizationField("ie", "valid_until_confirmed", event.target.checked)} /> I confirm this exact expiry day from my permission record</label>
              <small className="source-muted">A month such as DEC2027 does not establish an exact day. Stamp 1G searches wait for your confirmed date.</small>
            </>}
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
        {markets.includes("ie") && <fieldset disabled={!!running || !!busy} className="source-authorization">
          <legend>Permit facts and search preferences</legend>
          <label>Award date as supplied<input value={education.award_date_raw || ""} onChange={(event) => setEducation({ ...education, award_date_raw: event.target.value, award_date_confirmed: false })} /></label>
          <label>Exact degree award date<input type="date" value={education.award_date || ""} onChange={(event) => setEducation({ ...education, award_date: event.target.value, award_date_confirmed: false })} /></label>
          <label><input type="checkbox" checked={education.award_date_confirmed === true} disabled={!education.award_date} onChange={(event) => setEducation({ ...education, award_date_confirmed: event.target.checked })} /> I confirm the award date; this is not an expected graduation date</label>
          <label>NFQ level<select value={education.nfq_level ?? ""} onChange={(event) => setEducation({ ...education, nfq_level: event.target.value ? Number(event.target.value) : null })}><option value="">Not confirmed</option>{Array.from({ length: 10 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}</select></label>
          {([['irish_institution', 'Award from an Irish institution?'], ['relevant_degree', 'Is the degree relevant to your target occupations?']] as const).map(([field, label]) => <label key={field}>{label}<select value={education[field] == null ? "unknown" : String(education[field])} onChange={(event) => setEducation({ ...education, [field]: event.target.value === "unknown" ? null : event.target.value === "true" })}><option value="unknown">Not confirmed</option><option value="true">Yes</option><option value="false">No</option></select></label>)}
          <label><input type="checkbox" checked={preferences.graduate_search_confirmed === true} onChange={(event) => setPreferences({ ...preferences, graduate_search_confirmed: event.target.checked, seniority: event.target.checked ? ['graduate', 'junior', 'entry'] : [], max_years_required: event.target.checked ? 3 : null })} /> Search graduate and entry-level roles (up to 3 years required)</label>
          <label>Seniority to search<input value={(preferences.seniority || []).join(', ')} onChange={(event) => setPreferences({ ...preferences, seniority: event.target.value.split(',').map((s) => s.trim()).filter(Boolean) })} /></label>
          <label>Maximum years required<input type="number" min={0} max={50} value={preferences.max_years_required ?? ''} onChange={(event) => setPreferences({ ...preferences, max_years_required: event.target.value ? Number(event.target.value) : null })} /></label>
          <label>Salary floor (€ yearly base)<input type="number" min={1} value={preferences.salary_floor_eur ?? ''} placeholder="Default follows dated permit rules" onChange={(event) => setPreferences({ ...preferences, salary_floor_eur: event.target.value ? Number(event.target.value) : undefined, salary_floor_source: event.target.value ? 'person' : 'permit_rules' })} /></label>
          <label>Salary policy<select value={preferences.salary_policy || 'confirmed_or_estimated'} onChange={(event) => setPreferences({ ...preferences, salary_policy: event.target.value as JobSearchPreferences['salary_policy'] })}><option value="confirmed_or_estimated">Confirmed, or a clearly labelled estimate when none is advertised</option><option value="confirmed_only">Confirmed advertised pay only</option></select></label>
        </fieldset>}
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
      {!running && <button type="button" className="source-build-button" disabled={!activeSources.length || !!busy || marketUnavailable} onClick={() => void runAction("/build-runs", { target_markets: markets, work_authorization_by_market: Object.fromEntries(markets.map((market) => [market, authorization[market]])), education_for_permits: education, job_search: buildPreferences(preferences) })}>
        <Sparkles size={16} /> {profile.state === "ready" ? "Rebuild profile & agents" : "Build Agent for You"}
      </button>}
    </section>
  );
}
