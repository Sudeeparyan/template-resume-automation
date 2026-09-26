// Words and times the Agents tab and its live side panel share.

export const PROVIDER_LABEL: Record<string, string> = {
  auto: "Auto",
  azure_openai: "Azure OpenAI",
  claude_code: "Claude Code",
  codex: "Codex",
  kimi_cli: "Kimi Code",
  openai: "OpenAI",
  anthropic: "Claude API (Anthropic)",
  openrouter: "OpenRouter",
  gemini: "Gemini",
  kimi: "Kimi",
};
export const AGENT_LABEL: Record<string, string> = {
  discovery: "Job search",
  research: "Company & hiring research",
  resume_build: "Resume build & ATS check",
  resume_match: "Independent resume review",
  email: "Gmail sync",
  job_quality: "Posting check",
  study_plan: "Study plan",
};

export const active = (state?: string) => state === "queued" || state === "running";

export function duration(seconds?: number | null) {
  if (seconds == null) return "";
  if (seconds < 1) return "under 1s";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-US", {
    day: "numeric",
    month: "short",
  });
}

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

// "Auto → Codex · gpt-6-astra" when Auto picked the plan for this call.
export const providerText = (p?: string | null, m?: string | null, routed = false) =>
  p
    ? `${routed ? "Auto → " : ""}${PROVIDER_LABEL[p] || p}${
        m && !["codex-runtime", "kimi-runtime", "auto"].includes(m) ? " · " + m : ""
      }`
    : "";
