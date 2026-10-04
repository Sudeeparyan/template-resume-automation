import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, KeyRound, RefreshCw } from "lucide-react";
import { safeUrl, shellApi } from "../api";

/** backend/ai/settings.py machine_status(): what AI this computer can use. Never a key's value. */
export type AIStatus = {
  any_ready: boolean;
  clis: { id: string; label: string; kind: "cli"; ready: boolean; installed: boolean; signed_in: boolean }[];
  keys: { id: string; label: string; kind: "api_key"; ready: boolean; key_name: string; saved_in: string; get_key: string }[];
  note: string;
};

const CLI_HELP: Record<string, string> = {
  claude_code: "Install Claude Code and sign in with your Claude plan.",
  codex: "Install the ChatGPT desktop app (it includes Codex) and sign in.",
  kimi_cli: "Install Kimi Code and sign in with your Kimi membership.",
};

/** Before the first build: make sure this computer has an AI that can read the documents. */
export default function AISetup({ who, onReady }: { who: string; onReady?: () => void }) {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await shellApi<AIStatus>("/ai/status");
      setStatus(next);
      if (next.any_ready) onReady?.();
    } catch {
      setStatus(null);
    }
  }, [onReady]);
  useEffect(() => { void load(); }, [load]);

  async function save(provider: string, value: string) {
    setBusy(true);
    setMessage(null);
    try {
      const result = await shellApi<{ ok: boolean; detail: string; status: AIStatus }>(`/ai/keys/${provider}`, "PUT", { value });
      setMessage({ text: result.detail, ok: result.ok });
      setStatus(result.status);
      if (result.status.any_ready) onReady?.();
      return true;
    } catch (e) {
      setMessage({ text: (e as Error).message, ok: false });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!status || status.any_ready) return null;
  return <AISetupView who={who} status={status} busy={busy} message={message} onCheck={() => void load()} onSave={save} />;
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
      <p>No AI is set up on this computer yet. Use an AI app you already have, or paste an API key. {status.note}</p>
      <h3>Sign in to an AI app on this computer</h3>
      <ul className="ai-setup-list">
        {status.clis.map((cli) => <li key={cli.id}>
          <strong>{cli.label}</strong> — {cli.installed ? (cli.signed_in ? "installed and signed in" : "installed; sign in to it") : "not installed"}
          {!cli.installed && <small className="muted"> · {CLI_HELP[cli.id] || "Install it and sign in."}</small>}
        </li>)}
      </ul>
      <button type="button" className="text-button" onClick={onCheck}><RefreshCw size={14} /> Check again</button>
      <h3>Or add an API key</h3>
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
      {message && <p className={message.ok ? "small success" : "small error"}>{message.ok ? <CheckCircle2 size={14} /> : <CircleAlert size={14} />} {message.text}</p>}
    </section>
  );
}
