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
    // The backend's note is the whole introduction; it is not repeated.
    expect(html.split("No API key is needed").length).toBeLessThanOrEqual(2);
    expect(html).toContain("Claude Code</strong> — installed; sign in to it");
    expect(html).toContain("Codex</strong> — not installed");
    expect(html).toContain('type="password"');
    expect(html).toContain("career-dashboard/.env on this computer only");
    expect(html).toContain("https://console.anthropic.com/settings/keys");
    expect(html).not.toContain("sk-ant");
  });

  it("gives the exact install command and the sign-in step for a missing AI app", () => {
    const missing: AIStatus = {
      ...status,
      clis: [
        { id: "claude_code", label: "Claude Code", kind: "cli", ready: false, installed: false, signed_in: false, app_only: false,
          install: "irm https://claude.ai/install.ps1 | iex", sign_in: "Run `claude` once in a terminal and sign in with your Claude account." },
        { id: "antigravity", label: "Antigravity", kind: "cli", ready: false, installed: false, signed_in: false, app_only: true,
          install: "irm https://antigravity.google/cli/install.ps1 | iex", sign_in: "Run `agy` once in a terminal and sign in with your Google account." },
        { id: "codex", label: "Codex", kind: "cli", ready: false, installed: false, signed_in: false, install: "" },
      ],
    };
    const html = renderToStaticMarkup(<AISetupView who="Sam" status={missing} />);
    expect(html).toContain("<code>irm https://claude.ai/install.ps1 | iex</code>");
    expect(html).toContain("<code>irm https://antigravity.google/cli/install.ps1 | iex</code>");
    expect(html).toContain("Then: Run <code>agy</code> once in a terminal");
    expect(html).toContain("The Antigravity app is installed, but the dashboard needs its command-line tool.");
    expect(html).toContain('aria-label="Copy command"');
    // No command to paste: the plain instruction instead.
    expect(html).toContain("Install the ChatGPT desktop app (it includes Codex) and sign in.");
    expect(html).toContain("This page connects by itself when it is done.");
  });

  it("tells a signed-out app how to sign in", () => {
    const signedOut: AIStatus = {
      ...status,
      clis: [{ id: "claude_code", label: "Claude Code", kind: "cli", ready: false, installed: true, signed_in: false,
        install: "irm https://claude.ai/install.ps1 | iex", sign_in: "Run `claude` once in a terminal and sign in with your Claude account." }],
    };
    const html = renderToStaticMarkup(<AISetupView who="Sam" status={signedOut} />);
    expect(html).toContain("installed; sign in to it");
    expect(html).toContain("Run <code>claude</code> once in a terminal");
    expect(html).not.toContain("<code>irm https://claude.ai/install.ps1");
  });

  it("reports the result of a saved key", () => {
    const html = renderToStaticMarkup(<AISetupView who="Sam" status={status} message={{ text: "Key saved and working: 12 models available.", ok: true }} />);
    expect(html).toContain("Key saved and working");
    expect(html).toContain("small success");
  });
});
