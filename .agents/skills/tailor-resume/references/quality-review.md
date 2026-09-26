# Quality review before a resume is handed over

A resume passes only when every check passes; otherwise fix it and check again, or report the
exact failure.

1. **Facts.** Every employer, title, date, degree, school, number and skill appears in the
   person's evidence (App mode: the evidence registry; AI-only: `me/profile.md`). Nothing from the
   posting or company research became a claim about the person. Course or personal projects are
   labelled as such.
2. **Honesty.** No invented metric, graduation, publication, work permission or level. Gaps stay
   gaps. No placeholder text such as `[FILL IN]` is left.
3. **Format.** The country's paper and page count (`resume-format.md`), the section order, one
   date format, 10 to 12 pt body text, nothing cut off at the page edge.
4. **Readable by software.** Open the PDF: the text can be selected and reads in order; the name
   and contact line are text, not an image (`ats-rules.md`).
5. **Readable by a person.** Skim it for six seconds: the job's top two must-haves are visible in
   the top third of page one.
6. **Clean file.** No hidden characters (zero-width spaces, direction marks, non-breaking
   look-alikes) in the text. Document properties: title "<Name> CV" or "<Name> Resume", author the
   person's name, nothing else (no generator, description or keywords left by a tool). The app
   does this itself for its PDFs (`career-dashboard/backend/ai_marks.py`).

In App mode the app runs the full validator on its PDFs. For a LaTeX resume you may also run
`career check-resume <folder>/resume.tex --compile --output <folder>/resume.pdf --render-dir
<folder>/resume-preview --qa-json <folder>/qa.json`, then look at each preview page yourself.
