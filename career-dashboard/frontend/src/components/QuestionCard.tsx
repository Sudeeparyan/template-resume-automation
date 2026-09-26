import { useEffect, useState } from "react";
import { Check, CornerDownLeft } from "lucide-react";

export type QuestionOption = { label: string; description?: string; value: string };
export type Question = {
  id: string;
  key?: string;
  text: string;
  why?: string;
  options: QuestionOption[];
  multi: boolean;
  other: boolean;
  skippable: boolean;
  placeholder?: string;
  defaults?: string[];
};

/**
 * One question at a time, like a terminal assistant asks: numbered answers to click (or press
 * 1–9), several at once when `multi`, a line for an answer of your own, and Skip where allowed.
 * A single-choice click answers at once; everything else waits for Continue.
 */
export default function QuestionCard({
  question,
  busy,
  onAnswer,
}: {
  question: Question;
  busy: boolean;
  onAnswer: (choices: string[], other: string, skip?: boolean) => void;
}) {
  const known = (value: string) => question.options.some((o) => o.value === value);
  const [picked, setPicked] = useState<string[]>(() => (question.multi ? (question.defaults || []).filter(known) : []));
  const [other, setOther] = useState("");
  const toggle = (value: string) =>
    setPicked((now) => (now.includes(value) ? now.filter((v) => v !== value) : [...now, value]));
  const ready = picked.length > 0 || other.trim().length > 0;
  const submit = () => {
    if (ready && !busy) onAnswer(picked, other.trim());
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (busy || e.metaKey || e.ctrlKey || e.altKey || target?.closest("textarea, input, select")) return;
      const n = Number(e.key);
      if (!Number.isInteger(n) || n < 1 || n > Math.min(9, question.options.length)) return;
      e.preventDefault();
      const value = question.options[n - 1].value;
      if (question.multi) toggle(value);
      else onAnswer([value], "");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, question, onAnswer]);

  return (
    <div className="question-card" role="group" aria-label={question.text}>
      {question.options.length > 0 && (
        <ul className="question-options">
          {question.options.map((o, i) => {
            const on = picked.includes(o.value);
            return (
              <li key={o.value}>
                <button
                  type="button"
                  className={"question-option" + (on ? " selected" : "")}
                  aria-pressed={question.multi ? on : undefined}
                  disabled={busy}
                  onClick={() => (question.multi ? toggle(o.value) : onAnswer([o.value], ""))}
                >
                  <span className="question-key" aria-hidden="true">
                    {question.multi ? on ? <Check size={13} /> : "" : i + 1}
                  </span>
                  <span className="question-label">
                    <b>{o.label}</b>
                    {o.description && <small>{o.description}</small>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {question.other && (
        <div className="question-other">
          <input
            value={other}
            disabled={busy}
            aria-label="Your own answer"
            placeholder={
              question.options.length
                ? question.multi
                  ? question.placeholder || "Add others, separated by commas"
                  : "Or type your own answer"
                : question.placeholder || "Type your answer"
            }
            onChange={(e) => setOther(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
          />
        </div>
      )}
      {(question.multi || question.other || question.skippable) && (
        <div className="question-actions">
          {(question.multi || question.other) && (
            <button type="button" className="primary" disabled={busy || !ready} onClick={submit}>
              <CornerDownLeft size={14} />
              {question.multi && picked.length ? `Continue with ${picked.length}` : "Continue"}
            </button>
          )}
          {question.skippable && (
            <button type="button" className="text-button" disabled={busy} onClick={() => onAnswer([], "", true)}>
              Skip
            </button>
          )}
          {question.options.length > 1 && (
            <small className="muted question-tip">
              {question.multi ? "Press 1–" + Math.min(9, question.options.length) + " to pick" : "Press 1–" + Math.min(9, question.options.length) + " to answer"}
            </small>
          )}
        </div>
      )}
    </div>
  );
}
