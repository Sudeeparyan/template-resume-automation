import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Lock, Plus, Settings2, Trash2, RotateCcw } from "lucide-react";
import { PROFILE_ID, shellApi } from "./api";
import { Badge, Field, Modal, Switch } from "./components/UI";

export type Market = {
  code: string;
  name: string;
  adjective?: string;
  paper: string;
  timezone: string;
  default_location: string;
  pages?: number;
  min_body_pt?: number;
  max_body_pt?: number;
  /** The country's own tier wording (empty for the US: the page's wording applies). */
  tier_labels?: Record<string, string>;
};
export type ProfileSchedule = {
  time?: string;
  enabled?: boolean;
  task?: string;
  note?: string;
  exists?: boolean;
  supported?: boolean;
  next_run?: string | null;
  legacy?: boolean;
};
export type ProfileEntry = {
  id: string;
  name: string;
  country: string;
  target_markets?: ("ie" | "us")[];
  work_authorization_by_market?: Partial<Record<"ie" | "us", {
    status: "authorized" | "needs_sponsorship" | "unknown";
    citizenship: "citizen" | "noncitizen" | "unknown";
    needs_sponsorship_later?: "yes" | "no" | "unknown";
  }>>;
  state: "onboarding" | "ready";
  locked: boolean;
  legacy?: boolean;
  initials: string;
  created_at?: string;
  market: Market | null;
  schedule?: ProfileSchedule;
};
export type ProfileListing = { profiles: ProfileEntry[]; last_used: string };

/** The profile this tab belongs to, and every other profile on this PC. */
export const ProfileContext = createContext<{
  current: ProfileEntry | null;
  profiles: ProfileEntry[];
  reload: () => Promise<void>;
}>({ current: null, profiles: [], reload: async () => {} });
export const useProfiles = () => useContext(ProfileContext);

/** Open another profile: a fresh page, so nothing of this one stays in memory. */
export const openProfile = (id: string, hash = "") => {
  location.assign(`/p/${id}/` + (hash ? "#" + hash : ""));
};

/** Human-readable time zone label for the active profile. */
export function timeLabel(zone?: string) {
  if (!zone) return "Dublin time";
  if (zone === "America/Chicago") return "US Central time";
  const city = zone.split("/").pop() || zone;
  return city.replace(/_/g, " ") + " time";
}

function marketNames(profile: ProfileEntry): string {
  const codes = profile.target_markets?.length ? profile.target_markets : [profile.country];
  return codes.map((code) => code === "ie" ? "Ireland" : code === "us" ? "United States" : profile.market?.name || code).join(" and ");
}

/** Wording that follows this tab's profile and resume contract. */
export function useMarket(jobMarket?: string) {
  const current = useContext(ProfileContext).current;
  const market = current?.market;
  const dual = current?.target_markets?.length === 2 && !jobMarket;
  const code = jobMarket === "ie" || jobMarket === "us" ? jobMarket : market?.code;
  const us = code === "us";
  const primary = !jobMarket || code === market?.code;
  const pages = primary ? market?.pages || 2 : 1;
  const paper = primary ? market?.paper || "A4" : us ? "US Letter" : "A4";
  const pageLabel = pages === 1 ? "one page" : `${pages} pages`;
  return {
    us,
    dual,
    paper,
    pages,
    pageLabel,
    resumeShape: dual ? "market-specific" : `${pageLabel} ${paper}`,
    minBodyPt: primary ? market?.min_body_pt || 10 : 10,
    maxBodyPt: primary ? market?.max_body_pt || 12 : 11,
    time: timeLabel(market?.timezone),
    postings: dual ? "Ireland and US postings" : us ? "US postings" : `${market?.adjective || market?.name} postings`,
    tierLabels: us ? {} : market?.tier_labels || {},
  };
}

export function firstName(name?: string) {
  return (name || "").trim().split(/\s+/)[0] || "";
}

/** The current profile's badge and name; opens the menu of profiles. */
export function ProfileMenu({ compact = false }: { compact?: boolean }) {
  const { current, profiles } = useProfiles();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const label = firstName(current?.name).toUpperCase() || "CAREER";
  return (
    <div className={"profile-menu" + (compact ? " compact" : "")} ref={ref}>
      <button
        className={compact ? "avatar avatar-button" : "brand brand-button"}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Switch profile"
        onClick={() => setOpen((o) => !o)}
      >
        {compact ? (
          current?.initials || "?"
        ) : (
          <>
            <b>{(current?.initials || "C").slice(0, 1)}</b>
            <div>
              <span className="brand-name">{label}</span>
              <small>
                Workspace
                {current?.market ? ` · ${current.target_markets?.length === 2 ? "IE + US" : current.market.code.toUpperCase()}` : ""}
              </small>
            </div>
            <ChevronDown size={16} className="brand-chevron" />
          </>
        )}
      </button>
      {open && (
        <div className="profile-popover" role="menu">
          <div className="eyebrow">PROFILES ON THIS PC</div>
          {profiles.map((p) => (
            <button
              key={p.id}
              role="menuitemradio"
              aria-checked={p.id === current?.id}
              className={"profile-row" + (p.id === current?.id ? " current" : "")}
              onClick={() => (p.id === current?.id ? setOpen(false) : openProfile(p.id))}
            >
              <span className="profile-initials">{p.initials}</span>
              <span className="profile-row-text">
                <b>{p.name}</b>
                <small>
                  {p.state === "onboarding"
                    ? "Setting up"
                    : p.market
                      ? `${marketNames(p)} · ${p.target_markets?.length === 2 ? "market-specific" : p.market.paper} resumes`
                      : "Ready"}
                  {p.locked ? " · backup" : ""}
                </small>
              </span>
              {p.id === current?.id && <Check size={16} />}
            </button>
          ))}
          <div className="profile-popover-actions">
            <button className="secondary" onClick={() => { setOpen(false); setCreating(true); }}>
              <Plus size={15} /> New profile
            </button>
            <button
              className="secondary"
              onClick={() => {
                setOpen(false);
                location.hash = "settings";
                setTimeout(() => document.getElementById("this-profile")?.scrollIntoView({ behavior: "smooth" }), 150);
              }}
            >
              <Settings2 size={15} /> Manage
            </button>
          </div>
        </div>
      )}
      {creating && <NewProfile onClose={() => setCreating(false)} />}
    </div>
  );
}

/** Name a new, empty profile; its own page then asks for the documents. */
export function NewProfile({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal title="New profile" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            const r = await shellApi<{ profile: ProfileEntry }>("/profiles", "POST", { name });
            openProfile(r.profile.id);
          } catch (err) {
            setError((err as Error).message);
            setBusy(false);
          }
        }}
      >
        <p>
          Each profile is a separate workspace: its own documents, jobs, resumes, chats and
          settings. Nothing is shared with any other profile.
        </p>
        <Field label="Whose profile is it? (full name)">
          <input autoFocus required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {error && <p className="danger-text" role="alert">{error}</p>}
        <button className="primary" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create and add their documents"}
        </button>
      </form>
    </Modal>
  );
}

/** Settings → This profile: the daily search task, and Reset / Delete with a warning. */
export function ProfileSettings({ notify }: { notify: (text: string, error?: boolean) => void }) {
  const { current, reload } = useProfiles();
  const [schedule, setSchedule] = useState<ProfileSchedule | null>(null);
  const [action, setAction] = useState<"reset" | "delete" | null>(null);
  useEffect(() => {
    if (!current || current.state !== "ready") return;
    shellApi<ProfileSchedule>(`/profiles/${current.id}/schedule`).then(setSchedule).catch(() => setSchedule(null));
  }, [current]);
  if (!current) return null;
  return (
    <section className="card spaced" id="this-profile">
      <div className="section-title">
        <h2>This profile</h2>
        {current.locked ? (
          <Badge tone="green">
            <Lock size={12} /> Backup profile
          </Badge>
        ) : (
          <Badge>{current.market ? marketNames(current) : "Not built yet"}</Badge>
        )}
      </div>
      <p>
        <b>{current.name}</b>
        {current.market
          ? current.target_markets?.length === 2
            ? ` searches ${marketNames(current)}, with the right resume format and work rules for each market.`
            : ` searches ${marketNames(current)}, with ${current.market.paper} resumes and ${timeLabel(current.market.timezone)}.`
          : " has not been built from documents yet."}{" "}
        Its documents, jobs, resumes, chats and settings are kept in its own folder and never mix with another profile.
      </p>
      {schedule && (
        <div className="profile-schedule">
          <div>
            <b>Daily job search</b>
            <small className="muted">
              {schedule.legacy
                ? `Runs every morning at ${schedule.time} (${schedule.task}).`
                : schedule.supported === false
                  ? "Scheduled searches are set up on Windows only."
                  : schedule.exists
                    ? `Runs every day at ${schedule.time}${schedule.next_run ? `; next run ${schedule.next_run.replace("T", " ")}` : ""}.`
                    : `Not scheduled. Switch it on to search every morning at ${schedule.time || "07:20"}.`}
              {schedule.note && !schedule.exists ? ` ${schedule.note}` : ""}
            </small>
          </div>
          {!schedule.legacy && schedule.supported !== false && (
            <Switch
              label="Daily job search"
              checked={Boolean(schedule.exists && schedule.enabled !== false)}
              onChange={async (next) => {
                try {
                  setSchedule(await shellApi(`/profiles/${current.id}/schedule`, "PUT", { enabled: next }));
                  notify(next ? "The daily search is on." : "The daily search is off.");
                } catch (e) {
                  notify((e as Error).message, true);
                }
              }}
            />
          )}
        </div>
      )}
      <div className="danger-zone">
        <div className="danger-zone-head">
          <AlertTriangle size={18} />
          <div>
            <b>Reset or delete this profile</b>
            <small>
              {current.locked
                ? `${current.name} is the backup profile. It is locked and can never be reset or deleted.`
                : "Reset erases everything and starts again from the documents; Delete removes the profile for good. Neither can be undone."}
            </small>
          </div>
        </div>
        <div className="danger-zone-actions">
          <button className="secondary" disabled={current.locked} onClick={() => setAction("reset")}>
            <RotateCcw size={15} /> Reset profile
          </button>
          <button className="danger" disabled={current.locked} onClick={() => setAction("delete")}>
            <Trash2 size={15} /> Delete profile
          </button>
        </div>
      </div>
      {action && (
        <ConfirmErase
          profile={current}
          action={action}
          onClose={() => setAction(null)}
          onDone={async (listing) => {
            await reload();
            if (action === "delete") {
              const next = listing.last_used || listing.profiles[0]?.id;
              if (next) openProfile(next);
              else location.assign("/");
            }
            else location.reload();
          }}
        />
      )}
    </section>
  );
}

/** The irreversible step: a plain warning and the profile's name typed in full. */
export function ConfirmErase({
  profile,
  action,
  onClose,
  onDone,
}: {
  profile: ProfileEntry;
  action: "reset" | "delete";
  onClose: () => void;
  onDone: (listing: ProfileListing) => Promise<void>;
}) {
  const [typed, setTyped] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const matches = typed.trim().toLowerCase() === profile.name.trim().toLowerCase();
  const verb = action === "reset" ? "Reset" : "Delete";
  return (
    <Modal title={`${verb} ${profile.name}'s profile?`} onClose={onClose}>
      <div className="callout erase-warning" role="alert">
        <AlertTriangle size={20} />
        <div>
          <b>This process cannot be reverted.</b>
          <p>
            {action === "reset"
              ? `Everything in ${profile.name}'s profile is permanently erased: the uploaded documents, the profile, every job, resume, study plan, chat and setting. The profile then starts again from zero, asking for documents.`
              : `${profile.name}'s profile is permanently deleted: the uploaded documents, the profile, every job, resume, study plan, chat and setting, and its daily search. It disappears from the profile list.`}{" "}
            No backup is kept. Other profiles are not affected.
          </p>
        </div>
      </div>
      <Field label={`Type ${profile.name} to confirm`}>
        <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
      </Field>
      <label className="check-line">
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
        I understand this cannot be undone.
      </label>
      {error && <p className="danger-text" role="alert">{error}</p>}
      <div className="actions">
        <button className="secondary" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          className="danger"
          disabled={!matches || !understood || busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const listing = await shellApi<ProfileListing>(
                action === "reset" ? `/profiles/${profile.id}/reset` : `/profiles/${profile.id}`,
                action === "reset" ? "POST" : "DELETE",
                { confirm: typed },
              );
              await onDone(listing);
            } catch (e) {
              setError((e as Error).message);
              setBusy(false);
            }
          }}
        >
          {busy ? (action === "reset" ? "Resetting…" : "Deleting…") : `${verb} permanently`}
        </button>
      </div>
    </Modal>
  );
}

/** Load the profile list once; a page served without profiles (tests, old server) keeps working. */
export function useProfileListing() {
  const [listing, setListing] = useState<ProfileListing | null>(null);
  const [failed, setFailed] = useState(false);
  const reload = useCallback(async () => {
    try {
      setListing(await shellApi<ProfileListing>("/profiles"));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  const current = listing?.profiles.find((p) => p.id === PROFILE_ID) || null;
  return { listing, current, failed, reload };
}
