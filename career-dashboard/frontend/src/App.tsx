import { Component, useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  LayoutDashboard,
  MessageSquareText,
  Search,
  FileText,
  ShieldCheck,
  UserRound,
  SlidersHorizontal,
  Workflow,
  CheckCircle2,
  LoaderCircle,
  Menu,
  Radar,
  X,
} from "lucide-react";
import { api, shellApi } from "./api";
import { LatestRequest } from "./latestRequest";
import { ProfileContext, ProfileMenu, firstName, openProfile, timeLabel, useProfileListing } from "./profiles";
import OnboardingWorkspace from "./features/OnboardingWorkspace";
import { AskContext, Field, Loading, Modal, NoticeContext } from "./components/UI";
import JobDetail from "./components/JobDetail";
import Dashboard from "./features/Dashboard";
import DailySearch from "./features/DailySearch";
import Tracker from "./features/Tracker";
import ResumeStudio from "./features/ResumeStudio";
import Assurance from "./features/Assurance";
import Profile from "./features/Profile";
import Settings from "./features/Settings";
import Agents, { AGENT_LABEL } from "./features/Agents";
import Assistant from "./features/Assistant";
import { PHONE_SHORT, PhoneMore } from "./components/PhoneNav";
import type { Summary } from "./types";
const tabs = [
  ["assistant", "Assistant", MessageSquareText, "Your search"],
  ["dashboard", "Dashboard", LayoutDashboard, "Your search"],
  ["daily", "Daily Search", Search, "Your search"],
  ["tracker", "Tracker", Radar, "Your search"],
  ["resumes", "Resume Studio", FileText, "Your search"],
  ["assurance", "Assurance", ShieldCheck, "Your search"],
  ["profile", "Profile", UserRound, "You"],
  ["agents", "Agents", Workflow, "Behind the scenes"],
  ["settings", "Settings", SlidersHorizontal, "Behind the scenes"],
] as const;
function routeFromHash() {
  const r = location.hash.slice(1).split("/")[0];
  // The chat is the front door: it opens first unless the address names a tab.
  return tabs.some((t) => t[0] === r) ? r : "assistant";
}

/** A render failure in one page must not unmount the whole app. */
class ErrorBoundary extends Component<
  { children: ReactNode },
  { message: string; recovering: boolean }
> {
  state = { message: "", recovering: false };
  lastRecovery = 0;
  static getDerivedStateFromError(error: unknown) {
    return { message: error instanceof Error ? error.message : String(error) };
  }
  componentDidCatch(error: unknown) {
    console.error("Page render failed:", error);
    // Most failures pass (data arriving mid-update, the app restarting): try once more by
    // itself, and only show the message when it fails again straight away.
    if (Date.now() - this.lastRecovery > 15000) {
      this.lastRecovery = Date.now();
      this.setState({ recovering: true });
      window.setTimeout(() => this.setState({ message: "", recovering: false }), 1500);
    }
  }
  render() {
    if (!this.state.message) return this.props.children;
    if (this.state.recovering)
      return (
        <div className="callout" role="status">
          Reloading this page…
        </div>
      );
    return (
      <div className="callout warning" role="alert">
        Something on this page failed to display ({this.state.message}). Your
        data is safe.
        <button
          className="secondary"
          onClick={() => this.setState({ message: "" })}
        >
          Try again
        </button>
        <button className="secondary" onClick={() => location.reload()}>
          Reload the app
        </button>
      </div>
    );
  }
}

function FirstProfile() {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await shellApi<{ profile: { id: string } }>("/profiles", "POST", { name: name.trim() });
      openProfile(created.profile.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the profile.");
      setBusy(false);
    }
  }

  return (
    <section className="card" style={{ maxWidth: 700, margin: "48px auto", padding: 32 }}>
      <span className="eyebrow">YOUR LOCAL WORKSPACE</span>
      <h1>Build your career workspace</h1>
      <p>Create a private profile, add your resume or notes, then select <strong>Build Agent for You</strong>. Your source documents and job history stay on this computer.</p>
      <form onSubmit={create} style={{ display: "grid", gap: 12, marginTop: 24 }}>
        <label htmlFor="first-profile-name">Your name</label>
        <input id="first-profile-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Enter your name" maxLength={80} />
        {error && <p role="alert" className="callout warning">{error}</p>}
        <button type="submit" className="primary" disabled={!name.trim() || busy}>{busy ? "Creating profile…" : "Create profile"}</button>
      </form>
    </section>
  );
}

export default function App() {
  // Which profile this tab belongs to (the /p/<id>/ address) and the others on this PC.
  const { listing, current, destination, ready, unavailable, reload } = useProfileListing();
  const onboarding = current?.state === "onboarding";
  // Tabs open once a profile exists and is built.
  const locked = !ready;
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const summaryReads = useRef(new LatestRequest()).current;
  useEffect(() => {
    if (!destination) return;
    // Only a successful profile listing can redirect a stale tab. With zero
    // profiles, return to the shell so the new profile screen has the right URL.
    if (destination === "/") location.replace("/");
    else location.assign(destination + location.hash);
  }, [destination]);
  useEffect(() => {
    if (current) document.title = `${firstName(current.name) || current.name} · Career Workspace`;
  }, [current]);
  const [route, setRoute] = useState(routeFromHash);
  const [data, setData] = useState<Summary>();
  const [error, setError] = useState("");
  const [toast, setToast] = useState<{ text: string; error: boolean } | null>(
    null,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [studioJob, setStudioJob] = useState<string | null>(() =>
    location.hash.startsWith("#resumes/")
      ? decodeURIComponent(location.hash.slice(9))
      : null,
  );
  const [add, setAdd] = useState(false);
  useEffect(() => {
    if (!listing || current) return;
    setData(undefined);
    setError("");
    setSelected(null);
    setAdd(false);
  }, [listing, current]);
  // The phone's More sheet (the tabs that are not in its bottom bar).
  const [moreOpen, setMoreOpen] = useState(false);
  const closeMore = useCallback(() => setMoreOpen(false), []);
  useEffect(() => setMoreOpen(false), [route]);
  // A new profile is set up in the chat; the form is one click away (remembered per browser).
  const [setupForm, setSetupForm] = useState(() => {
    try {
      return localStorage.getItem("setup-view") === "form";
    } catch {
      return false;
    }
  });
  const chooseSetup = (form: boolean) => {
    setSetupForm(form);
    try {
      localStorage.setItem("setup-view", form ? "form" : "chat");
    } catch {
      /* private window: the choice lasts for this page only */
    }
  };
  // A question another tab handed to the Assistant; `n` makes asking the same thing twice count.
  const [asked, setAsked] = useState<{ text: string; send: boolean; n: number } | null>(null);
  const ask = useCallback((text: string, send = true) => {
    setSelected(null);
    setAsked((prev) => ({ text, send, n: (prev?.n ?? 0) + 1 }));
    location.hash = "assistant";
    setRoute("assistant");
  }, []);
  const notify = useCallback(
    (text: string, error = false) => setToast({ text, error }),
    [],
  );
  const refresh = useCallback(async () => {
    if (!readyRef.current) return;
    try {
      await summaryReads.run(() => api<Summary>("/v2/summary"), (value) => {
        if (!readyRef.current) return;
        setData(value);
        setError("");
      });
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  }, [summaryReads]);
  useEffect(() => {
    if (!ready) return;
    refresh().catch(() => {});
    const timer = setInterval(() => refresh().catch(() => {}), 8000);
    const hash = () => {
      setRoute(routeFromHash());
      if (location.hash.startsWith("#resumes/"))
        setStudioJob(decodeURIComponent(location.hash.slice(9)));
    };
    window.addEventListener("hashchange", hash);
    return () => {
      clearInterval(timer);
      summaryReads.invalidate();
      window.removeEventListener("hashchange", hash);
    };
  }, [refresh, ready, summaryReads]);
  useEffect(() => {
    const update = () => {
      if (ready) void refresh().catch(() => {});
      void reload().catch(() => {});
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === "career-workspace:changed") update();
    };
    window.addEventListener("career-workspace:changed", update);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("career-workspace:changed", update);
      window.removeEventListener("storage", onStorage);
    };
  }, [refresh, reload, ready]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 10000);
    return () => clearTimeout(timer);
  }, [toast]);
  function navigate(r: string) {
    location.hash =
      r === "resumes" && studioJob
        ? "resumes/" + encodeURIComponent(studioJob)
        : r;
    setRoute(r);
  }
  function openStudio(id: string) {
    setStudioJob(id);
    location.hash = "resumes/" + encodeURIComponent(id);
    setRoute("resumes");
  }
  const job = data?.jobs.find((j) => j.id === selected);
  const working = (data?.runs || []).filter((r) =>
    ["queued", "running"].includes(r.state),
  );
  const workingJob = working[0]
    ? data?.jobs.find((j) => j.id === working[0].job_id)
    : undefined;
  const tab = tabs.find((t) => t[0] === route);
  return (
    <ProfileContext.Provider value={{ current, profiles: listing?.profiles || [], reload }}>
    <NoticeContext.Provider value={toast}>
      <AskContext.Provider value={ask}>
      <div className="app">
        <aside className="app-sidebar">
          {listing ? (
            <ProfileMenu />
          ) : (
            <div className="brand">
              <b>C</b>
              <div>
                CAREER<small>WORKSPACE</small>
              </div>
            </div>
          )}
          <nav aria-label="Main navigation">
            {tabs.map(([id, label, Icon, group], i) => (
              <div key={id} className={"nav-item" + (id in PHONE_SHORT ? "" : " nav-extra")}>
                {group !== tabs[i - 1]?.[3] && (
                  <span className="nav-group">{group}</span>
                )}
                <button
                  title={locked ? "Available once this profile is built" : label}
                  aria-current={route === id && !locked ? "page" : undefined}
                  className={route === id && !locked ? "active" : ""}
                  disabled={locked}
                  onClick={() => navigate(id)}
                >
                  <Icon size={20} />
                  <span>{label}</span>
                  <small className="nav-short" aria-hidden="true">
                    {PHONE_SHORT[id] || label}
                  </small>
                  {id === "agents" && working.length > 0 && (
                    <span className="nav-count" aria-label={`${working.length} working`}>
                      {working.length}
                    </span>
                  )}
                </button>
              </div>
            ))}
            {/* Phones only: the fifth tab, opening the sheet with Assurance, Profile, Agents and Settings. */}
            <div className="nav-item nav-more">
              <button
                type="button"
                title="More: Assurance, Profile, Agents, Settings"
                aria-haspopup="dialog"
                aria-expanded={moreOpen}
                className={!locked && !(route in PHONE_SHORT) ? "active" : ""}
                disabled={locked}
                onClick={() => setMoreOpen(!moreOpen)}
              >
                <Menu size={20} />
                <small className="nav-short">More</small>
                {working.length > 0 && (
                  <span className="nav-count" aria-label={`${working.length} working`}>
                    {working.length}
                  </span>
                )}
              </button>
            </div>
          </nav>
          <div className="sidebar-note">
            <span className="status-dot" /> Personal workspace
            <p>
              A little progress.
              <br />
              Every single day.
            </p>
            <small>Saved locally{current?.market ? ` · ${timeLabel(current.market.timezone)}` : ""}</small>
          </div>
        </aside>
        <PhoneMore
          open={moreOpen}
          tabs={tabs.map(([id, label, Icon]) => ({ id, label, Icon }))}
          route={route}
          working={working.length}
          onClose={closeMore}
          onGo={navigate}
        />
        <main>
          <header>
            <span className="crumb">
              {listing?.profiles.length === 0 ? (
                <>New profile <span aria-hidden="true">/</span> <b>Start</b></>
              ) : onboarding ? (
                <>
                  New profile <span aria-hidden="true">/</span> <b>Add documents</b>
                </>
              ) : (
                <>
                  {tab?.[3]} <span aria-hidden="true">/</span> <b>{tab?.[1]}</b>
                </>
              )}
            </span>
            <span className="header-status">
              <button
                className={"live-pill" + (working.length ? " busy" : "")}
                onClick={() => navigate("agents")}
                title="Open the Agents tab"
              >
                {working.length ? (
                  <>
                    <LoaderCircle className="spin" size={14} />
                    {AGENT_LABEL[working[0].kind] || working[0].kind}
                    {workingJob ? ` · ${workingJob.company}` : ""}
                    {working.length > 1 ? ` +${working.length - 1}` : ""}
                  </>
                ) : (
                  <>
                    <span className={"status-dot" + (error ? " off" : "")} />
                    {error ? "Connection needs attention" : "Agents idle"}
                  </>
                )}
              </button>
              {listing ? <ProfileMenu compact /> : <span className="avatar">CW</span>}
            </span>
          </header>
          <div className={"page" + (route === "assistant" || onboarding || listing?.profiles.length === 0 ? " assistant-page" : "")}>
            {unavailable ? (
              <section className="card" role="alert">
                <p>The profile list is unavailable. The app is retrying the connection.</p>
                <button className="secondary" onClick={() => void reload()}>Retry connection</button>
              </section>
            ) : listing?.profiles.length === 0 ? (
              <FirstProfile />
            ) : onboarding && current ? (
              <OnboardingWorkspace profile={current} notify={notify} onBuilt={reload} setupForm={setupForm} chooseSetup={chooseSetup} />
            ) : (
            <>
            {error && (
              <div className="callout warning" role="alert">
                {error}
                <button
                  className="secondary"
                  onClick={() => refresh().catch(() => {})}
                >
                  Retry connection
                </button>
              </div>
            )}
            {!data ? (
              <Loading label="Loading your workspace" />
            ) : (
              // Keyed by tab: a page that failed must not keep its message on the next tab.
              <ErrorBoundary key={route}>
                {route === "assistant" && (
                  <Assistant
                    data={data}
                    refresh={refresh}
                    notify={notify}
                    onJob={openStudio}
                    asked={asked}
                    onAsked={() => setAsked(null)}
                  />
                )}
                {route === "dashboard" && (
                  <Dashboard
                    data={data}
                    refresh={refresh}
                    notify={notify}
                    onJob={openStudio}
                    onDaily={() => navigate("daily")}
                    onAdd={() => setAdd(true)}
                    onGo={navigate}
                  />
                )}
                {route === "daily" && (
                  <DailySearch
                    data={data}
                    refresh={refresh}
                    notify={notify}
                    onJob={openStudio}
                    onAdd={() => setAdd(true)}
                  />
                )}
                {route === "tracker" && <Tracker notify={notify} />}
                {route === "resumes" && (
                  <ResumeStudio
                    data={data}
                    jobId={studioJob}
                    onJob={openStudio}
                    onDetails={setSelected}
                    refresh={refresh}
                  />
                )}
                {route === "assurance" && (
                  <Assurance
                    data={data}
                    notify={notify}
                    refresh={refresh}
                    onJob={openStudio}
                  />
                )}
                {route === "settings" && <Settings notify={notify} />}
                {route === "agents" && (
                  <Agents
                    data={data}
                    notify={notify}
                    onJob={openStudio}
                    onSettings={() => navigate("settings")}
                    onAssurance={() => navigate("assurance")}
                  />
                )}
                {route === "profile" && (
                  <Profile refresh={refresh} notify={notify} />
                )}
              </ErrorBoundary>
            )}
            </>
            )}
          </div>
        </main>
        {toast && (
          <div
            role={toast.error ? "alert" : "status"}
            className={"toast " + (toast.error ? "error" : "")}
          >
            <CheckCircle2 size={20} />
            <span>{toast.text}</span>
            <button
              aria-label="Dismiss notification"
              onClick={() => setToast(null)}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {job && data && (
          <JobDetail
            key={job.id}
            job={job}
            data={data}
            onClose={() => setSelected(null)}
            refresh={refresh}
            notify={notify}
          />
        )}
        {add && data && (
          <AddJob
            defaultLocation={
              !current?.market || current.market.code === "us" ? "Remote (US)" : current.market.default_location
            }
            date={data.goals.date}
            onClose={() => setAdd(false)}
            refresh={refresh}
            notify={notify}
            onSelect={openStudio}
          />
        )}
      </div>
      </AskContext.Provider>
    </NoticeContext.Provider>
    </ProfileContext.Provider>
  );
}
function AddJob({
  defaultLocation,
  date,
  onClose,
  refresh,
  notify,
  onSelect,
}: {
  defaultLocation: string;
  date: string;
  onClose: () => void;
  refresh: () => Promise<void>;
  notify: (s: string, e?: boolean) => void;
  onSelect: (s: string) => void;
}) {
  const [form, setForm] = useState({
    company: "",
    title: "",
    location: defaultLocation,
    url: "",
    requisition_id: "",
    description: "",
  });
  const [today, setToday] = useState(true);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="Save a job posting" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            const r = await api("/v2/jobs", "POST", form);
            // The sponsorship gate and the never-re-apply rules run before anything is saved.
            if (r.excluded) {
              await refresh();
              onClose();
              notify(
                `Not saved: the posting says “${r.sentence}”. ${r.reason_label}. It is listed under Excluded roles on the Dashboard, where you can restore it if that is wrong.`,
                true,
              );
              return;
            }
            if (r.blocked) {
              notify("Not saved: " + r.note, true);
              return;
            }
            if (today && !r.duplicate)
              await api("/search-runs/" + date + "/jobs/" + r.job.id, "POST");
            await refresh();
            onClose();
            onSelect(r.job.id);
            // Discovery gates on relevance before saving; a job you add by hand is
            // always saved, but you are told when it scores poorly and why.
            const weak =
              !r.duplicate && r.relevance && r.relevance.eligible === false
                ? ` Fit ${r.relevance.score}/100 — ${(r.relevance.blockers || []).join(" ")}`
                : "";
            notify(
              (r.duplicate
                ? "This posting is already saved. Opening its existing record."
                : r.upgraded
                  ? "Full posting added to the application previously tracked from Gmail."
                  : `Job saved to your workspace (sponsorship tier ${r.job?.sponsor_tier || "C"}).`) + weak,
              !!weak,
            );
          } catch (e) {
            notify((e as Error).message, true);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-grid">
          {[
            ["company", "Company"],
            ["title", "Job title"],
            ["location", "Location"],
            ["requisition_id", "Requisition ID (optional)"],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                required={key !== "requisition_id"}
                maxLength={150}
                value={form[key as keyof typeof form]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              />
            </Field>
          ))}
        </div>
        <Field label="Direct posting URL">
          <input
            type="url"
            required
            maxLength={2500}
            value={form.url}
            onChange={(e) => setForm({ ...form, url: e.target.value })}
          />
        </Field>
        <Field label="Full job description">
          <textarea
            rows={9}
            required
            minLength={80}
            maxLength={100000}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </Field>
        <label className="check-line">
          <input
            type="checkbox"
            checked={today}
            onChange={(e) => setToday(e.target.checked)}
          />
          Add new posting to today’s search
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save job"}
        </button>
      </form>
    </Modal>
  );
}
