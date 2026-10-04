import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AISetupView, type AIStatus } from "./AISetup";

const status: AIStatus = {
  any_ready: false,
  note: "Sign in to one of these AI apps on this computer, or add an API key. Keys stay on this computer.",
  clis: [
    { id: "claude_code", label: "Claude Code", kind: "cli", ready: false, installed: true, signed_in: false },
    { id: "codex", label: "Codex", kind: "cli", ready: false, installed: false, signed_in: false },
  ],
  keys: [{ id: "anthropic", label: "Claude", kind: "api_key", ready: false, key_name: "ANTHROPIC_API_KEY", saved_in: "", get_key: "https://console.anthropic.com/settings/keys" }],
};

describe("AI setup before the first build", () => {
  it("offers the AI apps on this computer and a key that stays local", () => {
    const html = renderToStaticMarkup(<AISetupView who="Sam" status={status} />);
    expect(html).toContain("First, an AI to read Sam’s documents");
    expect(html).toContain("Claude Code</strong> — installed; sign in to it");
    expect(html).toContain("Codex</strong> — not installed");
    expect(html).toContain('type="password"');
    expect(html).toContain("career-dashboard/.env on this computer only");
    expect(html).toContain("https://console.anthropic.com/settings/keys");
    expect(html).not.toContain("sk-ant");
  });

  it("reports the result of a saved key", () => {
    const html = renderToStaticMarkup(<AISetupView who="Sam" status={status} message={{ text: "Key saved and working: 12 models available.", ok: true }} />);
    expect(html).toContain("Key saved and working");
    expect(html).toContain("small success");
  });
});
