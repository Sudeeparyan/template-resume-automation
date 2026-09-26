import { describe, it, expect } from "vitest";
import { readField, writeField, macroSpan } from "./studioFields";
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
