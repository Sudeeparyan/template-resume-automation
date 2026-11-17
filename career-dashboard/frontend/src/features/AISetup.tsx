import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, Copy, KeyRound, RefreshCw } from "lucide-react";
import { safeUrl, shellApi } from "../api";

/** backend/ai/settings.py machine_status(): what AI this computer can use. Never a key's value. */
export type AIStatus = {
  any_ready: boolean;
  clis: {
    id: string; label: string; kind: "cli"; ready: boolean; installed: boolean; signed_in: boolean; tested?: boolean;
    /** The exact install command for this computer ("" when there is none to paste). */
    install?: string;
    /** How to sign in once it is installed. */
    sign_in?: string;
    /** The desktop app is installed, but not the command-line tool the dashboard runs. */
    app_only?: boolean;
  }[];
  keys: { id: string; label: string; kind: "api_key"; ready: boolean; key_name: string; saved_in: string; get_key: string }[];
  note: string;
};

const CLI_HELP: Record<string, string> = {
  claude_code: "Install Claude Code and sign in with your Claude plan.",
  codex: "Install the ChatGPT desktop app (it includes Codex) and sign in.",
  kimi_cli: "Install Kimi Code and sign in with your Kimi membership.",
  antigravity: "Install the Antigravity command-line tool (agy) and sign in with your Google account.",
};
// How often the page looks again while no AI is ready, so signing in is enough to continue.
const RECHECK_MS = 15000;

/** Before the first build: make sure this computer has an AI that can read the documents. */
export default function AISetup({ who, onReady }: { who: string; onReady?: () => void }) {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [connected, setConnected] = useState<string | null>(null);
  const missing = useRef(false);

  const take = useCallback((next: AIStatus) => {
    setStatus(next);
    if (next.any_ready) {
      // Say which AI was found when it arrived while this page was open; a page that opens ready stays quiet.
      if (missing.current) {
        const found = [...next.clis.filter((cli) => cli.ready && cli.signed_in), ...next.keys.filter((key) => key.ready)];
        setConnected(found.map((row) => row.label).join(", ") || "your AI");
      }
      missing.current = false;
      onReady?.();
    } else {
      missing.current = true;
    }
  }, [onReady]);

  const load = useCallback(async () => {
    try {
      take(await shellApi<AIStatus>("/ai/status"));
    } catch {
      setStatus(null);
    }
  }, [take]);
  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (status?.any_ready) return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, RECHECK_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [status?.any_ready, load]);

  async function save(provider: string, value: string) {
    setBusy(true);
    setMessage(null);
    try {
      const result = await shellApi<{ ok: boolean; detail: string; status: AIStatus }>(`/ai/keys/${provider}`, "PUT", { value });
      setMessage({ text: result.detail, ok: result.ok });
      take(result.status);
      return true;
    } catch (e) {
      setMessage({ text: (e as Error).message, ok: false });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (connected) {
    return <p className="card spaced ai-setup small success" role="status"><CheckCircle2 size={14} /> Connected: {connected}. You can build {who}’s profile now.</p>;
  }
  if (!status || status.any_ready) return null;
  return <AISetupView who={who} status={status} busy={busy} message={message} onCheck={() => void load()} onSave={save} />;
}

/** A hint such as "Run `claude` once…", with each `command` shown as code. */
function Hint({ text }: { text: string }) {
  return <>{text.split("`").map((part, index) => index % 2 ? <code key={index}>{part}</code> : part)}</>;
}

function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="ai-setup-command">
      <code>{text}</code>
      <button type="button" className="text-button" aria-label="Copy command"
        onClick={() => { void navigator.clipboard?.writeText(text).then(() => setCopied(true), () => setCopied(false)); }}>
        <Copy size={13} /> {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

/** The step itself, drawn from a status (tests render it without a server). */
export function AISetupView({ who, status, busy = false, message = null, onCheck, onSave }: {
  who: string;
  status: AIStatus;
  busy?: boolean;
  message?: { text: string; ok: boolean } | null;
  onCheck?: () => void;
  onSave?: (provider: string, value: string) => Promise<boolean>;
}) {
  const [provider, setProvider] = useState(status.keys[0]?.id || "");
  const [value, setValue] = useState("");
  const chosen = status.keys.find((key) => key.id === provider);
  return (
    <section className="card spaced ai-setup" aria-label="Set up an AI">
      <h2><CircleAlert size={18} /> First, an AI to read {who}’s documents</h2>
      <p>{status.note || "Use an AI app you already have. No API key is needed."}</p>
      <h3>Sign in to an AI app on this computer</h3>
      <p className="small muted">Easiest: Claude Code or Antigravity. Open a terminal (Windows: PowerShell; macOS: Terminal), paste one install command, then sign in once. This page connects by itself when it is done.</p>
      <ul className="ai-setup-list">
        {status.clis.map((cli) => <li key={cli.id}>
          <strong>{cli.label}</strong> — {cli.installed ? (cli.signed_in ? "installed and signed in" : "installed; sign in to it") : "not installed"}
          {cli.tested && <small className="muted"> · answered a real test call</small>}
          {cli.installed && !cli.signed_in && cli.sign_in && <small className="muted"> · <Hint text={cli.sign_in} /></small>}
          {!cli.installed && cli.app_only && <small className="muted"> · The {cli.label} app is installed, but the dashboard needs its command-line tool.</small>}
          {!cli.installed && (cli.install
            ? <div className="ai-setup-steps">
                <small className="muted">Install:</small> <Command text={cli.install} />
                {cli.sign_in && <small className="muted ai-setup-then">Then: <Hint text={cli.sign_in} /></small>}
              </div>
            : <small className="muted"> · {CLI_HELP[cli.id] || "Install it and sign in."}</small>)}
        </li>)}
      </ul>
      <button type="button" className="text-button" onClick={onCheck}><RefreshCw size={14} /> Check again</button>
      <details className="advanced spaced">
        <summary><b>Advanced: use an API key instead</b><small>Optional. Paid API fallback is off by default for new profiles.</small></summary>
        <div className="ai-setup-key">
          <label>Provider
            <select value={provider} onChange={(event) => setProvider(event.target.value)}>
              {status.keys.map((key) => <option key={key.id} value={key.id}>{key.label}</option>)}
            </select>
          </label>
          <label>API key
            <input type="password" autoComplete="off" spellCheck={false} value={value} placeholder={chosen ? chosen.key_name : "API key"}
              onChange={(event) => setValue(event.target.value)} />
          </label>
          <button type="button" disabled={busy || !value.trim() || !onSave}
            onClick={() => void onSave?.(provider, value.trim()).then((saved) => { if (saved) setValue(""); })}>
            <KeyRound size={14} /> {busy ? "Testing…" : "Save and test"}
          </button>
        </div>
        {chosen?.get_key && <p className="small">Get a {chosen.label} key: <a href={safeUrl(chosen.get_key)} target="_blank" rel="noreferrer">{chosen.get_key} ↗</a></p>}
        <p className="small muted">The key is saved in career-dashboard/.env on this computer only. It is never shown again or included in a shared copy of the app.</p>
      </details>
      {message && <p className={message.ok ? "small success" : "small error"}>{message.ok ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />} {message.text}</p>}
    </section>
  );
}
