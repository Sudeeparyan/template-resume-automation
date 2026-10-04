---
name: tailor-resume
description: >-
  Make an honest resume tailored to one job, from the person's own evidence only, as an Irish A4
  CV, in PDF and DOCX, then audit it like a recruiter. Use for "make a resume for this job", a pasted posting or link, or for each
  job that find-jobs keeps.
---

# Tailor a resume

Read `AGENTS.md` first. Then read `references/resume-format.md`, `references/ats-rules.md`,
`references/recruiter-audit.md` and `references/quality-review.md`. Every resume gets the
recruiter audit and the quality review before it is handed over; a first draft is never the
deliverable. Prefer App mode whenever it is available: its gates, evidence registry, page
contract and validator are stricter than anything done by hand.

## Before writing

1. Have the posting's full text from its own page and its link. A job only pasted as a title is
   not enough: ask for the link or the text.
2. Run the checks from `find-jobs` (location, permission to work, never re-apply, legitimate).
   If one fails, say which and why, quoting the posting, and stop unless the person insists.
3. Read the person's evidence (App mode: the profile's `data/context/evidence.yml`; AI-only:
   `me/profile.md` and the files it cites).

## App mode

First read `career ws summary --profile <profile-id>` and
`career ws profile --profile <profile-id>` for the current state and candidate facts. Resolve
pending evidence reconciliation before tailoring. Use the exact selected profile ID and the
saved job ID in every command below:

```text
career add --url <posting link> --profile <profile-id>
career add --file job.json --profile <profile-id>    (instead, when you have the full text as JSON)
career ws fit --job-id <job-id> --profile <profile-id>
career ws run --kind research --job-id <job-id> --profile <profile-id>
career ws tailor --job-id <job-id> --profile <profile-id>
career ws run --kind resume_match --job-id <job-id> --profile <profile-id>
career ws docx --job-id <job-id> --profile <profile-id>
career ws cover-letter --job-id <job-id> --profile <profile-id>
career ws run --kind study_plan --job-id <job-id> --profile <profile-id>
```

The `--url` form resolves the link to the employer's own posting first (an applicant-tracking
feed, the page's structured posting, or the one employer posting an aggregator page links to)
and reads its full text; when it answers `needs_employer_link` or `needs_details`, ask the person
for what it names instead of guessing. `career add` JSON: `company`, `title`, `location`, `url`,
the full `description`. `career ws
tailor` tailors with the AI app signed in on the computer and uses the research; with no AI ready
it ranks and fits registered evidence only (`tailored_by_ai` says which, so say so). Report the
PDF path, pages, coverage, ATS score, gaps and the match review. AI-suggested skills or proposed
projects are gaps to learn, never candidate experience. New facts require the person's own
words or documents and profile evidence reconciliation before they can appear on a resume.
Keeping a suggestion in Assurance alone does not supply that evidence.
Read the independent review's `verdict` and `issues`, not just the run's completed state.
Only `pass` with no unresolved issues passes that check; `review`, `blocked` or a legacy
report without a verdict needs attention before the resume is offered as ready.

The tailor may reword up to two bullets of a registered project in the posting's words; the app
keeps a rewording only when it states the same numbers and names no tool, employer or date that
the evidence does not hold, and the resume check tests it again (the registry itself never
changes). `left_out` says when a rewording was refused and the registered line kept.
`career ws docx` writes a Word copy of the current checked PDF revision (same content; it refuses
when the PDF is out of date). `career ws cover-letter` drafts a letter from registered evidence
and verified company facts with the AI, checks every number, name and skill in it, has a second
AI that did not write it flag any sentence about the person the evidence does not state, and
otherwise builds one from the person's registered sentences (`method` says which; `note` says why
an AI draft was set aside); it saves `.md` and `.docx` and never mentions visas or permits. The
Daily Search and overnight hunt can write one per job when the person switches on the "Cover
letter" helper (off by default). Give the person the paths and ask them to read the letter
before using it.

## AI-only mode

1. **Map the job.** List the posting's must-haves and nice-to-haves. For each, name the line of
   `me/profile.md` that proves it, or mark it as a gap. Never cover a gap by writing it in.
2. **Choose what to show.** Keep the person's real roles in date order. Inside each role, order the
   true bullets by relevance to this job. Pick the 2 or 3 projects that best match. Drop what does
   not help, but never drop an employer or a date in a way that hides a gap they did not ask to hide.
3. **Write.** Follow `references/resume-format.md` for the country. Use the posting's words only
   where they describe something the person really did (same skill, same tool). Keep every title,
   employer, date, degree and number exactly as in the evidence. When a number would help but the
   evidence has none, leave it out and ask the person for it afterwards; never estimate one.
4. **Make the files** in the job's folder (`my-jobs/<date>/NN-company-role/`), named
   `<First>-<Last>-CV`:
   - a `.docx` (easy for them to edit) and a `.pdf` (to upload). Use your document tools: for
     example the docx and pdf skills in Claude, Python with python-docx or reportlab, or your
     app's file export. When you can make only one, make the DOCX and say so. When you cannot
     create files at all, give the resume as text in your reply.
5. **Audit it** with `references/recruiter-audit.md` (three passes: score and red flags,
   rewrite, final scan as software and as a hiring manager) and apply the fixes.
6. **Check the result** with `references/quality-review.md`: open the PDF, count the pages, make
   sure the text can be selected and read in order, compare every fact with the evidence, and
   remove hidden characters and tool metadata. Fix and re-check until it passes.
7. **Save the notes** in the job's `job.md`: the requirement-to-evidence map, the audit scores
   before and after, and the gaps. Put any gap worth learning in a short "To learn" list there,
   never on the resume.

## Report

Per job: the file paths, pages, the fit and the honest gaps, anything you need from them (for
example a missing number), and a reminder that nothing was submitted. They apply themselves
through the posting's link.
