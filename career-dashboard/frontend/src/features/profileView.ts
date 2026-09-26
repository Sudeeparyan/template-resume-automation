import type { ProfileEntry, ProfileFieldSpec, ProfileFieldValue, ProfileSync } from "../types";

export const KIND_LABELS: Record<string, string> = {
  personal: "Basics",
  experience: "Experience",
  project: "Project",
  skill: "Skill",
  education: "Education",
  certification: "Certification",
  fact: "Fact",
};

const STATUS: Record<string, { label: string; tone: string; title: string }> = {
  confirmed: { label: "Confirmed", tone: "green", title: "Confirmed in your evidence registry." },
  user_reported: { label: "You reported", tone: "neutral", title: "From your own words; not independently verified." },
  conditional: { label: "Conditional", tone: "neutral", title: "Usable only alongside the evidence named below." },
  missing: { label: "Not on record", tone: "amber", title: "Nothing on record yet; resumes never state it." },
  hold: { label: "On hold", tone: "amber", title: "Held until you supply the missing details." },
};

/** The badge for an entry: an unreviewed edit wins over the registry status. */
export function statusBadge(entry: Pick<ProfileEntry, "review_state" | "status">) {
  if (entry.review_state === "user_updated")
    return { label: "Waiting for your review", tone: "amber", title: "A suggested change. It applies everywhere once you confirm it." };
  return (
    STATUS[entry.status || ""] || { label: "Registered", tone: "green", title: "Part of your active profile." }
  );
}

const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/** Sort key for a date range such as "Jul 2024 - Present": later end dates first. */
export function dateRank(dates: unknown): number {
  const text = String(dates || "");
  const end = text.split(/\s[-–—]\s|\sto\s/i).pop() || "";
  if (/present|current|now/i.test(end)) return 1e6;
  const year = end.match(/\b(19|20)\d{2}\b/) || text.match(/\b(19|20)\d{2}\b/);
  if (!year) return 0;
  const month = MONTHS.findIndex((m) => end.toLowerCase().includes(m));
  return Number(year[0]) * 12 + Math.max(month, 0);
}

export function byDateDesc(a: ProfileEntry, b: ProfileEntry) {
  return dateRank(b.fields.dates) - dateRank(a.fields.dates);
}

/** Short lines read best as tags; sentences read best as a list. */
export function isTagList(lines: string[]) {
  return lines.length > 0 && lines.every((line) => line.length <= 48);
}

export function asList(value: ProfileFieldValue | undefined): string[] {
  if (Array.isArray(value)) return value;
  return String(value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function asText(value: ProfileFieldValue | undefined): string {
  if (Array.isArray(value)) return value.join(", ");
  return value === null || value === undefined ? "" : String(value);
}

/** Group entries under headings, in the given order, then any others alphabetically. */
export function grouped(entries: ProfileEntry[], order: string[]) {
  const map = new Map<string, ProfileEntry[]>();
  for (const entry of entries) {
    const key = entry.group || "Other";
    map.set(key, [...(map.get(key) || []), entry]);
  }
  const rest = [...map.keys()].filter((k) => !order.includes(k)).sort();
  return [...order, ...rest]
    .filter((k) => map.has(k))
    .map((name) => ({ name, entries: map.get(name)! }));
}

export function emptyFields(form: ProfileFieldSpec[]): Record<string, ProfileFieldValue> {
  return Object.fromEntries(
    form.map((spec) => [spec.key, spec.type === "list" ? [""] : spec.type === "number" ? null : ""]),
  );
}

/** Lists drop blank lines before saving; the backend does the same. */
export function cleanFields(values: Record<string, ProfileFieldValue>) {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [
      key,
      Array.isArray(value) ? value.map((v) => v.trim()).filter(Boolean) : value,
    ]),
  );
}

const WORDS: Record<string, string> = {
  ai: "AI", cv: "CV", gpa: "GPA", genai: "GenAI", github: "GitHub", id: "ID", ids: "IDs",
  jd: "JD", linkedin: "LinkedIn", ml: "ML", opt: "OPT", url: "URL", us: "US", h1b: "H-1B",
};

export function humanize(key: string) {
  return key
    .split(/[\s_\-]+/)
    .filter(Boolean)
    .map((word, i) => WORDS[word.toLowerCase()] || (i === 0 ? word[0].toUpperCase() + word.slice(1) : word.toLowerCase()))
    .join(" ");
}

/** Config tokens such as "include_exactly_as_supplied" read as words; other text is untouched. */
export function readable(text: string) {
  const words = text.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (token) => token.replace(/_/g, " "));
  return words === text ? text : words.charAt(0).toUpperCase() + words.slice(1);
}

export function isUrl(text: string) {
  return /^https?:\/\/\S+$/i.test(text.trim());
}

function joinWords(words: string[]) {
  return words.length < 2 ? words.join("") : words.slice(0, -1).join(", ") + " and " + words[words.length - 1];
}

/** "Saved. Also updated: …" — every place a Profile save reached, in plain words. */
export function describeSync(done: string, synced: ProfileSync | undefined) {
  const notes = (synced?.notes || []).map((note) => " " + note).join("");
  if (!synced || !synced.updated.length) return done + notes;
  const places = synced.updated
    .filter((place) => place !== "Draft resumes")
    .map((place) => place.replace(/\s*\(.*\)$/, "").toLowerCase());
  const drafts = synced.drafts || [];
  if (drafts.length) {
    const companies = [...new Set(drafts.map((d) => d.company))];
    places.push(`${drafts.length} draft resume${drafts.length === 1 ? "" : "s"} (${joinWords(companies)})`);
  }
  const rebuild = drafts.length ? " Rebuild their PDFs in Resume Studio." : "";
  return `${done} Also updated: ${joinWords(places)}.${rebuild}${notes}`;
}

type Change = {
  operation: string;
  id?: string;
  kind?: string;
  title?: string;
  summary?: string;
  rationale?: string;
};

/** A Profile chat change set, read as plain sentences. */
export function describeChange(change: Change, labels: Record<string, string>) {
  const kind = (KIND_LABELS[change.kind || ""] || humanize(change.kind || "fact")).toLowerCase();
  const name = change.id ? labels[change.id] || change.id : "";
  switch (change.operation) {
    case "add":
      return { action: `Add ${kind}`, name: change.title || "", text: change.summary || "" };
    case "update":
      return { action: "Change", name, text: change.summary || "" };
    case "remove":
      return { action: "Remove", name, text: "" };
    default:
      return { action: "Save for review", name: change.title || "Note", text: change.summary || "" };
  }
}
