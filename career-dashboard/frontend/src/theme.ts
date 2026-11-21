/** The person's colour theme: the computer's setting, or a fixed light or dark choice.
 * public/assets/theme-init.js applies the saved choice before the first paint; this module
 * changes it later. Storage can be blocked, so every read and write is guarded and the
 * system theme is the fallback. */
export type ThemeChoice = "system" | "light" | "dark";
export const THEME_KEY = "career-theme";
export const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function storage(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readTheme(store: Store | null = storage()): ThemeChoice {
  try {
    const value = store?.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function saveTheme(choice: ThemeChoice, store: Store | null = storage()): void {
  try {
    if (choice === "system") store?.removeItem(THEME_KEY);
    else store?.setItem(THEME_KEY, choice);
  } catch {
    // Not remembered this time; the choice still applies until the page reloads.
  }
  applyTheme(choice);
}

export function applyTheme(choice: ThemeChoice): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}
