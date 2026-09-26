import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, FileText, ListChecks, LoaderCircle, Paperclip, Send, Sparkles } from "lucide-react";
import { shellApi, uploadFile } from "../api";
import { RichText } from "../components/UI";
import QuestionCard, { type Question } from "../components/QuestionCard";
import { firstName, type ProfileEntry } from "../profiles";

type Step = { label: string; status: "running" | "done" | "failed"; detail: string };
export type SetupMessage = {
  id: string;
  role: "assistant" | "you";
  text: string;
  kind: string;
  at: string;
  question?: Question;
  files?: string[];
};
export type SetupView = {
  messages: SetupMessage[];
  pending: Question | null;
  thinking: boolean;
  intake: { state: string; steps: Step[]; files: { name: string; bytes: number }[]; error?: string | null };
};

const ACCEPT = ".docx,.pdf,.txt,.md";

/** What the composer invites, given where the setup is. */
export function composerHint(view: SetupView): string {
  // A typed list replaces the ticked answers, so say so rather than invite an "addition".
  if (view.pending?.multi) return "Tick answers above, or type the complete list here";
  if (view.pending?.other) return view.pending.placeholder || "Type your answer";
  const state = view.intake.state;
  if (state === "empty" || state === "uploaded" || state === "failed")
    return "Paste text or attach documents";
  if (state === "reading") return "Reading your documents… (type “stop” to stop)";
  return "Tell me anything to add or correct";
}

/**
 * A new profile's first page: the setup chat. Documents go in with the paperclip (or are
 * pasted), the assistant reads them, asks what they leave open one question at a time, and
 * builds the workspace; then the page reloads into the full app. The form stays available.
 */
export default function OnboardingChat({
  profile,
  notify,
  onUseForm,
}: {
  profile: ProfileEntry;
  notify: (t: string, e?: boolean) => void;
  onUseForm: () => void;
}) {
  const [view, setView] = useState<SetupView | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const base = `/profiles/${profile.id}/intake/chat`;
  const who = firstName(profile.name) || profile.name;

  const load = useCallback(async () => {
    try {
      setView(await shellApi<SetupView>(base));
    } catch (e) {
      notify((e as Error).message, true);
    }
  }, [base, notify]);
  useEffect(() => {
    load();
  }, [load]);

  const state = view?.intake.state;
  const last = view?.messages[view.messages.length - 1];
  const built = last?.kind === "built";
  // Poll only while something happens on the server: reading, building, or the AI choosing a question.
  const active = !!view && !built && (view.thinking || state === "reading" || state === "building");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [active, load]);
  useEffect(() => {
    if (!built) return;
    const timer = setTimeout(() => location.reload(), 1800);
    return () => clearTimeout(timer);
  }, [built]);
  const count = view?.messages.length ?? 0;
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [count, view?.thinking, view?.pending?.id]);

  async function call(path: string, body?: unknown) {
    setBusy(true);
    try {
      setView(await shellApi<SetupView>(path, "POST", body));
      return true;
    } catch (e) {
      notify((e as Error).message, true);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    const message = text.trim();
    if (!message || busy) return;
    if (await call(base, { text: message })) setText("");
  }
  async function attach(files: FileList | File[]) {
    setBusy(true);
    try {
      for (const file of Array.from(files)) setView(await uploadFile<SetupView>("/api" + base + "/files", file, file.name));
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  const answer = (id: string) => (choices: string[], other: string, skip = false) =>
    void call(base + "/answer", { question_id: id, choices, other, skip });

  if (!view)
    return (
      <div className="onboarding">
        <LoaderCircle className="spin" /> Loading…
      </div>
    );
  const pending = view.pending;
  const lastProgress = [...view.messages].reverse().find((m) => m.kind === "progress")?.id;
  const working = state === "reading" || state === "building";
  const canAttach = !busy && !working && !built && !view.thinking;
  return (
    <div
      className={"setup-chat" + (dragging ? " dragging" : "")}
      onDragOver={(e) => {
        if (!canAttach) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (canAttach && e.dataTransfer.files.length) void attach(e.dataTransfer.files);
      }}
    >
      <div className="setup-head">
        <div>
          <div className="eyebrow">NEW PROFILE · SET UP IN CHAT</div>
          <h1>Let’s build {who}’s workspace</h1>
        </div>
        <button type="button" className="text-button" onClick={onUseForm}>
          <ListChecks size={15} /> Use the form instead
        </button>
      </div>

      <div className="chat-thread" aria-live="polite">
        {view.messages.map((m) =>
          m.role === "you" ? (
            <div key={m.id} className="chat-turn you">
              <div className="chat-turn-body">
                <div className="chat-msg you">
                  {m.kind === "files" ? (
                    <div className="setup-files">
                      {(m.files || []).map((f) => (
                        <span key={f}>
                          <FileText size={14} /> {f}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="chat-text">{m.text}</div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div key={m.id} className="chat-turn assistant">
              <span className="chat-avatar" aria-hidden="true">
                {m.kind === "built" ? <CheckCircle2 size={14} /> : m.kind === "error" ? <CircleAlert size={14} /> : <Sparkles size={14} />}
              </span>
              <div className="chat-turn-body">
                <div
                  className={
                    "chat-msg assistant" +
                    (m.kind === "error" ? " failed" : m.question && pending?.id === m.question.id ? " needs_input" : "")
                  }
                >
                  <RichText text={m.text} />
                  {m.kind === "progress" && m.id === lastProgress && working && (
                    <ol className="intake-steps setup-steps">
                      {view.intake.steps.map((s) => (
                        <li key={s.label} className={"step-" + s.status}>
                          {s.status === "running" ? (
                            <LoaderCircle size={16} className="spin" />
                          ) : s.status === "done" ? (
                            <CheckCircle2 size={16} />
                          ) : (
                            <CircleAlert size={16} />
                          )}
                          <div>
                            <b>{s.label}</b>
                            {s.detail && <small>{s.detail}</small>}
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                  {m.question?.why && <p className="setup-why">{m.question.why}</p>}
                  {m.question && pending?.id === m.question.id && (
                    <QuestionCard key={m.question.id} question={m.question} busy={busy} onAnswer={answer(m.question.id)} />
                  )}
                </div>
              </div>
            </div>
          ),
        )}
        {view.thinking && (
          <div className="chat-turn assistant processing">
            <span className="chat-avatar" aria-hidden="true">
              <Sparkles size={14} />
            </span>
            <div className="chat-turn-body">
              <div className="chat-msg assistant" role="status">
                <span className="setup-thinking">
                  <LoaderCircle size={15} className="spin" /> Thinking about what to ask next…
                </span>
              </div>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <div className={"chat-input" + (busy || view.thinking ? " working" : "")}>
          <textarea
            rows={1}
            value={text}
            maxLength={120000}
            disabled={built}
            aria-label="Message the setup assistant"
            placeholder={composerHint(view)}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="chat-input-buttons">
            <button
              type="button"
              className="chat-attach"
              onClick={() => fileRef.current?.click()}
              disabled={!canAttach}
              aria-label="Attach documents"
              title="Attach resumes or documents about you (Word, PDF, text or Markdown, up to 20 MB each)"
            >
              {busy ? <LoaderCircle className="spin" size={16} /> : <Paperclip size={16} />}
            </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept={ACCEPT}
              onChange={(e) => {
                const files = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (files.length) void attach(files);
              }}
            />
            <button className="chat-send" disabled={busy || view.thinking || !text.trim() || built} aria-label="Send" title="Send (Enter)">
              {busy ? <LoaderCircle className="spin" size={18} /> : <Send size={17} />}
            </button>
          </div>
        </div>
        <div className="chat-hint">
          <small className="muted">
            <kbd>Enter</kbd> sends · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line · drop files anywhere here · every question can be
            clicked, typed or skipped
          </small>
        </div>
      </form>
    </div>
  );
}
