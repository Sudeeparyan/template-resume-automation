import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  Gauge,
  Globe,
  KeyRound,
  Mail,
  RefreshCw,
  Route,
  Trash2,
} from "lucide-react";
import { api } from "../api";
import { Badge, Field } from "../components/UI";
import { PROVIDER_LABEL } from "./Agents";
import AutoRoute from "./AutoRoute";
import { ProfileSettings } from "../profiles";
import type { RouteEndpoint, RoutePolicy } from "../types";

type Provider = {
  id: string;
  label: string;
  kind: "api" | "local" | "auto";
  configured: boolean;
  models: string[];
  agent_models?: string[];
  capabilities?: string[];
  defaults: Record<string, string>;
  key_name?: string;
  key_source?: string | null;
  error?: string | null;
  note?: string;
  route?: RoutePolicy;
  endpoints?: RouteEndpoint[];
};
type Choice = { provider: string; model: string };
type RouteRow = {
  action: string;
  label: string;
  provider: string | null;
  model: string | null;
  moved: boolean;
  override?: boolean;
  error: string | null;
};
type SettingsData = {
  main: Choice;
  routes: RouteRow[];
  providers: Provider[];
  tiers: Record<string, string>;
  preferences: { tiers: Record<string, Choice>; fallback?: Choice | null };
};
// daily_call_limit is paid AI calls a day; free plan calls never count against it.
type Budget = {
  daily_call_limit: number;
  calls_today: number;
  remaining_calls: number;
  paid_calls_today?: number;
  free_calls_today?: number;
  note?: string;
};
type MailConnection = {
  connector_id?: string;
  expected_email?: string;
  email?: string;
  connected?: boolean;
  status?: string;
  last_synced_at?: string;
  last_error?: string;
};
const AUTO = "auto";
const AUTO_LABEL = "Auto · free plans first";
const choiceLabel = (c: Choice) =>
  c.provider === AUTO ? AUTO_LABEL : `${PROVIDER_LABEL[c.provider] || c.provider} · ${c.model}`;

const KEY_PAGE: Record<string, string> = {
  openrouter: "https://openrouter.ai/keys",
  openai: "https://platform.openai.com/api-keys",
  anthropic: "https://console.anthropic.com/settings/keys",
  gemini: "https://aistudio.google.com/apikey",
  kimi: "https://platform.moonshot.ai/console/api-keys",
  azure_openai: "https://ai.azure.com",
};
const TIER_LABEL: Record<string, string> = {
  strong: "Writing model",
  cheap: "Reading model",
};
const BLURB: Record<string, string> = {
  auto: "Kimi K3 → Codex → Claude, then Azure only if all three are out",
  claude_code: "Your Claude plan's usage limits",
  codex: "Your ChatGPT plan · only one with Gmail",
  kimi_cli: "Your Kimi membership's usage limits",
  openai: "GPT models · has web search",
  anthropic: "Claude models, billed per call",
  openrouter: "Hundreds of models, one key",
  gemini: "Google's models · free tier",
  kimi: "Moonshot's Kimi models",
  azure_openai: "Your Azure deployment · needs endpoint + deployment in .env",
};
// Order on the page: Auto, then the no-key options, then the API keys.
const ORDER = ["auto", "kimi_cli", "codex", "claude_code", "openai", "azure_openai", "anthropic", "openrouter", "gemini", "kimi"];

export default function Settings({
  notify,
}: {
  notify: (text: string, error?: boolean) => void;
}) {
  const [data, setData] = useState<SettingsData>();
  const [main, setMain] = useState<Choice>();
  const [busy, setBusy] = useState("");
  const [tested, setTested] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [keyResult, setKeyResult] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const [tiers, setTiers] = useState<Record<string, Choice>>({});
  const [fallback, setFallback] = useState<Choice | null>(null);
  const [budget, setBudget] = useState<Budget>();
  const [limit, setLimit] = useState<number>();
  const [mailConnection, setMailConnection] = useState<MailConnection>({});
  const [mailDraft, setMailDraft] = useState({ connector_id: "", expected_email: "" });
  const [error, setError] = useState("");

  const load = useCallback(async (refresh = false) => {
    try {
      const [next, control, connection] = await Promise.all([
        api<SettingsData>("/v2/ai/settings" + (refresh ? "?refresh=true" : "")),
        api<{ budget: Budget }>("/v2/agent-control"),
        api<MailConnection>("/v2/mail/connection"),
      ]);
      setData(next);
      setMain(next.main);
      setTiers(next.preferences.tiers);
      setFallback(next.preferences.fallback || null);
      setBudget(control.budget);
      setLimit(control.budget.daily_call_limit);
      setMailConnection(connection);
      setMailDraft({
        connector_id: connection.connector_id || "",
        expected_email: connection.expected_email || "",
      });
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <div className="callout warning">{error}</div>;
  if (!data || !main) return <p>Loading your AI settings…</p>;

  const byId = (id: string) => data.providers.find((p) => p.id === id);
  const providers = ORDER.map(byId).filter(Boolean) as Provider[];
  const chosen = byId(main.provider);
  const changed =
    main.provider !== data.main.provider || main.model !== data.main.model;
  const agentModels = chosen?.agent_models || [];

  async function act(name: string, fn: () => Promise<void>) {
    setBusy(name);
    try {
      await fn();
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy("");
    }
  }

  function pick(p: Provider) {
    if (!p.configured) {
      document.getElementById("key-" + p.id)?.focus();
      notify(
        p.kind === "api"
          ? `Add your ${p.label} API key below first.`
          : p.kind === "auto"
          ? "Auto needs at least one AI set up: sign in to Kimi Code, Codex or Claude Code, or add an Azure key."
          : `${p.label} is not installed on this machine.`,
        true,
      );
      return;
    }
    const models = p.agent_models || [];
    const preferred = p.defaults?.strong;
    setMain({
      provider: p.id,
      model: models.includes(preferred) ? preferred : models[0] || "",
    });
    setTested({});
  }

  const saveMain = () =>
    act("main", async () => {
      const next = await api<SettingsData>("/v2/ai/main", "PUT", main);
      setData(next);
      setMain(next.main);
      setTiers(next.preferences.tiers);
      notify(
        main.provider === AUTO
          ? "All agents now use Auto: your Kimi, Codex and Claude plans first, Azure only as a last resort."
          : `All agents now run on ${choiceLabel(main)}.`,
      );
    });

  const saveRoute = (policy: RoutePolicy) =>
    act("route", async () => {
      const next = await api<SettingsData>("/v2/ai/route", "PUT", policy);
      setData(next);
      notify("Route saved. Auto tries the plans in this order.");
    });

  const wake = (provider: string) =>
    act("wake-" + provider, async () => {
      const next = await api<SettingsData>("/v2/ai/route/rest/" + provider, "DELETE");
      setData(next);
      notify(`${PROVIDER_LABEL[provider] || provider} will be tried again on the next step.`);
    });

  const test = (choice: Choice, slot: string) =>
    act("test-" + slot, async () => {
      const result = await api<{ ok: boolean; detail: string }>(
        "/v2/ai/settings/test",
        "POST",
        choice,
      );
      setTested((t) => ({ ...t, [slot]: result }));
    });

  const saveKey = (p: Provider) =>
    act("key-" + p.id, async () => {
      const result = await api<{ ok: boolean; detail: string }>(
        "/v2/ai/keys/" + p.id,
        "PUT",
        { value: keys[p.id] || "" },
      );
      setKeys((k) => ({ ...k, [p.id]: "" }));
      setKeyResult((r) => ({ ...r, [p.id]: result }));
      notify(result.detail, !result.ok);
      await load();
    });

  const removeKey = (p: Provider) =>
    act("remove-" + p.id, async () => {
      if (!window.confirm(`Remove the ${p.label} key saved in the app?`)) return;
      const result = await api<{ detail: string }>("/v2/ai/keys/" + p.id, "DELETE");
      setKeyResult((r) => ({ ...r, [p.id]: { ok: true, detail: result.detail } }));
      notify(result.detail);
      await load();
    });

  const saveLimit = () =>
    act("limit", async () => {
      const next = await api<Budget>("/v2/agent-control/budget", "PUT", {
        daily_call_limit: limit ?? budget?.daily_call_limit,
      });
      setBudget(next);
      notify(next.daily_call_limit ? `Paid AI calls a day: ${next.daily_call_limit}.` : "Paid AI is off: only your free plans will run.");
    });

  const saveMailConnection = () =>
    act("mail", async () => {
      const connection = await api<MailConnection>("/v2/mail/connection", "PUT", mailDraft);
      setMailConnection(connection);
      setMailDraft({ connector_id: connection.connector_id || "", expected_email: connection.expected_email || "" });
      notify("Mail settings saved for this profile. The mailbox will be verified before its first sync.");
    });

  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">WHICH AI DOES THE WORK</div>
          <h1>Settings</h1>
          <p>
            Pick the AI that runs your agents and manage your API keys. Switching
            takes one click; nothing else needs to change.
          </p>
        </div>
        <button
          className="secondary"
          disabled={busy === "refresh"}
          onClick={() =>
            act("refresh", async () => {
              await load(true);
              notify("Provider status and model lists refreshed.");
            })
          }
        >
          <RefreshCw size={16} className={busy === "refresh" ? "spin" : ""} /> Refresh
        </button>
      </div>

      <section className="card">
        <div className="section-title">
          <h2>AI provider</h2>
          <Badge tone="green">In use: {choiceLabel(data.main)}</Badge>
        </div>
        <p className="muted">
          Every agent uses this choice. Kimi Code, Codex and Claude Code use your
          plans on this PC and need no key; the others need an API key (add it
          below). Auto uses the free plans first and moves to the next one when a
          plan reaches its usage limit.
        </p>
        {(
          [
            ["auto", "Recommended: uses your free plans first"],
            ["local", "No key needed — uses your subscription on this machine"],
            ["api", "Pay-as-you-go with an API key"],
          ] as const
        ).map(([kind, heading]) => (
        <div key={kind}>
        <h3 className="provider-group">{heading}</h3>
        <div className="provider-grid" role="radiogroup" aria-label={heading}>
          {providers.filter((p) => p.kind === kind).map((p) => {
            const selected = main.provider === p.id;
            const caps = p.capabilities || [];
            return (
              <button
                key={p.id}
                role="radio"
                aria-checked={selected}
                className={"provider-tile" + (selected ? " selected" : "") + (p.configured ? "" : " unavailable")}
                onClick={() => pick(p)}
              >
                <span className="provider-tile-head">
                  <b>{PROVIDER_LABEL[p.id] || p.label}</b>
                  {selected && <CheckCircle2 size={18} />}
                </span>
                <small>{BLURB[p.id]}</small>
                <span className="provider-tile-tags">
                  <Badge tone={p.configured ? "green" : "amber"}>
                    {p.kind === "auto"
                      ? p.configured ? "Ready" : "No plan set up"
                      : p.kind === "local"
                      ? p.configured ? "Ready" : "Not installed"
                      : p.configured ? "Key saved" : "Needs key"}
                  </Badge>
                  {caps.includes("web") && (
                    <Badge><Globe size={11} /> Web search</Badge>
                  )}
                  {caps.includes("apps") && (
                    <Badge><Mail size={11} /> Gmail</Badge>
                  )}
                </span>
              </button>
            );
          })}
        </div>
        </div>
        ))}

        <div className="main-choice">
          {main.provider === AUTO ? (
            <p className="small muted auto-choice-note">
              Auto picks each plan's model: the best one for writing (resumes,
              research, study plans) and a lighter one for reading and sorting,
              so each plan's usage limit lasts longer.
            </p>
          ) : (
          <Field label={`Model for ${PROVIDER_LABEL[main.provider] || "this provider"}`}>
            {agentModels.length > 25 ? (
              <>
                <input
                  list="agent-models"
                  value={main.model}
                  onChange={(e) => setMain({ ...main, model: e.target.value })}
                  placeholder="Type to search models"
                />
                <datalist id="agent-models">
                  {agentModels.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </>
            ) : (
              <select
                value={main.model}
                onChange={(e) => setMain({ ...main, model: e.target.value })}
              >
                {agentModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}
          </Field>
          )}
          <div className="actions">
            <button
              className="secondary"
              disabled={busy === "test-main" || !main.model}
              onClick={() => test(main, "main")}
            >
              {busy === "test-main" ? "Testing…" : "Test connection"}
            </button>
            <button
              className="primary"
              disabled={busy === "main" || !changed || !main.model}
              onClick={saveMain}
            >
              {busy === "main" ? "Saving…" : changed ? "Use for all agents" : "In use"}
            </button>
          </div>
        </div>
        {tested.main && (
          <div className={tested.main.ok ? "callout" : "callout warning"}>
            {tested.main.ok ? "Working. " : "Not working. "}
            {tested.main.detail}
          </div>
        )}
        {chosen?.note && <p className="small muted">{chosen.note}</p>}
        {main.provider === AUTO && chosen?.endpoints && chosen.route && (
          <AutoRoute
            endpoints={chosen.endpoints}
            route={chosen.route}
            busy={busy}
            onSave={saveRoute}
            onWake={wake}
          />
        )}

        <div className="route-list">
          <h3>
            <Route size={16} /> Where each kind of work runs
          </h3>
          {data.routes.map((r) => (
            <div key={r.action} className="route-row">
              <span>{r.label}</span>
              {r.provider ? (
                <b>
                  {r.provider === AUTO ? AUTO_LABEL : PROVIDER_LABEL[r.provider] || r.provider}
                  {r.model && !["codex-runtime", "kimi-runtime", AUTO].includes(r.model) ? ` · ${r.model}` : ""}
                </b>
              ) : (
                <Badge tone="red">Not available</Badge>
              )}
              <small>
                {r.error
                  ? r.error
                  : r.override
                    ? "Chosen for this task in Resume Studio · picking a provider above resets it"
                    : r.moved
                    ? r.action === "email"
                      ? "Needs Gmail access, which only Codex has"
                      : "Needs web search, so it moves to a provider that has it"
                    : "Your main choice"}
              </small>
            </div>
          ))}
        </div>
      </section>

      <section className="card spaced">
        <div className="section-title">
          <h2>
            <KeyRound size={18} /> API keys
          </h2>
        </div>
        <p className="muted">
          Paste a key and press Save. It is checked straight away with a free
          call, kept only on this machine (in <code>career-dashboard/.env</code>),
          and never shown on this page again.
        </p>
        <div className="key-list">
          {providers
            .filter((p) => p.kind === "api")
            .map((p) => {
              const result = keyResult[p.id];
              const fromApp = p.key_source === "saved in the app";
              return (
                <div key={p.id} className="key-row">
                  <div className="key-name">
                    <b>{PROVIDER_LABEL[p.id] || p.label}</b>
                    <Badge tone={p.configured ? (p.error ? "amber" : "green") : "neutral"}>
                      {p.configured
                        ? p.error
                          ? "Key has a problem"
                          : `Key set · ${p.key_source}`
                        : "No key"}
                    </Badge>
                    <a href={KEY_PAGE[p.id]} target="_blank" rel="noreferrer" className="small">
                      Get a key <ExternalLink size={11} />
                    </a>
                  </div>
                  <form
                    className="key-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveKey(p);
                    }}
                  >
                    <input
                      id={"key-" + p.id}
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      aria-label={`${p.label} API key`}
                      placeholder={p.configured ? "Paste a new key to replace it" : `Paste your ${p.label} key (${p.key_name})`}
                      value={keys[p.id] || ""}
                      onChange={(e) => setKeys((k) => ({ ...k, [p.id]: e.target.value }))}
                    />
                    <button
                      className="primary"
                      disabled={!keys[p.id]?.trim() || busy === "key-" + p.id}
                    >
                      {busy === "key-" + p.id ? "Checking…" : "Save & check"}
                    </button>
                    {fromApp && (
                      <button
                        type="button"
                        className="icon-button danger-icon"
                        title="Remove this key"
                        aria-label={`Remove the ${p.label} key`}
                        onClick={() => void removeKey(p)}
                      >
                        <Trash2 size={17} />
                      </button>
                    )}
                  </form>
                  {(result || p.error) && (
                    <small className={result?.ok ? "tone-done" : "tone-warn"}>
                      {result?.detail || p.error}
                    </small>
                  )}
                </div>
              );
            })}
        </div>
      </section>

      {budget && (
        <section className="card spaced">
          <div className="section-title">
            <h2>
              <Gauge size={18} /> Paid AI calls a day
            </h2>
            <Badge tone={budget.remaining_calls ? "green" : "amber"}>
              {budget.paid_calls_today ?? 0} of {budget.daily_call_limit} paid calls used today
            </Badge>
          </div>
          <p className="muted">
            A cap on calls that cost money: Azure and any API key. Kimi Code,
            Codex and Claude Code never count here: each plan has its own usage
            limit, and Auto moves to the next plan when one is used up
            {budget.free_calls_today ? ` (${budget.free_calls_today} calls on your plans today)` : ""}.
            When this cap is reached, Auto keeps going on your free plans and
            skips Azure until tomorrow. Set 0 to never pay.
          </p>
          <form
            className="actions"
            onSubmit={(e) => {
              e.preventDefault();
              void saveLimit();
            }}
          >
            <input
              className="limit-input"
              type="number"
              min={0}
              max={200}
              aria-label="Maximum paid AI calls per day"
              value={limit ?? budget.daily_call_limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            />
            <button className="secondary" disabled={busy === "limit"}>
              Save limit
            </button>
          </form>
        </section>
      )}

      <section className="card spaced">
        <div className="section-title">
          <h2>Backup provider</h2>
          {fallback ? (
            <Badge tone="green">
              {PROVIDER_LABEL[fallback.provider] || fallback.provider} · {fallback.model}
            </Badge>
          ) : (
            <Badge>None</Badge>
          )}
        </div>
        <p className="muted">
          If the main provider fails a call (out of credits, rate limited,
          unreachable), the call is retried once on this backup and the switch
          is shown in the activity log.
          {data.main.provider === AUTO &&
            " Auto does not need one: it already moves along its whole route. The backup is used only when you pick a single AI above."}
        </p>
        <div className="tier-row">
          <select
            aria-label="Backup provider"
            value={fallback?.provider || ""}
            onChange={(e) => {
              const p = byId(e.target.value);
              setFallback(
                p
                  ? { provider: p.id, model: p.defaults?.strong || p.models?.[0] || "" }
                  : null,
              );
            }}
          >
            <option value="">No backup</option>
            {providers
              .filter((p) => p.configured && p.kind !== "auto")
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
          </select>
          {fallback && (
            <select
              aria-label="Backup model"
              value={fallback.model}
              onChange={(e) => setFallback({ ...fallback, model: e.target.value })}
            >
              {!((byId(fallback.provider)?.models) || []).includes(fallback.model) && (
                <option value={fallback.model}>{fallback.model}</option>
              )}
              {(byId(fallback.provider)?.models || []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
          <button
            className="secondary"
            disabled={busy === "fallback"}
            onClick={() =>
              act("fallback", async () => {
                const next = await api<SettingsData>("/v2/ai/fallback", "PUT", {
                  provider: fallback?.provider || "",
                  model: fallback?.model || "",
                });
                setData(next);
                notify(
                  fallback
                    ? `Backup set: if a call fails it retries on ${PROVIDER_LABEL[fallback.provider] || fallback.provider}.`
                    : "Backup cleared. A failed call now just reports the error.",
                );
              })
            }
          >
            {busy === "fallback" ? "Saving…" : "Save backup"}
          </button>
        </div>
      </section>

      <details className="card spaced advanced">
        <summary>
          <b>Advanced: separate models for writing and reading</b>
          <small>Used by resume chat and profile chat. Choosing a provider above sets both.</small>
        </summary>
        {Object.keys(TIER_LABEL).map((tier) => {
          const choice = tiers[tier] || { provider: main.provider, model: "" };
          const provider = byId(choice.provider);
          return (
            <div className="tier-row" key={tier}>
              <div>
                <b>{TIER_LABEL[tier]}</b>
                <p className="small muted">{data.tiers[tier]}</p>
              </div>
              <select
                aria-label={`${TIER_LABEL[tier]} provider`}
                value={choice.provider}
                onChange={(e) => {
                  const p = byId(e.target.value);
                  setTiers((t) => ({
                    ...t,
                    [tier]: { provider: e.target.value, model: p?.defaults?.[tier] ?? p?.models?.[0] ?? "" },
                  }));
                }}
              >
                {providers
                  .filter((p) => p.id !== "codex")
                  .map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.configured}>
                      {p.label}
                      {p.configured ? "" : " — not ready"}
                    </option>
                  ))}
              </select>
              <select
                aria-label={`${TIER_LABEL[tier]} model`}
                value={choice.model}
                onChange={(e) => setTiers((t) => ({ ...t, [tier]: { ...choice, model: e.target.value } }))}
              >
                {provider && !provider.models.includes(choice.model) && (
                  <option value={choice.model}>{choice.model}</option>
                )}
                {(provider?.models || []).map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <button
                className="secondary"
                disabled={busy === "test-" + tier}
                onClick={() => test(choice, tier)}
              >
                {busy === "test-" + tier ? "Testing…" : "Test"}
              </button>
              {tested[tier] && (
                <small className={tested[tier].ok ? "tone-done" : "tone-warn"}>
                  {tested[tier].detail}
                </small>
              )}
            </div>
          );
        })}
        <div className="actions">
          <button
            className="primary"
            disabled={busy === "tiers"}
            onClick={() =>
              act("tiers", async () => {
                await api("/v2/ai/settings", "PUT", { tiers });
                notify("Writing and reading models saved.");
                await load();
              })
            }
          >
            {busy === "tiers" ? "Saving…" : "Save these models"}
          </button>
        </div>
      </details>
      <section className="card spaced">
        <div className="section-title">
          <h2><Mail size={18} /> Optional Gmail connection</h2>
          <Badge tone={mailConnection.connected ? "green" : mailConnection.connector_id ? "amber" : "neutral"}>
            {mailConnection.connected ? "Mailbox verified" : mailConnection.connector_id ? "Ready to verify" : "Not connected"}
          </Badge>
        </div>
        <p className="muted">
          This profile reads job-related mail only after you connect Gmail to Codex.
          Enter that connection's ID and the exact mailbox address. The app checks
          the connected account before reading any messages. Each profile has its own settings.
        </p>
        <form onSubmit={(e) => { e.preventDefault(); void saveMailConnection(); }}>
          <Field label="Codex Gmail connector ID">
            <input
              value={mailDraft.connector_id}
              onChange={(e) => setMailDraft({ ...mailDraft, connector_id: e.target.value })}
              placeholder="Connector ID from Codex"
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          <Field label="Expected Gmail address">
            <input
              type="email"
              value={mailDraft.expected_email}
              onChange={(e) => setMailDraft({ ...mailDraft, expected_email: e.target.value })}
              placeholder="you@example.com"
              autoComplete="email"
            />
          </Field>
          <div className="actions">
            <button className="primary" disabled={busy === "mail" || !mailDraft.connector_id.trim() || !mailDraft.expected_email.trim()}>
              {busy === "mail" ? "Saving…" : "Save mail connection"}
            </button>
            {mailConnection.connector_id && (
              <button type="button" className="secondary" disabled={busy === "mail"} onClick={() => {
                setMailDraft({ connector_id: "", expected_email: "" });
                void act("mail", async () => {
                  const connection = await api<MailConnection>("/v2/mail/connection", "PUT", { connector_id: "", expected_email: "" });
                  setMailConnection(connection);
                  notify("Mail sync disconnected for this profile. Saved evidence remains available.");
                });
              }}>Disconnect</button>
            )}
          </div>
        </form>
        {mailConnection.email && <p className="small muted">Last verified account: {mailConnection.email}</p>}
        {mailConnection.last_error && <div className="callout warning">{mailConnection.last_error}</div>}
      </section>
      <ProfileSettings notify={notify} />
    </>
  );
}
