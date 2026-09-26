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
