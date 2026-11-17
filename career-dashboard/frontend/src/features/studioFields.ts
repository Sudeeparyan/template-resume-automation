/** Browser drafts belong to one profile even when two profiles saved the same job ID. */
export function studioDraftKey(apiBase: string, jobId: string) {
  return "resume-studio:" + apiBase + ":" + jobId;
}

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** Browser storage is optional: saved server source must still open in restricted browsers. */
export function readStudioDraft(key: string, storage?: DraftStorage): string | null {
  try { return (storage ?? sessionStorage).getItem(key); } catch { return null; }
}

export function saveStudioDraft(key: string, source: string, storage?: DraftStorage): void {
  try { (storage ?? sessionStorage).setItem(key, source); } catch { /* The live editor still holds the source. */ }
}

export function clearStudioDraft(key: string, storage?: DraftStorage): void {
  try { (storage ?? sessionStorage).removeItem(key); } catch { /* Saving to the server remains available. */ }
}

export function macroSpan(source: string, name: string) {
  const prefix = new RegExp("\\\\newcommand\\{\\\\" + name + "\\}\\s*\\{").exec(
    source,
  );
  if (!prefix) return null;
  const start = prefix.index + prefix[0].length;
  let depth = 1;
  for (let i = start; i < source.length; i++) {
    if (source[i] === "\\") {
      i++;
      continue;
    }
    if (source[i] === "{") depth++;
    if (source[i] === "}" && --depth === 0) return { start, end: i };
  }
  return null;
}
export function readField(source: string, name: string) {
  const span = macroSpan(source, name);
  if (!span) return "";
  return source
    .slice(span.start, span.end)
    .replaceAll("\\textbar{}", "|")
    .replaceAll("\\textbackslash{}", "\\")
    .replace(/\\([&%$#_{}])/g, "$1");
}
export function writeField(source: string, name: string, value: string) {
  const span = macroSpan(source, name);
  if (!span) return source;
  const replacements: Record<string, string> = {
    "\\": "\\textbackslash{}",
    "|": "\\textbar{}",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
  };
  const escaped = value
    .replace(/\n/g, " ")
    .replace(/[\\&%$#_{}|~^]/g, (c) => replacements[c] || "\\" + c);
  return source.slice(0, span.start) + escaped + source.slice(span.end);
}
