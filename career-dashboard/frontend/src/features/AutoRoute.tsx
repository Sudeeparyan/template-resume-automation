import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, RotateCcw } from "lucide-react";
import { Badge } from "../components/UI";
import type { RouteEndpoint, RoutePolicy } from "../types";
import { formatTokens } from "./pipelineEstimate";

const SOURCE: Record<string, string> = {
  you: "set by you",
  learned: "learned when it last ran out",
  guess: "starting guess",
};

// Friendlier names for the models each plan runs under Auto.
const MODEL_NAME: Record<string, string> = {
  "kimi-code/k3": "K3",
  "kimi-code/k3-256k": "K3-256k",
  "kimi-runtime": "Kimi's default",
  "codex-runtime": "Codex's default",
  "gpt-6-astra": "GPT-6-Astra",
  "gpt-6-sol": "GPT-6-Sol",
  "gpt-6-luna": "GPT-6-Luna",
  opus: "Opus",
  sonnet: "Sonnet",
  haiku: "Haiku",
};
export const modelName = (id: string) => MODEL_NAME[id] || id;

function models(e: RouteEndpoint) {
  const write = modelName(e.models.strong);
  const read = modelName(e.models.cheap);
  return write === read ? write : `${write} to write · ${read} to read`;
}

function state(e: RouteEndpoint, on: boolean) {
  if (!on) return <Badge>Off</Badge>;
  if (!e.ready) return <Badge tone="amber">Not set up</Badge>;
  if (e.resting)
    return (
      <Badge tone="amber" title={e.resting.reason}>
        Resting until {e.resting.until_text}
      </Badge>
    );
  if (e.paid && e.paid_block) return <Badge tone="amber">{e.paid_block}</Badge>;
  return <Badge tone="green">Ready</Badge>;
}

/** Auto's route, OpenRouter-style: free plans in the order she picks, Azure (paid) always last. */
export default function AutoRoute({
  endpoints,
  route,
  busy,
  onSave,
  onWake,
}: {
  endpoints: RouteEndpoint[];
  route: RoutePolicy;
  busy: string;
  onSave: (policy: RoutePolicy) => void;
  onWake: (provider: string) => void;
}) {
  const [draft, setDraft] = useState<RoutePolicy>(route);
  useEffect(() => setDraft(route), [route]);

  const byId = Object.fromEntries(endpoints.map((e) => [e.provider, e]));
  const free = draft.order.filter((p) => byId[p] && !byId[p].paid);
  const paid = draft.order.filter((p) => byId[p]?.paid);
  const changed = JSON.stringify(draft) !== JSON.stringify(route);

  const move = (provider: string, step: number) => {
    const next = [...free];
    const at = next.indexOf(provider);
    const to = at + step;
    if (to < 0 || to >= next.length) return;
    [next[at], next[to]] = [next[to], next[at]];
    setDraft({ ...draft, order: [...next, ...paid] });
  };
  const toggle = (provider: string) =>
    setDraft({ ...draft, enabled: { ...draft.enabled, [provider]: !draft.enabled[provider] } });
  // The limit is typed in thousands of tokens; empty goes back to the learned limit or the guess.
  const setCapacity = (provider: string, thousands: string) =>
    setDraft({
      ...draft,
      capacity: { ...(draft.capacity || {}), [provider]: thousands.trim() ? Math.round(Number(thousands) * 1000) : null },
    });

  const row = (provider: string, index: number, list: string[]) => {
    const e = byId[provider];
    const on = draft.enabled[provider] !== false;
    return (
      <li key={provider} className={"auto-route-row" + (on ? "" : " off")}>
        <span className="auto-route-step">{index + 1}</span>
        <div className="auto-route-name">
          <b>{e.label}</b>
          <small>
            {models(e)}
            {e.paid ? " · paid per call" : " · your plan, no extra cost"}
          </small>
          {e.usage && (
            <small className="auto-route-usage">
              {e.usage.percent ?? 0}% of its 5-hour limit used
              {e.usage.tokens
                ? ` (${formatTokens(e.usage.tokens)}${e.usage.resets_text ? ", window resets " + e.usage.resets_text : ""})`
                : ""}
            </small>
          )}
          {e.usage && (
            <label className="auto-route-cap">
              Limit per 5 hours
              <input
                type="number"
                min={10}
                step={50}
                aria-label={`${e.label} limit, thousands of tokens per 5 hours`}
                placeholder={e.usage.capacity ? String(Math.round(e.usage.capacity / 1000)) : ""}
                value={draft.capacity?.[provider] ? String(Math.round((draft.capacity[provider] as number) / 1000)) : ""}
                onChange={(event) => setCapacity(provider, event.target.value)}
              />
              K tokens · {draft.capacity?.[provider] ? SOURCE.you : SOURCE[e.usage.capacity_source]}
            </label>
          )}
        </div>
        <div className="auto-route-state">
          {state(e, on)}
          {e.resting && on && (
            <button
              className="link-button"
              disabled={busy === "wake-" + provider}
              title="Use it again now, if its limit has already reset"
              onClick={() => onWake(provider)}
            >
              <RotateCcw size={12} /> Try now
            </button>
          )}
        </div>
        <div className="auto-route-controls">
          {!e.paid && (
            <>
              <button
                className="icon-button"
                aria-label={`Move ${e.label} up`}
                disabled={index === 0}
                onClick={() => move(provider, -1)}
              >
                <ArrowUp size={15} />
              </button>
              <button
                className="icon-button"
                aria-label={`Move ${e.label} down`}
                disabled={index === list.length - 1}
                onClick={() => move(provider, 1)}
              >
                <ArrowDown size={15} />
              </button>
            </>
          )}
          <label className="auto-route-toggle">
            <input type="checkbox" checked={on} onChange={() => toggle(provider)} /> On
          </label>
        </div>
      </li>
    );
  };

  return (
    <div className="auto-route">
      <h3>The route, in the order Auto tries it</h3>
      <ol>
        {free.map((p, i) => row(p, i, free))}
        {paid.map((p, i) => row(p, free.length + i, paid))}
      </ol>
      <p className="small muted">
        When a plan reaches its usage limit, Auto rests it until the reset time the app printed and
        moves to the next one. Azure is always last: it runs only when every free plan is resting,
        failing or switched off, and never beyond your paid calls a day (below). The plans do not
        publish their limits in tokens, so each starts from a guess; type your own, or leave it
        empty and Auto learns it the first time the plan runs out. Daily Search uses these to show
        how much of each limit a search will take.
      </p>
      <div className="actions">
        <label className="auto-route-toggle">
          <input
            type="checkbox"
            checked={draft.allow_fallbacks}
            onChange={() => setDraft({ ...draft, allow_fallbacks: !draft.allow_fallbacks })}
          />{" "}
          If one fails, try the next
        </label>
        <button className="secondary" disabled={!changed || busy === "route"} onClick={() => onSave(draft)}>
          {busy === "route" ? "Saving…" : changed ? "Save the route" : "Route saved"}
        </button>
      </div>
    </div>
  );
}
