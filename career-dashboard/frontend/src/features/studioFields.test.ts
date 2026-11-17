import { describe, it, expect } from "vitest";
import { clearStudioDraft, readField, readStudioDraft, saveStudioDraft, writeField, macroSpan, studioDraftKey } from "./studioFields";
describe("Studio text editing", () => {
  const source = String.raw`\newcommand{\CoreSkills}{SQL; Power BI}
\newcommand{\SelectedProjectTitle}{A \textbar{} B}
\begin{document}Untouched\end{document}`;
  it("round-trips punctuation without changing the template", () => {
    const value = "Python; 50% & SQL; {draft}_#1";
    const next = writeField(source, "CoreSkills", value);
    expect(readField(next, "CoreSkills")).toBe(value);
    expect(next).toContain("\\begin{document}Untouched\\end{document}");
  });
  it("handles nested braces and missing macros", () => {
    expect(readField(source, "SelectedProjectTitle")).toBe("A | B");
    expect(
      readField(
        writeField(source, "SelectedProjectTitle", "Next"),
        "SelectedProjectTitle",
      ),
    ).toBe("Next");
    expect(macroSpan(source, "Absent")).toBeNull();
    expect(writeField(source, "Absent", "Ignored")).toBe(source);
  });
});

describe("Studio draft isolation", () => {
  it("never restores another profile's unsaved source for the same job", () => {
    const drafts = new Map<string, string>();
    const first = studioDraftKey("/p/first-profile/api", "shared-job");
    const second = studioDraftKey("/p/second-profile/api", "shared-job");
    drafts.set(first, "First profile's private resume source");
    expect(drafts.get(second)).toBeUndefined();
    expect(drafts.get(studioDraftKey("/p/first-profile/api", "shared-job"))).toContain("First profile");
    expect(studioDraftKey("/p/first-profile/api", "different-job")).not.toBe(first);
  });

  it("continues opening the saved resume when browser storage is blocked", () => {
    const blocked = {
      getItem: () => { throw new Error("Browser storage is unavailable"); },
      setItem: () => { throw new Error("Browser storage is unavailable"); },
      removeItem: () => { throw new Error("Browser storage is unavailable"); },
    };
    const key = studioDraftKey("/p/example-profile/api", "example-job");
    expect(readStudioDraft(key, blocked) ?? "Saved server source").toBe("Saved server source");
    expect(() => saveStudioDraft(key, "Edited source", blocked)).not.toThrow();
    expect(() => clearStudioDraft(key, blocked)).not.toThrow();
  });

  it("preserves and clears unsaved text when browser storage is available", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => { values.set(key, value); },
      removeItem: (key: string) => { values.delete(key); },
    };
    saveStudioDraft("example-key", "Unsaved source", storage);
    expect(readStudioDraft("example-key", storage)).toBe("Unsaved source");
    clearStudioDraft("example-key", storage);
    expect(readStudioDraft("example-key", storage)).toBeNull();
  });
});
