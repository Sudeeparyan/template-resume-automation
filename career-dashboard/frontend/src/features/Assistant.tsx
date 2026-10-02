import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  CalendarCheck,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardPaste,
  Copy,
  Cpu,
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
  Globe2,
  History,
  LoaderCircle,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  Pencil,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Square,
  SquarePen,
  Trash2,
  Workflow,
  X,
  XCircle,
} from "lucide-react";
import type { ComponentType } from "react";
import { API_BASE, api, fileUrl, safeUrl, shellApi, uploadFile } from "../api";
import { Badge, Modal, RichText } from "../components/UI";
import QuestionCard from "../components/QuestionCard";
import type {
  AssistantConversation,
  AssistantMessage,
  AssistantOverview,
  AssistantStep,
  ApplicationDocuments,
  Job,
  Summary,
} from "../types";
import { TierBadge } from "../components/JobList";
import { useMarket, useProfiles } from "../profiles";
import SourceLibrary from "./SourceLibrary";

type Props = {
  data: Summary;
  refresh: () => Promise<void>;
  notify: (text: string, error?: boolean) => void;
  onJob: (id: string) => void;
  /** A question another tab handed over ("Ask the assistant"); cleared once taken. */
  asked?: { text: string; send: boolean; n: number } | null;
  onAsked?: () => void;
};

type Starter = { label: string; hint: string; send?: string; icon: ComponentType<{ size?: number }> };

/** What an empty chat offers: each is a real message the agent understands. "Paste a posting" focuses the box. */
const STARTERS: Starter[] = [
  { label: "Paste a posting", hint: "The whole job description plus its link; a contract-sized PDF comes back.", icon: ClipboardPaste },
  { label: "Find jobs for me", hint: "Today's search through your company list and the job boards, checked against your must-haves.", send: "find jobs", icon: Search },
  { label: "Build missing resumes", hint: "Every saved job without a resume gets one, fitted to its page contract and scored.", send: "Which saved jobs still have no resume? Build them", icon: FileText },
  { label: "What's waiting on me?", hint: "Read from the tracker; nothing changes.", send: "What did I apply to this week, and what's still waiting?", icon: CalendarCheck },
];

const PENDING_LABEL: Record<string, string> = {
  posting_link: "Waiting for the posting link",
  posting_fields: "Waiting for Company | Job title | Location",
  confirm_applied: "Waiting for yes or no",
  confirm_tool: "Waiting for your yes before it changes anything",
  choose_job: "Waiting for you to pick a posting",
  agent: "Waiting for your answer",
};

/** The short name a step shows for the registry agent that took it. */
const AGENT_SHORT: Record<string, string> = {
  assistant: "Assistant",
  orchestrator: "Orchestrator",
  sponsorship: "Sponsorship gate",
  reapply: "Never re-apply",
  discovery: "Job discovery",
  fit: "Requirement check",
  resume: "Resume Studio",
  resume_match: "Resume scorer",
  resume_tracker: "Tracker",
  research: "Company research",
  hiring: "Hiring review",
  match: "Profile comparison",
  study_plan: "Study planner",
  profile: "Profile curator",
  email: "Email evidence",
};

/** Polling: quick while a reply is being worked on, relaxed when idle, slow in a hidden tab. */
const POLL_BUSY = 1200;
const POLL_IDLE = 8000;
const POLL_HIDDEN = 30000;
/** A message longer than this folds until "Show all" is pressed. */
const FOLD_AT = 700;

const active = (state: string) => state === "queued" || state === "running";

function requestId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : Date.now().toString(36) + Math.random().toString(36).slice(2);
}

/** "18:30" in the reader's own clock. */
export function clock(iso: string): string {
  const at = new Date(iso);
  return isNaN(at.getTime()) ? "" : at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "12 s", "1 m 05 s": how long an exchange took. */
export function elapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s + " s";
  return Math.floor(s / 60) + " m " + String(s % 60).padStart(2, "0") + " s";
}

/** "Today", "Yesterday", then "Mon, Sep 15" (with the year once it differs). */
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function dayLabel(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (isNaN(at.getTime())) return "";
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(at)) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  // Fixed abbreviations: toLocaleDateString's short month is CLDR-dependent
  // ("Sep" vs "Sept"), so the same day rendered differently across machines.
  const base = `${DAY_NAMES[at.getDay()]}, ${MONTH_NAMES[at.getMonth()]} ${at.getDate()}`;
  return at.getFullYear() === now.getFullYear() ? base : `${base}, ${at.getFullYear()}`;
}

/** Whether an exchange mentions the search words (all of them, in either side). */
export function matches(m: Pick<AssistantMessage, "message" | "response">, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = (m.message + "\n" + m.response).toLowerCase();
  return words.every((w) => text.includes(w));
}

/** The conversation as a Markdown file she can keep or send. */
export function exportMarkdown(title: string, messages: AssistantMessage[], now: Date = new Date()): string {
  const lines = [`# ${title}`, "", `Assistant chat exported ${now.toLocaleString()} · ${messages.length} ${messages.length === 1 ? "exchange" : "exchanges"}`, ""];
  for (const m of messages) {
    lines.push("---", "", `**You** · ${new Date(m.created_at).toLocaleString()}`, "", m.message, "");
    const state = m.state === "failed" ? " (failed)" : m.state === "processing" ? " (still working)" : "";
    lines.push(`**Assistant**${state} · ${new Date(m.updated_at).toLocaleString()}`, "", m.response, "");
    if (m.data.pdf) lines.push(`Resume PDF: ${m.data.pdf}`, "");
  }
  return lines.join("\n");
}

/** The one-tap answers a pending question accepts, so a yes never needs typing. */
/** A suggestion ending in "…" to be completed: its text without the ellipsis, else null. */
export function unfinished(text: string): string | null {
  return /(…|\.\.\.)\s*$/.test(text) ? text.replace(/\s*(…|\.\.\.)\s*$/, " ") : null;
}

export function quickReplies(pending: AssistantOverview["pending"]): { label: string; send: string }[] {
  if (!pending) return [];
  if (pending.kind === "confirm_tool" || pending.kind === "confirm_applied") {
    return [
      { label: "Yes, go ahead", send: "yes" },
      { label: "No, skip it", send: "no" },
    ];
  }
  if (pending.kind === "choose_job") {
    return (pending.candidates || []).slice(0, 6).map((c, i) => ({
      label: `${i + 1}. ${c.company} — ${c.title}`,
      send: String(i + 1),
    }));
  }
  return [];
}

/** Files shown beside the chat, including the profile's first resume before any job is saved. */
export function assistantOutputEntries(documents: ApplicationDocuments[], baseResumePath: string | null) {
  const entries: { path: string; label: string }[] = [];
  if (baseResumePath) entries.push({ path: baseResumePath, label: "Base resume" });
  for (const document of documents) {
    for (const resume of document.resumes)
      entries.push({ path: resume.path, label: `${document.company} · ${resume.label}` });
    if (document.cover_letter)
      entries.push({ path: document.cover_letter.path, label: `${document.company} · Cover letter v${document.cover_letter.version}` });
  }
  return entries.slice(0, 5);
}

/** Whether the reader is close enough to the bottom for new activity to keep them there. */
function nearBottom() {
  const root = document.documentElement;
  return window.innerHeight + window.scrollY >= root.scrollHeight - 140;
}

/** The true end of the page: there the sticky composer sits below the thread, hiding nothing. */
function scrollToEnd(behavior: ScrollBehavior) {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior });
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // An older browser or a page without clipboard permission: the selection route still works.
    const box = document.createElement("textarea");
    box.value = text;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.opacity = "0";
    document.body.appendChild(box);
    box.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    box.remove();
    return ok;
  }
}

function greeting(now: Date = new Date()) {
  const h = now.getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Assistant({ data, refresh, notify, onJob, asked, onAsked }: Props) {
  const { current: profile, reload: reloadProfile } = useProfiles();
  const profileId = profile?.id;
  const [baseResumePath, setBaseResumePath] = useState<string | null>(null);
  const loadBaseResume = useCallback(async () => {
    if (!profileId) return;
    try {
      const state = await shellApi<{ base_resume?: { pdf?: string | null; status?: string } }>(`/profiles/${profileId}/intake`);
      const resume = state.base_resume;
      setBaseResumePath(resume?.pdf?.startsWith("base/") && ["PASS", "AUTOMATED_PASS_MANUAL_PENDING"].includes(resume.status || "") ? resume.pdf : null);
    } catch {
      setBaseResumePath(null);
    }
  }, [profileId]);
  useEffect(() => { void loadBaseResume(); }, [loadBaseResume]);
  const [overview, setOverview] = useState<AssistantOverview>();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // The message just sent, shown at once while the server records it.
  const [echo, setEcho] = useState<{ id: string; text: string; at: string } | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [stepsOpen, setStepsOpen] = useState<Record<string, boolean>>({});
  const [moreOpen, setMoreOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<AssistantConversation | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [unseen, setUnseen] = useState(false);
  const [away, setAway] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const boxRef = useRef<HTMLTextAreaElement>(null);
  // A document about the candidate, read on the server; its text goes into the composer
  // with a request to propose what is new, and each change still waits for a yes.
  const fileRef = useRef<HTMLInputElement>(null);
  const [attaching, setAttaching] = useState(false);
  async function attach(file: File) {
    setAttaching(true);
    try {
      const r = await uploadFile<{ name: string; text: string; characters: number }>(API_BASE + "/v2/documents/text", file, file.name);
      const intro = `Here is a document about me (${r.name}). Compare it with my profile and propose anything new or different for me to confirm; change nothing without my yes.\n\n`;
      const text = intro + r.text.slice(0, 118000 - intro.length);
      setDraft(text);
      if (boxRef.current) {
        boxRef.current.value = text;
        grow(boxRef.current);
        boxRef.current.focus();
      }
      notify(r.characters > 118000 ? `${r.name} is long: the first part is in the message box. Send it, then attach it again for the rest.` : `${r.name} is in the message box. Send it when ready.`);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setAttaching(false);
    }
  }
  const searchRef = useRef<HTMLInputElement>(null);
  // Which messages were still processing at the last poll: when one settles,
  // the rest of the app is refreshed so every tab shows what the chat changed.
  const processing = useRef<Set<string>>(new Set());
  // Every load carries a sequence number: a slow poll that lands after a newer
  // one is dropped, so the thread never flicks back to an older state.
  const seq = useRef(0);
  const timer = useRef(0);
  const alive = useRef(true);
  const tickRef = useRef<() => Promise<void>>(async () => {});
  // The reader is at the bottom of the thread, so new activity may scroll it.
  const stick = useRef(true);
  const firstScroll = useRef(true);
  // While the page scrolls itself (smoothly) the passing positions are not the
  // reader's choice, so they must not turn the pinning off.
  const autoUntil = useRef(0);
  // On a phone the keyboard's Enter is a new line; the Send button sends.
  const touch = useMemo(() => typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches, []);
  const mac = useMemo(() => /Mac|iPhone|iPad/.test(navigator.userAgent || ""), []);
  const mod = mac ? "⌘" : "Ctrl+";

  /** A fresh overview from an action (new chat, open, delete) replaces whatever a slower poll would bring. */
  const adopt = useCallback((next: AssistantOverview) => {
    seq.current++;
    processing.current = new Set(next.messages.filter((m) => m.state === "processing").map((m) => m.id));
    setOverview(next);
  }, []);

  const load = useCallback(async () => {
    const n = ++seq.current;
    const next = await api<AssistantOverview>("/v2/assistant", "GET", undefined, { timeout: 20000 });
    if (n !== seq.current || !alive.current) return next;
    const settled = next.messages.filter(
      (m) => processing.current.has(m.id) && m.state !== "processing",
    );
    processing.current = new Set(
      next.messages.filter((m) => m.state === "processing").map((m) => m.id),
    );
    setOverview(next);
    setEcho((e) => (e && next.messages.some((m) => m.id === e.id) ? null : e));
    if (settled.length) {
      await refresh().catch(() => {});
      for (const m of settled)
        if (m.state === "failed" && m.data.intent !== "stopped") notify(m.response, true);
    }
    return next;
  }, [refresh, notify]);

  const schedule = useCallback((ms: number) => {
    if (!alive.current) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void tickRef.current(), ms);
  }, []);

  const tick = useCallback(async () => {
    if (!alive.current) return;
    let busy = false;
    try {
      busy = (await load()).busy;
      if (alive.current) setError("");
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    }
    schedule(busy ? POLL_BUSY : document.visibilityState === "hidden" ? POLL_HIDDEN : POLL_IDLE);
  }, [load, schedule]);
  tickRef.current = tick;

  useEffect(() => {
    alive.current = true;
    void tickRef.current();
    // Coming back to the tab (or the window) checks at once instead of waiting out a long timer.
    const wake = () => {
      if (document.visibilityState === "visible") void tickRef.current();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    const onScroll = () => {
      const bottom = nearBottom();
      setAway(!bottom);
      if (autoUntil.current > Date.now()) {
        if (bottom) autoUntil.current = 0;
        return;
      }
      stick.current = bottom;
      if (bottom) setUnseen(false);
    };
    // A wheel or a finger is the reader: it ends any scroll the page started.
    const onGesture = () => {
      autoUntil.current = 0;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("wheel", onGesture, { passive: true });
    window.addEventListener("touchmove", onGesture, { passive: true });
    return () => {
      alive.current = false;
      seq.current++;
      window.clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("wheel", onGesture);
      window.removeEventListener("touchmove", onGesture);
    };
  }, []);

  const messages = overview?.messages || [];
  const latest = messages[messages.length - 1];
  const working = !!echo || latest?.state === "processing";
  // A once-a-second clock only while something is being worked on, for the elapsed time.
  useEffect(() => {
    if (!working) return;
    const clockTimer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(clockTimer);
  }, [working]);
  // "Stopping…" ends when the reply does.
  useEffect(() => {
    if (!working) setStopping(false);
  }, [working]);

  // Only real changes to the thread move the page: a new exchange, a step, a settled
  // reply. A reader who scrolled up keeps their place and gets a "new activity" nudge.
  const loaded = !!overview;
  const count = messages.length;
  const lastState = latest?.state;
  const lastSteps = latest?.steps.length;
  const echoId = echo?.id;
  const conversationId = overview?.conversation_id;
  useEffect(() => {
    if (!loaded || (count === 0 && !echoId)) return; // the welcome card reads from the top
    if (query) return; // a filtered thread is read where the reader is
    if (stick.current) {
      autoUntil.current = Date.now() + 1500;
      scrollToEnd(firstScroll.current ? "auto" : "smooth");
      if (firstScroll.current) {
        // Fonts and previews that land after the first paint make the thread taller.
        document.fonts?.ready.then(() => stick.current && scrollToEnd("auto")).catch(() => {});
      }
      firstScroll.current = false;
    } else if (count || echoId) {
      setUnseen(true);
    }
  }, [loaded, count, lastState, lastSteps, echoId, conversationId, query]);

  function jumpToLatest() {
    stick.current = true;
    setUnseen(false);
    autoUntil.current = Date.now() + 1500;
    scrollToEnd("smooth");
  }

  /** A different thread reads from its own end, with nothing folded open. */
  function resetThread() {
    setExpanded({});
    setStepsOpen({});
    setUnseen(false);
    setQuery("");
    setSearchOpen(false);
    stick.current = true;
    firstScroll.current = true;
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    const id = requestId();
    setSending(true);
    setError("");
    setEcho({ id, text: message, at: new Date().toISOString() });
    setDraft("");
    if (boxRef.current) boxRef.current.style.height = "";
    if (query) {
      setQuery("");
      setSearchOpen(false);
    }
    stick.current = true;
    setUnseen(false);
    try {
      await api("/v2/assistant/messages", "POST", { message, request_id: id });
      await load();
      schedule(POLL_BUSY);
    } catch (e) {
      setEcho(null);
      setDraft(message);
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  /** Stop the reply being worked on; the worker ends it at its next step. */
  async function stop() {
    const id = latest?.state === "processing" ? latest.id : echo?.id;
    if (!id || stopping) return;
    setStopping(true);
    try {
      await api(`/v2/assistant/messages/${encodeURIComponent(id)}/stop`, "POST");
      await load();
      schedule(POLL_BUSY);
    } catch (e) {
      setStopping(false);
      notify((e as Error).message, true);
    }
  }

  async function newChat() {
    if (working) {
      notify("Wait for this reply, or stop it, before starting a new chat.", true);
      return;
    }
    try {
      adopt(await api<AssistantOverview>("/v2/assistant/conversations", "POST"));
      resetThread();
      setHistoryOpen(false);
      boxRef.current?.focus();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }

  async function openChat(id: string) {
    if (id === overview?.conversation_id) {
      setHistoryOpen(false);
      return;
    }
    try {
      adopt(await api<AssistantOverview>(`/v2/assistant/conversations/${encodeURIComponent(id)}`, "PUT"));
      resetThread();
      setHistoryOpen(false);
    } catch (e) {
      notify((e as Error).message, true);
    }
  }

  async function deleteChat(c: AssistantConversation) {
    try {
      adopt(await api<AssistantOverview>(`/v2/assistant/conversations/${encodeURIComponent(c.id)}`, "DELETE"));
      setConfirmDelete(null);
      if (c.current) resetThread();
      notify("Chat deleted. The jobs, resumes and profile changes it made are still in the workspace.");
    } catch (e) {
      notify((e as Error).message, true);
    }
  }

  async function clearHistory() {
    try {
      adopt(await api<AssistantOverview>("/v2/assistant/conversations", "DELETE"));
      setConfirmClear(false);
      setHistoryOpen(false);
      resetThread();
      notify("Chat history cleared. The jobs, resumes and profile changes those chats made are still in the workspace.");
      boxRef.current?.focus();
    } catch (e) {
      notify((e as Error).message, true);
    }
  }

  function exportChat() {
    if (!messages.length) return;
    const title = currentConversation?.title || "Assistant chat";
    const stamp = new Date().toISOString().slice(0, 10);
    const name = "assistant-" + (title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "chat") + "-" + stamp + ".md";
    const blob = new Blob([exportMarkdown(title, messages)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("Saved " + name + " to your downloads.");
  }

  function toggleSearch(open = !searchOpen) {
    setSearchOpen(open);
    if (!open) setQuery("");
    else window.setTimeout(() => searchRef.current?.focus(), 0);
  }

  // A question from another tab, taken once the thread has loaded so it lands after it. One
  // that only reads is sent; one that would start work waits in the box, as does anything
  // asked while a reply is still being worked on.
  const askedN = asked?.n;
  useEffect(() => {
    if (!asked || !overview) return;
    onAsked?.();
    if (asked.send && !working && !sending) void send(asked.text);
    else prompt(asked.text);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askedN, !!overview]);

  /** Pre-fills the composer and never sends (an edit, a suggestion to finish, a question from
   *  another tab that would start work). A complete message is one Enter away. */
  function prompt(text: string) {
    setDraft(text);
    const box = boxRef.current;
    if (box) {
      grow(box);
      box.focus();
      window.setTimeout(() => {
        box.setSelectionRange(text.length, text.length);
        grow(box);
        box.scrollIntoView({ block: "nearest" });
      }, 0);
    }
  }

  async function toggleAutoApply(enabled: boolean) {
    try {
      const next = await api<AssistantOverview>("/v2/assistant/auto-apply", "PUT", { enabled });
      adopt(next);
      notify(
        enabled
          ? "Auto-apply on: changes run without asking first. Each one is still listed in the chat, marked auto-applied."
          : "Auto-apply off: every change asks for your yes first.",
      );
    } catch (e) {
      notify((e as Error).message, true);
    }
  }

  function grow(el: HTMLTextAreaElement) {
    el.style.height = "";
    el.style.height = Math.min(el.scrollHeight, 260) + "px";
  }

  // Keyboard: Esc closes what is open, ⌘⇧O starts a chat, ⌘K searches, "/" jumps to the box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const cmd = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();
      if (cmd && e.shiftKey && key === "o") {
        e.preventDefault();
        void newChat();
      } else if (cmd && !e.shiftKey && key === "k") {
        e.preventDefault();
        toggleSearch();
      } else if (e.key === "Escape") {
        if (confirmDelete || confirmClear) return; // the dialog closes itself
        if (moreOpen) return; // the menu closes itself
        if (historyOpen) setHistoryOpen(false);
        else if (searchOpen) toggleSearch(false);
      } else if (e.key === "/" && !cmd && !e.altKey) {
        const t = e.target as HTMLElement | null;
        if (t && !/^(input|textarea|select)$/i.test(t.tagName) && !t.isContentEditable) {
          e.preventDefault();
          boxRef.current?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const jobs = new Map(data.jobs.map((j) => [j.id, j]));
  const runningNow = data.runs.filter((r) => active(r.state)).length;
  const current = latest?.state === "processing" ? latest.steps[latest.steps.length - 1] : undefined;
  const startedAt = echo ? new Date(echo.at).getTime() : latest?.state === "processing" ? new Date(latest.created_at).getTime() : 0;
  const quick = quickReplies(overview?.pending ?? null);
  const names = new Map(overview?.agents.map((a) => [a.id, AGENT_SHORT[a.id] || a.name]));
  const conversations = overview?.conversations || [];
  const currentConversation = conversations.find((c) => c.current);
  const q = query.trim();
  const shown = q ? messages.filter((m) => matches(m, q)) : messages;
  const empty = messages.length === 0 && !echo;
  const heading = currentConversation?.title || (empty ? "New chat" : "Assistant");
  const engine = overview?.engine;
  const outputEntries = assistantOutputEntries(data.documents, baseResumePath);
  return (
    <div className="chat assistant-workspace">
      <aside className="assistant-project-rail" aria-label="Profile and conversations">
        <div className="assistant-project-head"><FolderOpen size={20} /><div><h2>{profile?.name || "My profile"}</h2><small>Career project</small></div></div>
        {profile && <div id="assistant-sources" className="assistant-rail-section"><SourceLibrary compact profile={profile} notify={notify} onBuilt={async () => { await Promise.all([refresh(), reloadProfile(), loadBaseResume()]); }} /></div>}
        <div className="assistant-rail-section">
          <div className="assistant-rail-label">CONVERSATIONS</div>
          <ul className="assistant-conversation-list">
            {conversations.length ? conversations.map((conversation) => <li key={conversation.id}>
              <button type="button" className={conversation.current ? "current" : ""} onClick={() => void openChat(conversation.id)} title={conversation.title}>{conversation.title}</button>
            </li>) : <li className="assistant-output-muted">Your first conversation appears here.</li>}
          </ul>
        </div>
      </aside>
      <div className="chat-main assistant-center">
        <div className="chat-bar">
          <div className="chat-bar-title">
            <span className="chat-bar-icon" aria-hidden="true">
              <Sparkles size={17} />
            </span>
            <div className="chat-bar-text">
              <h1>Assistant</h1>
              <small title={heading}>
                {heading}
                {messages.length > 0 && <span className="chat-bar-count"> · {messages.length} {messages.length === 1 ? "exchange" : "exchanges"}</span>}
              </small>
            </div>
          </div>
          <div className="chat-bar-status">
            {engine && <EngineChip engine={engine} />}
            {runningNow > 0 && (
              <a className="chat-chip busy" href="#agents" title="Background runs the chat or a search started: open the Agents tab">
                <LoaderCircle className="spin" size={13} />
                {runningNow} running
              </a>
            )}
          </div>
          <div className="chat-bar-actions" role="toolbar" aria-label="Chat actions">
            <button type="button" className="bar-button" onClick={() => void newChat()} title={`New chat (${mac ? "⌘⇧O" : "Ctrl+Shift+O"})`}>
              <SquarePen size={15} />
              <span>New chat</span>
            </button>
            <button
              type="button"
              className={"bar-button" + (historyOpen ? " on" : "")}
              onClick={() => setHistoryOpen(true)}
              title="Earlier chats"
              aria-haspopup="dialog"
              aria-expanded={historyOpen}
            >
              <History size={15} />
              <span>History</span>
              {conversations.length > 1 && <em className="bar-count">{conversations.length}</em>}
            </button>
            <MoreMenu
              open={moreOpen}
              onOpen={setMoreOpen}
              empty={empty}
              searchLabel={`Search this chat (${mod}K)`}
              onSearch={() => toggleSearch(true)}
              onExport={exportChat}
              autoApply={!!overview?.auto_apply}
              onAutoApply={(enabled) => void toggleAutoApply(enabled)}
            />
          </div>
        </div>
        {overview && !overview.ai_configured && (
          <div className="chat-notice" role="status">
            <Badge tone="amber">No AI ready</Badge>
            <span>Shortcuts only. Sign in to Kimi Code, Codex or Claude Code, or add a key in Settings, and the chat can do the rest.</span>
          </div>
        )}
        {engine?.note && (
          <div className="chat-notice" role="status">
            <Badge tone="amber">AI</Badge>
            <span>{engine.note}</span>
          </div>
        )}
        {searchOpen && (
          <div className="chat-search" role="search">
            <Search size={15} aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              value={query}
              placeholder="Search this chat…"
              aria-label="Search this chat"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  toggleSearch(false);
                }
              }}
            />
            <small>{q ? `${shown.length} of ${messages.length}` : `${messages.length} ${messages.length === 1 ? "exchange" : "exchanges"}`}</small>
            <button type="button" className="icon-button" aria-label="Close search" onClick={() => toggleSearch(false)}>
              <X size={16} />
            </button>
          </div>
        )}
        {error && (
          <div className="callout warning" role="alert">
            {error}
          </div>
        )}
        <div className={"chat-thread" + (empty && overview ? " welcome" : "")} aria-live="polite">
          {!overview ? (
            <p className="muted chat-loading">
              <LoaderCircle className="spin" size={15} /> Loading the conversation…
            </p>
          ) : empty ? (
            <Welcome
              onPick={(starter) => (starter.send ? void send(starter.send) : boxRef.current?.focus())}
            />
          ) : q && shown.length === 0 ? (
            <p className="muted chat-empty-search">Nothing in this chat mentions “{q}”.</p>
          ) : (
            shown.map((m, i) => {
              const day = dayLabel(m.created_at);
              const before = i > 0 ? dayLabel(shown[i - 1].created_at) : "";
              return (
                <Fragment key={m.id}>
                  {day && day !== before && (
                    <div className="chat-day" role="separator">
                      <span>{day}</span>
                    </div>
                  )}
                  <Exchange
                    message={m}
                    names={names}
                    expanded={!!expanded[m.id]}
                    onExpand={() => setExpanded({ ...expanded, [m.id]: !expanded[m.id] })}
                    stepsOpen={stepsOpen[m.id]}
                    onSteps={(open) => setStepsOpen({ ...stepsOpen, [m.id]: open })}
                    onJob={onJob}
                    onSend={send}
                    onEdit={prompt}
                    now={now}
                    jobTier={m.data.job_id ? jobs.get(m.data.job_id)?.sponsor_tier : undefined}
                    jobs={jobs}
                    stopping={stopping && m.state === "processing"}
                    latest={i === shown.length - 1 && !echo}
                  />
                </Fragment>
              );
            })
          )}
          {echo && !q && (
            <>
              {(!latest || dayLabel(latest.created_at) !== "Today") && (
                <div className="chat-day" role="separator">
                  <span>Today</span>
                </div>
              )}
              <div className="chat-turn you echo">
                <div className="chat-turn-body">
                  <div className="chat-msg you">
                    <div className="chat-text">{echo.text.length > FOLD_AT ? echo.text.slice(0, FOLD_AT) + "…" : echo.text}</div>
                  </div>
                  <div className="chat-meta">
                    <span>Sending…</span>
                  </div>
                </div>
              </div>
              <div className="chat-turn assistant">
                <span className="chat-avatar" aria-hidden="true">
                  <Sparkles size={14} />
                </span>
                <div className="chat-turn-body">
                  <div className="chat-msg assistant processing" aria-busy="true">
                    <Typing label={stopping ? "Stopping…" : "Working on it"} />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
        <form
          className="chat-composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
        >
          {(unseen || away) && !empty && (
            <button type="button" className={"chat-jump" + (unseen ? " new" : "")} onClick={jumpToLatest} aria-label="Jump to the latest message">
              <ArrowDown size={15} />
              {unseen && <span>New activity</span>}
            </button>
          )}
          {working ? (
            <div className="chat-working" role="status">
              <span className="rail-live" aria-hidden="true" />
              <span className="chat-working-label">
                {stopping ? "Stopping after the current step" : current ? current.label : "Working on it"}
                {!stopping && current?.agent && current.agent !== "assistant" && (
                  <em className="chat-step-agent">{names.get(current.agent) || current.agent}</em>
                )}
              </span>
              {startedAt > 0 && <small>{elapsed((now - startedAt) / 1000)}</small>}
            </div>
          ) : (
            overview?.pending && (
              <div className="chat-pending-row">
                <div className="chat-pending">
                  <MessageSquare size={14} />
                  {PENDING_LABEL[overview.pending.kind] || "Waiting for your answer"}
                  <button type="button" className="text-button" onClick={() => send("cancel")}>
                    Cancel
                  </button>
                </div>
                {quick.length > 0 && (
                  <div className="chat-quick" aria-label="Quick answers">
                    {quick.map((qr) => (
                      <button key={qr.send} type="button" className={"chip" + (qr.send === "yes" ? " yes" : "")} onClick={() => send(qr.send)}>
                        {qr.send === "yes" && <CheckCircle2 size={13} />}
                        {qr.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
          )}
          <div className={"chat-input" + (working ? " working" : "")}>
            <textarea
              ref={boxRef}
              rows={1}
              placeholder={touch ? "Ask, or paste a job posting…" : "Ask anything, or paste a job posting with its link…"}
              value={draft}
              maxLength={120000}
              aria-label="Message the assistant"
              enterKeyHint="send"
              onChange={(e) => {
                setDraft(e.target.value);
                grow(e.target);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !touch && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send(draft);
                } else if (e.key === "Escape" && draft && !e.nativeEvent.isComposing) {
                  e.stopPropagation();
                  setDraft("");
                  grow(e.currentTarget);
                }
              }}
            />
            <div className="chat-input-buttons">
              <button
                type="button"
                className="chat-attach"
                onClick={() => fileRef.current?.click()}
                disabled={working || attaching}
                aria-label="Add a document about you"
                title="Add a document about you (Word, PDF or text): the assistant proposes what is new"
              >
                {attaching ? <LoaderCircle className="spin" size={16} /> : <Paperclip size={16} />}
              </button>
              <input
                ref={fileRef}
                type="file"
                hidden
                accept=".docx,.pdf,.txt,.md"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) void attach(file);
                }}
              />
              {working && (
                <button
                  type="button"
                  className="chat-stop"
                  onClick={() => void stop()}
                  disabled={stopping}
                  aria-label={stopping ? "Stopping" : "Stop the reply"}
                  title={stopping ? "Stopping after the current step" : "Stop the reply"}
                >
                  {stopping ? <LoaderCircle className="spin" size={16} /> : <Square size={14} fill="currentColor" />}
                </button>
              )}
              <button className="chat-send" disabled={sending || !draft.trim()} aria-label="Send" title={touch ? "Send" : "Send (Enter)"}>
                {sending ? <LoaderCircle className="spin" size={18} /> : <Send size={17} />}
              </button>
            </div>
          </div>
          {(!touch || overview?.auto_apply) && (
            <div className="chat-hint">
              {/* Keyboard hints are for keyboards; a phone gets only the auto-apply note. */}
              {!touch &&
                (draft.length > 1000 ? (
                  <small className="muted">{draft.length.toLocaleString()} characters · a whole posting is fine</small>
                ) : (
                  <small className="muted">
                    <kbd>Enter</kbd> sends · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line · <kbd>{mac ? "⌘⇧O" : "Ctrl+Shift+O"}</kbd> new chat · <kbd>{mod}K</kbd> search
                  </small>
                ))}
              {overview?.auto_apply && (
                <small className="chat-auto-on" title="Changes run without the yes/no question in this chat. Turn it off in the ⋯ menu.">
                  Auto-apply on
                </small>
              )}
            </div>
          )}
        </form>
      </div>
      <aside className="assistant-output-rail" aria-label="Outputs, sources and agent progress">
        <section>
          <h2>Outputs</h2>
          {outputEntries.length ? <ul className="assistant-output-list">
            {outputEntries.map((entry) => <li key={entry.path}><FileText size={15} /><a href={fileUrl(entry.path)} target="_blank" rel="noreferrer">{entry.label}</a></li>)}
          </ul> : <p className="assistant-output-muted">Resumes and reports will appear here.</p>}
        </section>
        <section>
          <h2>Sources</h2>
          <ul className="assistant-output-list">
            <li><FolderOpen size={15} /><a href="#assistant-sources">Profile documents and notes</a></li>
            <li><Globe2 size={15} /><span>Verified job and company research</span></li>
            {latest?.data.posting_url && safeUrl(latest.data.posting_url) !== "#" && <li><FileText size={15} /><a href={safeUrl(latest.data.posting_url)} target="_blank" rel="noreferrer">Current job posting</a></li>}
          </ul>
        </section>
        <section>
          <h2>Agent progress</h2>
          {latest?.steps.length ? latest.steps.slice(-6).map((step, index) => <div key={`${step.at}:${index}`} className={`assistant-output-progress ${step.state}`}>
            {step.state === "running" ? <LoaderCircle className="spin" size={14} /> : step.state === "done" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            <span>{step.label}{step.agent ? ` · ${names.get(step.agent) || step.agent}` : ""}</span>
          </div>) : <p className="assistant-output-muted">No agent is running in this conversation.</p>}
          {runningNow > 0 && <p className="assistant-output-progress"><Bot size={14} /> {runningNow} background run{runningNow === 1 ? "" : "s"} active</p>}
          <a className="source-text-button" href="#agents"><Workflow size={14} /> View all agents</a>
        </section>
      </aside>
      <HistoryDrawer
        conversations={conversations}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onOpen={(id) => void openChat(id)}
        onNew={() => void newChat()}
        onDelete={(c) => setConfirmDelete(c)}
        onClear={() => setConfirmClear(true)}
        working={working}
      />
      {confirmDelete && (
        <Modal title="Delete this chat?" onClose={() => setConfirmDelete(null)}>
          <p>
            <b>{confirmDelete.title}</b> — {confirmDelete.count} {confirmDelete.count === 1 ? "exchange" : "exchanges"}, last active{" "}
            {dayLabel(confirmDelete.updated_at).toLowerCase()} at {clock(confirmDelete.updated_at)}.
          </p>
          <p>
            Only the messages go. The jobs, resumes and profile changes this chat made stay in the workspace. This cannot be undone.
          </p>
          <div className="actions">
            <button type="button" className="danger" onClick={() => void deleteChat(confirmDelete)}>
              <Trash2 size={15} /> Delete chat
            </button>
            <button type="button" className="secondary" onClick={() => setConfirmDelete(null)}>
              Keep it
            </button>
          </div>
        </Modal>
      )}
      {confirmClear && (
        <Modal title="Clear all chat history?" onClose={() => setConfirmClear(false)}>
          <p>
            This deletes {conversations.length === 1 ? "the one chat" : `all ${conversations.length} chats`} —{" "}
            {conversations.reduce((n, c) => n + c.count, 0).toLocaleString()} exchanges in total — and opens a fresh one.
          </p>
          <p>
            Only the messages go. The jobs, resumes and profile changes those chats made stay in the workspace. This cannot be undone.
          </p>
          <div className="actions">
            <button type="button" className="danger" onClick={() => void clearHistory()}>
              <Trash2 size={15} /> Clear chat history
            </button>
            <button type="button" className="secondary" onClick={() => setConfirmClear(false)}>
              Keep everything
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** Three dots while a reply is being worked on, with what is happening right now. */
function Typing({ label }: { label?: string }) {
  return (
    <p className="chat-typing" role="status">
      <span className="chat-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {label && <span>{label}</span>}
    </p>
  );
}

/** Copies to the clipboard and says so for a moment. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={"chat-tool" + (done ? " done" : "")}
      title={done ? "Copied" : label}
      aria-label={done ? "Copied" : label}
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          window.setTimeout(() => setDone(false), 1600);
        }
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
      {done && <span>Copied</span>}
    </button>
  );
}

export function HistoryDrawer({
  conversations,
  open,
  onClose,
  onOpen,
  onNew,
  onDelete,
  onClear,
  working = false,
}: {
  conversations: AssistantConversation[];
  open: boolean;
  onClose: () => void;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (c: AssistantConversation) => void;
  /** Clear every chat at once; asks first. */
  onClear?: () => void;
  /** A reply in progress: nothing can be cleared until it ends. */
  working?: boolean;
}) {
  const groups = useMemo(() => {
    const out: { day: string; items: AssistantConversation[] }[] = [];
    for (const c of conversations) {
      const day = dayLabel(c.updated_at) || "Earlier";
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(c);
      else out.push({ day, items: [c] });
    }
    return out;
  }, [conversations]);
  return (
    <>
      {open && <div className="rail-backdrop history-backdrop" onClick={onClose} aria-hidden="true" />}
      <div className={"chat-history" + (open ? " open" : "")} role="dialog" aria-label="Earlier chats" aria-hidden={!open}>
        <div className="rail-head">
          <div>
            <div className="eyebrow">HISTORY</div>
            <h2>Your chats</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose} tabIndex={open ? 0 : -1}>
            <X size={20} />
          </button>
        </div>
        <button type="button" className="secondary history-new" onClick={onNew} tabIndex={open ? 0 : -1}>
          <SquarePen size={15} /> New chat
        </button>
        {conversations.length === 0 ? (
          <p className="muted rail-empty">Nothing yet. The first message starts a chat; every chat stays here until you delete it.</p>
        ) : (
          <div className="history-list">
            {groups.map((g) => (
              <section key={g.day}>
                <div className="rail-label">{g.day}</div>
                <ul>
                  {g.items.map((c) => (
                    <li key={c.id} className={"history-item" + (c.current ? " current" : "")}>
                      <button type="button" className="history-open" onClick={() => onOpen(c.id)} aria-current={c.current ? "true" : undefined} tabIndex={open ? 0 : -1}>
                        <b>{c.title}</b>
                        <small>
                          {c.busy ? "Working… · " : ""}
                          {c.count} {c.count === 1 ? "exchange" : "exchanges"} · {clock(c.updated_at)}
                          {c.current ? " · open now" : ""}
                        </small>
                      </button>
                      <button
                        type="button"
                        className="chat-tool history-delete"
                        aria-label={"Delete " + c.title}
                        title="Delete this chat"
                        onClick={() => onDelete(c)}
                        disabled={c.busy}
                        tabIndex={open ? 0 : -1}
                      >
                        <Trash2 size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
        <div className="history-foot">
          {onClear && conversations.length > 0 && (
            <button
              type="button"
              className="history-clear"
              onClick={onClear}
              disabled={working}
              title={working ? "Wait for the reply, or stop it, before clearing" : "Delete every chat"}
              tabIndex={open ? 0 : -1}
            >
              <Trash2 size={15} /> Clear chat history
            </button>
          )}
          <p className="muted history-note">Deleting a chat removes only its messages. Jobs, resumes and profile changes stay where they are.</p>
        </div>
      </div>
    </>
  );
}

/** The greeting and four starters, sitting just above the composer before the first message. */
export function Welcome({ onPick, now = new Date() }: { onPick: (starter: Starter) => void; now?: Date }) {
  const market = useMarket();
  return (
    <section className="chat-welcome" aria-label="Start a chat">
      <h2>{greeting(now)}. What would you like done?</h2>
      <p>Paste a posting for its {market.resumeShape} PDF, or ask in plain words. Nothing is ever sent for you.</p>
      <div className="chat-starters" role="list">
        {STARTERS.map((s) => {
          const Icon = s.icon;
          return (
            <button key={s.label} type="button" role="listitem" className="chat-starter" title={s.hint} onClick={() => onPick(s)}>
              <Icon size={15} />
              <span>{s.label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

/** Which AI answers: the Settings choice, as one small chip that opens Settings. */
export function EngineChip({ engine }: { engine: AssistantOverview["engine"] }) {
  const auto = engine.provider === "auto";
  const label = auto ? "Auto · free plans first" : engine.label;
  const switched = engine.last_fallback;
  const title =
    (engine.ready ? `The chat and its runs use ${label}.` : `${label} is not ready on this PC.`) +
    (switched ? ` Last switch: ${switched.from_provider} → ${switched.to_provider}.` : "") +
    " Change it in Settings.";
  return (
    <a className={"chat-chip engine" + (engine.ready ? "" : " warn")} href="#settings" title={title}>
      <Cpu size={13} />
      <span className="chat-chip-label">{engine.ready ? label : "No AI ready"}</span>
    </a>
  );
}

/** The chat's less-used actions: search, download, and the per-chat auto-apply switch. */
export function MoreMenu({
  open,
  onOpen,
  empty,
  searchLabel,
  onSearch,
  onExport,
  autoApply,
  onAutoApply,
}: {
  open: boolean;
  onOpen: (open: boolean) => void;
  empty: boolean;
  searchLabel: string;
  onSearch: () => void;
  onExport: () => void;
  autoApply: boolean;
  onAutoApply: (enabled: boolean) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      document.removeEventListener("keydown", key);
    };
  }, [open, onOpen]);
  const pick = (action: () => void) => () => {
    onOpen(false);
    action();
  };
  return (
    <div className="chat-more" ref={ref}>
      <button
        type="button"
        className={"bar-button icon" + (open ? " on" : "")}
        aria-label="More chat actions"
        aria-haspopup="menu"
        aria-expanded={open}
        title="More"
        onClick={() => onOpen(!open)}
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div className="chat-menu" role="menu" aria-label="More chat actions">
          <button type="button" role="menuitem" disabled={empty} onClick={pick(onSearch)} title={searchLabel}>
            <Search size={15} /> Search this chat
          </button>
          <button type="button" role="menuitem" disabled={empty} onClick={pick(onExport)}>
            <Download size={15} /> Download as a file
          </button>
          <div className="chat-menu-sep" role="separator" />
          <label className="chat-menu-toggle" role="menuitemcheckbox" aria-checked={autoApply}>
            <input type="checkbox" checked={autoApply} onChange={(e) => onAutoApply(e.target.checked)} />
            <span>
              <b>Auto-apply changes</b>
              <small>In this chat, changes like marking a job applied run without asking first. Each one is still listed.</small>
            </span>
          </label>
        </div>
      )}
    </div>
  );
}

export function Exchange({
  message: m,
  names,
  expanded,
  onExpand,
  stepsOpen,
  onSteps,
  onJob,
  onSend,
  onEdit,
  now,
  jobTier,
  jobs,
  stopping = false,
  latest = false,
}: {
  message: AssistantMessage;
  names: Map<string, string>;
  expanded: boolean;
  onExpand: () => void;
  stepsOpen?: boolean;
  onSteps: (open: boolean) => void;
  onJob: (id: string) => void;
  onSend: (text: string) => void;
  /** Puts the text back in the composer to change and send again. */
  onEdit?: (text: string) => void;
  now: number;
  jobTier?: string | null;
  jobs?: Map<string, Job>;
  stopping?: boolean;
  /** The newest exchange: only its open question gets answer buttons. */
  latest?: boolean;
}) {
  const long = m.message.length > FOLD_AT;
  const shown = long && !expanded ? m.message.slice(0, FOLD_AT) + "…" : m.message;
  const working = m.state === "processing";
  const failed = m.state === "failed";
  const stopped = failed && m.data.intent === "stopped";
  // A result card only where there is a document to act on; a question or a
  // queued run just names the job in its text.
  const hasResult = !!m.data.pdf || m.data.intent === "open" || m.data.intent === "resume_ready";
  const tier = (m.data.tier || jobTier || "C") as string;
  const stepName = (step: AssistantStep) => (step.agent && step.agent !== "assistant" ? names.get(step.agent) : undefined);
  const started = new Date(m.created_at).getTime();
  const finished = new Date(m.updated_at).getTime();
  const took = working ? (now - started) / 1000 : (finished - started) / 1000;
  // Steps stay open while they happen and after a failure; a finished answer folds them away.
  const showSteps = stepsOpen ?? (working || failed);
  const stepSummary = m.steps.map((s) => s.label).filter((label, i, all) => all.indexOf(label) === i).slice(0, 3).join(" · ");
  const currentStep = working ? m.steps[m.steps.length - 1] : undefined;
  const failedActions = (m.data.trace || []).filter((t) => t.error).length;
  // "Add these roles: …" is a template to finish, not a message: it goes to the composer.
  const useSuggestion = (text: string) => {
    const open = unfinished(text);
    if (open !== null && onEdit) onEdit(open);
    else onSend(text);
  };
  return (
    <>
      <div className="chat-turn you">
        <div className="chat-turn-body">
          <div className="chat-msg you">
            <div className="chat-text">{shown}</div>
            {long && (
              <button type="button" className="text-button" onClick={onExpand}>
                {expanded ? "Show less" : `Show all ${m.message.length.toLocaleString()} characters`}
              </button>
            )}
          </div>
          <div className="chat-meta">
            <span className="chat-tools">
              <CopyButton text={m.message} label="Copy your message" />
              {onEdit && (
                <button type="button" className="chat-tool" title="Edit and send again" aria-label="Edit and send again" onClick={() => onEdit(m.message)}>
                  <Pencil size={14} />
                </button>
              )}
            </span>
            <span className="chat-when">{clock(m.created_at)}</span>
          </div>
        </div>
      </div>
      <div className={"chat-turn assistant " + m.state}>
        <span className="chat-avatar" aria-hidden="true">
          <Sparkles size={14} />
        </span>
        <div className="chat-turn-body">
          <div className={"chat-msg assistant " + m.state + (stopped ? " stopped" : "")} aria-busy={working}>
            {m.steps.length > 0 && (
              <div className="chat-steps-wrap">
                <button
                  type="button"
                  className="chat-steps-toggle"
                  aria-expanded={showSteps}
                  onClick={() => onSteps(!showSteps)}
                >
                  {showSteps ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {working ? (
                    <span>
                      {m.steps.length} {m.steps.length === 1 ? "step" : "steps"} so far · {elapsed(took)}
                    </span>
                  ) : (
                    <span>
                      {m.steps.length} {m.steps.length === 1 ? "step" : "steps"} in {elapsed(took)}
                      {!showSteps && stepSummary && <em> — {stepSummary}{m.steps.length > 3 ? "…" : ""}</em>}
                    </span>
                  )}
                </button>
                {showSteps && (
                  <ol className="chat-steps">
                    {m.steps.map((step, n) => (
                      <li key={n} className={step.state}>
                        <span aria-hidden="true">
                          {step.state === "running" ? (
                            <LoaderCircle className="spin" size={15} />
                          ) : step.state === "failed" ? (
                            <XCircle size={15} />
                          ) : (
                            <CheckCircle2 size={15} />
                          )}
                        </span>
                        <div>
                          <b>
                            {step.label}
                            {stepName(step) && <em className="chat-step-agent">{stepName(step)}</em>}
                          </b>
                          {step.detail && <small>{step.detail}</small>}
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
            {working ? (
              <Typing label={stopping ? "Stopping after the current step…" : currentStep ? currentStep.label : "Working on it"} />
            ) : (
              <RichText text={m.response} />
            )}
            {failed && !working && (
              <div className="chat-retry">
                <button type="button" className="secondary" onClick={() => onSend(m.message)}>
                  <RotateCcw size={14} /> {stopped ? "Send it again" : "Try again"}
                </button>
              </div>
            )}
            {m.data.intent === "confirm_tool" && !!m.data.diff?.length && !working && (
              <div className="chat-diff" role="group" aria-label="What will change">
                <b>What changes if you say yes</b>
                {m.data.diff.map((row, i) => (
                  <div key={i} className="chat-diff-row">
                    <span className="chat-diff-field">{row.field}</span>
                    <span className="chat-diff-before">{row.before}</span>
                    <span aria-hidden="true">→</span>
                    <span className="chat-diff-after">{row.after}</span>
                  </div>
                ))}
              </div>
            )}
            {!!m.data.trace?.length && !working && (
              // A failed action is never hidden behind a fold: the list opens with it marked.
              <details className="chat-trace" open={failedActions > 0}>
                <summary>
                  What I did · {m.data.trace.length} {m.data.trace.length === 1 ? "action" : "actions"}
                  {failedActions > 0 && (
                    <em className="chat-trace-failed">
                      <XCircle size={12} /> {failedActions} failed
                    </em>
                  )}
                </summary>
                <ol>
                  {m.data.trace.map((t, i) => (
                    <li key={i} className={t.error ? "failed" : ""}>
                      <b>{t.tool.replaceAll("_", " ")}</b>
                      {t.auto_applied && <em className="chat-auto">auto-applied</em>}
                      {t.summary && <small>{t.summary}</small>}
                    </li>
                  ))}
                </ol>
              </details>
            )}
            {hasResult && !working && (
              <div className="chat-result">
                {m.data.preview_png && (
                  <a
                    className="chat-page"
                    href={fileUrl(m.data.preview_png)}
                    target="_blank"
                    rel="noreferrer"
                    title="Open the rendered page"
                  >
                    <img src={fileUrl(m.data.preview_png)} alt="Rendered resume page" />
                  </a>
                )}
                <div className="chat-result-body">
                  {m.data.company && (
                    <div className="chat-result-title">
                      <FileText size={16} />
                      <b>
                        {m.data.company} — {m.data.title}
                      </b>
                      {(m.data.tier || jobTier) && (
                        <TierBadge job={{ ...(m.data.job_id ? jobs?.get(m.data.job_id) : undefined), sponsor_tier: tier } as Job} />
                      )}
                      {m.data.revision != null && <small>v{m.data.revision}</small>}
                    </div>
                  )}
                  {(m.data.coverage != null || m.data.ats != null) && (
                    <div className="chat-scores">
                      {m.data.coverage != null && (
                        <span>
                          <b>{m.data.coverage}</b> JD coverage
                        </span>
                      )}
                      {m.data.ats != null && (
                        <span>
                          <b>{m.data.ats}</b> ATS readiness
                        </span>
                      )}
                    </div>
                  )}
                  <div className="chat-actions">
                    {m.data.pdf && (
                      <a
                        className="primary"
                        href={fileUrl(m.data.pdf)}
                        download={
                          (m.data.company || "resume").replace(/\W+/g, "-") + "-resume.pdf"
                        }
                      >
                        <Download size={15} /> Download PDF
                      </a>
                    )}
                    {m.data.job_id && (
                      <button type="button" className="secondary" onClick={() => onJob(m.data.job_id!)}>
                        <FileText size={15} /> Open in Resume Studio
                      </button>
                    )}
                    {m.data.posting_url && (
                      <a
                        className="secondary"
                        href={safeUrl(m.data.posting_url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <ExternalLink size={15} /> Open the posting
                      </a>
                    )}
                  </div>
                </div>
              </div>
            )}
            {!working && !!m.data.cards?.length && (
              <ul className="chat-cards" aria-label="Resumes in this answer">
                {m.data.cards.map((card) => (
                  <li key={card.job_id} className="chat-card-row">
                    <FileText size={15} aria-hidden="true" />
                    <span className="chat-card-name">
                      <b>{card.company}</b> — {card.title}
                      {card.revision != null && <small> · v{card.revision}</small>}
                    </span>
                    {card.tier && (
                      <TierBadge job={{ ...jobs?.get(card.job_id), sponsor_tier: card.tier } as Job} />
                    )}
                    {(card.coverage != null || card.ats != null) && (
                      <small className="chat-card-scores">
                        {card.coverage != null && <>{card.coverage} coverage</>}
                        {card.coverage != null && card.ats != null && " · "}
                        {card.ats != null && <>{card.ats} ATS</>}
                      </small>
                    )}
                    <span className="chat-card-actions">
                      {card.pdf && (
                        <a
                          className="secondary"
                          href={fileUrl(card.pdf)}
                          download={card.company.replace(/\W+/g, "-") + "-resume.pdf"}
                        >
                          <Download size={14} /> PDF
                        </a>
                      )}
                      <button type="button" className="secondary" onClick={() => onJob(card.job_id)}>
                        Open
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {!working && !!m.data.suggestions?.length && (
              latest && m.state === "needs_input" && m.data.intent === "agent_question" ? (
                // The agent's question, answered like a terminal prompt: click or press 1–9, or type below.
                <QuestionCard
                  question={{
                    id: m.id,
                    text: m.response,
                    options: m.data.suggestions.map((s) => ({ label: s, value: s })),
                    multi: false,
                    other: false,
                    skippable: false,
                  }}
                  busy={false}
                  onAnswer={(choices) => choices[0] && useSuggestion(choices[0])}
                />
              ) : (
                <div className="chat-suggestions">
                  {m.data.suggestions.map((s) => (
                    <button key={s} type="button" className="chip" onClick={() => useSuggestion(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )
            )}
          </div>
          {!working && (
            <div className="chat-meta">
              <span className="chat-when">
                {stopped ? "Stopped" : failed ? "Failed" : "Answered"} at {clock(m.updated_at)}
              </span>
              <span className="chat-tools">
                <CopyButton text={m.response} label="Copy the reply" />
                <button type="button" className="chat-tool" title="Ask again" aria-label="Ask again" onClick={() => onSend(m.message)}>
                  <RefreshCw size={14} />
                </button>
              </span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
