import { useEffect } from "react";
import type { ComponentType } from "react";

/** The phone's bottom bar: these four tabs by short name, then More for the rest. */
export const PHONE_SHORT: Record<string, string> = {
  assistant: "Chat",
  dashboard: "Dashboard",
  daily: "Search",
  resumes: "Resumes",
};

export type PhoneTab = { id: string; label: string; Icon: ComponentType<{ size?: number }> };

/** The sheet More opens on a phone: every tab that is not in the bar, as a big row above it. */
export function PhoneMore({
  open,
  tabs,
  route,
  working,
  onClose,
  onGo,
}: {
  open: boolean;
  tabs: PhoneTab[];
  route: string;
  /** Background runs in progress, shown beside Agents. */
  working: number;
  onClose: () => void;
  onGo: (id: string) => void;
}) {
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="phone-more">
      <div className="phone-more-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="phone-more-sheet" role="dialog" aria-label="More pages">
        {tabs
          .filter((t) => !(t.id in PHONE_SHORT))
          .map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              className={route === id ? "active" : ""}
              aria-current={route === id ? "page" : undefined}
              onClick={() => {
                onClose();
                onGo(id);
              }}
            >
              <Icon size={20} />
              {label}
              {id === "agents" && working > 0 && <small>{working} working</small>}
            </button>
          ))}
      </div>
    </div>
  );
}
