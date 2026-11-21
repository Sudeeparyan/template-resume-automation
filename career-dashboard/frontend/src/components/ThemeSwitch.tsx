import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { readTheme, saveTheme, THEMES, type ThemeChoice } from "../theme";

const ICONS = { system: Monitor, light: Sun, dark: Moon } as const;

/** System / Light / Dark, remembered in this browser. */
export function ThemeSwitch({ labelled = false }: { labelled?: boolean }) {
  const [choice, setChoice] = useState<ThemeChoice>(() => readTheme());
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Colour theme">
      {THEMES.map(({ id, label }) => {
        const Icon = ICONS[id];
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={choice === id}
            title={`${label} theme`}
            className={choice === id ? "selected" : ""}
            onClick={() => {
              setChoice(id);
              saveTheme(id);
            }}
          >
            <Icon size={14} aria-hidden="true" />
            <span className={labelled ? "" : "visually-hidden"}>{label}</span>
          </button>
        );
      })}
    </div>
  );
}
