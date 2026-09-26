import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { safeUrl } from "../api";
import { Field, Modal } from "../components/UI";
import type { ProfileEntry, ProfileFieldSpec, ProfileFieldValue } from "../types";
import { cleanFields, emptyFields, humanize, isTagList, isUrl, KIND_LABELS } from "./profileView";

export function Tags({ items, tone = "" }: { items: string[]; tone?: string }) {
  return (
    <div className="tag-row">
      {items.map((item, i) => (
        <span key={i} className={"tag " + tone}>
          {item}
        </span>
      ))}
    </div>
  );
}

/** Short lines as tags, sentences as a bulleted list. */
export function Lines({ items, tone = "" }: { items: string[]; tone?: string }) {
  if (!items.length) return <p className="muted small">Nothing listed yet.</p>;
  if (isTagList(items)) return <Tags items={items} tone={tone} />;
  return (
    <ul className="entry-bullets">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

/** A single value: a link when it is a web address, text otherwise. */
export function Value({ text }: { text: string }) {
  if (!text.trim()) return <span className="muted">Not provided</span>;
  if (isUrl(text))
    return (
      <a href={safeUrl(text.trim())} target="_blank" rel="noreferrer">
        {text.trim().replace(/^https?:\/\//, "")}
      </a>
    );
  return <span className="preserve">{text}</span>;
}

function itemName(value: unknown, index: number) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const v = value as Record<string, unknown>;
    const name = v.id || v.name || v.title || v.label;
    const title = v.title && v.id && v.title !== v.id ? " — " + v.title : "";
    if (name) return String(name) + title;
  }
  return "Item " + (index + 1);
}

/** Any stored value, shown as labelled rows, tags and lists; never as raw JSON. */
export function DataView({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "")
    return <span className="muted">—</span>;
  if (typeof value === "boolean") return <>{value ? "Yes" : "No"}</>;
  if (typeof value === "number") return <>{value}</>;
  if (typeof value === "string") return isUrl(value) ? <Value text={value} /> : <span className="preserve">{value}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">None</span>;
    if (value.every((v) => v === null || typeof v !== "object"))
      return <Lines items={value.map((v) => String(v ?? ""))} />;
    return (
      <div className="data-items">
        {value.map((item, i) => (
          <details key={i}>
            <summary>{itemName(item, i)}</summary>
            <DataView value={item} />
          </details>
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return <span className="muted">None</span>;
  return (
    <dl className="data-view">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{humanize(key)}</dt>
          <dd>
            <DataView value={item} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** One editable line per list item: Enter starts a new line, pasted lines split. */
export function ListField({
  labelId,
  value,
  onChange,
  placeholder,
}: {
  labelId: string;
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}) {
  const refs = useRef<(HTMLTextAreaElement | null)[]>([]);
  const focus = (index: number) =>
    requestAnimationFrame(() => {
      const el = refs.current[index];
      el?.focus();
      el?.setSelectionRange(el.value.length, el.value.length);
    });
  const move = (from: number, to: number) => {
    const next = [...value];
    [next[from], next[to]] = [next[to], next[from]];
    onChange(next);
    focus(to);
  };
  return (
    <div className="list-editor" role="group" aria-labelledby={labelId}>
      {value.map((line, i) => (
        <div className="list-row" key={i}>
          <textarea
            ref={(el) => {
              refs.current[i] = el;
            }}
            rows={line.length > 110 ? 3 : line.length > 55 ? 2 : 1}
            value={line}
            placeholder={placeholder}
            aria-label={`Line ${i + 1}`}
            onChange={(e) => {
              const parts = e.target.value.split("\n");
              if (parts.length > 1) {
                onChange([...value.slice(0, i), ...parts, ...value.slice(i + 1)]);
                focus(i + parts.length - 1);
              } else onChange(value.map((v, n) => (n === i ? e.target.value : v)));
            }}
            onKeyDown={(e) => {
              if (e.key === "Backspace" && !line && value.length > 1) {
                e.preventDefault();
                onChange(value.filter((_, n) => n !== i));
                focus(Math.max(i - 1, 0));
              }
            }}
          />
          <div className="list-row-actions">
            <button type="button" className="icon-button" aria-label={`Move line ${i + 1} up`} disabled={i === 0} onClick={() => move(i, i - 1)}>
              <ArrowUp size={14} />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label={`Move line ${i + 1} down`}
              disabled={i === value.length - 1}
              onClick={() => move(i, i + 1)}
            >
              <ArrowDown size={14} />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label={`Remove line ${i + 1}`}
              onClick={() => onChange(value.length > 1 ? value.filter((_, n) => n !== i) : [""])}
            >
              <X size={14} />
            </button>
          </div>
        </div>
      ))}
      <button
        type="button"
        className="text-button"
        onClick={() => {
          onChange([...value, ""]);
          focus(value.length);
        }}
      >
        <Plus size={14} /> Add line
      </button>
    </div>
  );
}

function FormField({
  spec,
  value,
  onChange,
}: {
  spec: ProfileFieldSpec;
  value: ProfileFieldValue | undefined;
  onChange: (value: ProfileFieldValue) => void;
}) {
  const id = "profile-field-" + spec.key.replace(/\W+/g, "-");
  if (spec.type === "list") {
    const lines = Array.isArray(value) && value.length ? value : [""];
    return (
      <div className="field wide-field">
        <span id={id}>{spec.label}</span>
        {spec.hint && <small className="muted">{spec.hint}</small>}
        <ListField labelId={id} value={lines} onChange={onChange} placeholder={spec.placeholder} />
      </div>
    );
  }
  const text = value === null || value === undefined ? "" : Array.isArray(value) ? value.join(", ") : String(value);
  return (
    <div className={spec.type === "textarea" ? "wide-field" : ""}>
      <Field label={spec.label + (spec.required ? " (required)" : "")}>
        {spec.type === "textarea" ? (
          <textarea rows={5} maxLength={30000} value={text} placeholder={spec.placeholder} onChange={(e) => onChange(e.target.value)} />
        ) : (
          <input
            type={spec.type === "number" ? "number" : "text"}
            required={spec.required}
            maxLength={spec.key === "title" ? 250 : 1000}
            value={text}
            placeholder={spec.placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {spec.hint && <small className="muted">{spec.hint}</small>}
      </Field>
    </div>
  );
}

/** The add / edit form. Its fields come from the backend, so what is saved is what is shown. */
export function ProfileEditor({
  entry,
  initialKind,
  schema,
  busy,
  onSave,
  onClose,
}: {
  entry: ProfileEntry | null;
  initialKind: string;
  schema: Record<string, ProfileFieldSpec[]>;
  busy: boolean;
  onSave: (kind: string, fields: Record<string, ProfileFieldValue>) => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState(entry?.kind || initialKind);
  const form = entry ? entry.form : schema[kind] || [];
  const [values, setValues] = useState<Record<string, ProfileFieldValue>>(() =>
    entry ? { ...entry.fields } : emptyFields(schema[initialKind] || []),
  );
  return (
    <Modal title={entry ? "Edit " + entry.label : "Add to your profile"} onClose={onClose} wide>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSave(kind, cleanFields(values));
        }}
      >
        {!entry && (
          <Field label="What are you adding?">
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setValues(emptyFields(schema[e.target.value] || []));
              }}
            >
              {Object.keys(schema).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k] || humanize(k)}
                </option>
              ))}
            </select>
          </Field>
        )}
        {entry && !entry.in_sync && (
          <div className="callout warning" role="note">
            <div>
              <b>This entry's wording was changed outside this form</b>
              <p>It currently reads:</p>
              <p className="preserve">{entry.summary}</p>
              <p>Saving replaces that wording with the fields below.</p>
            </div>
          </div>
        )}
        <div className="form-grid profile-form">
          {form.map((spec) => (
            <FormField
              key={spec.key}
              spec={spec}
              value={values[spec.key]}
              onChange={(value) => setValues((current) => ({ ...current, [spec.key]: value }))}
            />
          ))}
        </div>
        <p className="muted small">
          Saving marks this entry for your review. New resumes wait until you confirm it; nothing is
          written to your evidence registry.
        </p>
        <div className="actions">
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
