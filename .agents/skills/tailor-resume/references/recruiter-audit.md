# The recruiter audit: three passes over every resume

Run this on every tailored resume before it is handed over. Do all three passes yourself and show
the person the result; never hand them these steps as prompts to run.

Why: the resume's only job is to get shortlisted. It is read twice before anyone decides: once by
software, once by a person skimming for six seconds. Pass 1 finds what would fail, Pass 2 fixes
it, Pass 3 checks again as both readers.

"The evidence" below means the person's own facts: App mode, the profile's
`data/context/evidence.yml`; AI-only mode, `me/profile.md` and the files it cites.

## Pass 1: a senior recruiter at this company

Act as a senior recruiter at this company, holding this posting, after reading fifty resumes for
it. Be direct; the person would rather fix problems now than be ignored later.

1. **Match score out of 100**, with the parts shown:

   | Part | Weight | Measures |
   |---|---|---|
   | Must-have coverage | 40% | Share of the must-haves backed by real, evidenced experience |
   | Keywords | 20% | Must-have phrases present in the posting's own wording |
   | Evidence strength | 20% | Numbers, scope and outcomes rather than duties |
   | Level and domain | 10% | Right seniority band and industry vocabulary |
   | Six-second read | 10% | Front-loading, bullet length, easy scanning |

2. **The five missing keywords** the software is most likely scanning for, ranked by how often and
   how prominently the posting uses them. For each, say which it is:
   - *In the evidence but not on this page*: a writing problem, fixed in Pass 2.
   - *Not in the evidence at all*: a truth problem. It becomes a gap in the report and a "To learn"
     item; it never goes on the resume.
3. **The three red flags** a hiring manager would see in ten seconds, and where. Check at least:
   an unexplained gap, a level far above anything held, duty-listing instead of results, no
   numbers anywhere, a summary that fits any job, several short stints with no label, the one
   thing this job wants buried on page two, bullets of three lines or more, missing tools the
   posting names, and an open question about location or permission to work.
4. **Strong and weak sections**, one line each, naming the reason.
5. **Against a strong candidate:** what the best resume for this posting looks like, where this
   one stands, and what closes the distance. If only time closes it, say so.

## Pass 2: rewrite

Rework the bullets. Stay inside what the evidence says for each role: select, reorder and tighten,
never add a fact.

1. Add the missing keywords from Pass 1 only where they are true, in a natural sentence. A keyword
   that cannot be added truthfully stays a gap.
2. Fix every red flag, or say plainly why paper cannot fix it.
3. Shape each bullet as action, then what changed, then how it was measured, then how it was done.
4. Start with a strong verb; never "responsible for", "helped with", "worked on", "assisted in".
5. Use numbers the evidence records. When a number would matter but is not recorded, do not write
   one: add it to a short list of questions for the person ("About how many reports a week?").
6. One or two lines per bullet. The most relevant result leads each role; roles stay in date order.

## Pass 3: final scan

**As the software:** would it pass (yes or no)? Which keywords are now present and where, which
are still missing and why (gap, or not yet placed), and any formatting risk from `ats-rules.md`.
In App mode, back this with `career ws score --job-id <id>` and the `resume_match` review.

**As a hiring manager on resume 147 of 200:** which sections you would skip, what makes you stop
(good or bad, quote the line), which pile (yes, maybe, no) and the one change that moves it up.
Rewrite anything that would be skipped. Then save the final version; the audited version is the
deliverable.

## What to tell the person

Keep it short:

```text
Recruiter audit: <Company>, <Role>
Before: NN/100   After: NN/100   Pile: yes | maybe | no
Fixed:         red flags and keywords closed, one line each
Still open:    what could not be fixed truthfully, and why
Need from you: each missing number, as a plain question
```

## Honesty

- The "after" score is an honest re-score. If the fixes moved it four points, report four.
- A keyword counts as present only when it is true of the person.
- Below 55 with structural gaps, say plainly that the application is a long shot and what would
  make it viable. A resume cannot fix a role mismatch.
