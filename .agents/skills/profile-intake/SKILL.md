---
name: profile-intake
description: >-
  Record something new or corrected about the person so every future resume can use it: a
  finished course, a new project, a certification, a job change, an answer to an open question,
  an updated CV. Extractive only: every recorded line traces to their own words or documents.
  Use for "I built …", "I finished …", "add this to my profile", "that date is wrong".
---

# Profile intake

Read `AGENTS.md` first.

## The rule

Record only what the person said or supplied, in their words. Missing details become questions,
never plausible guesses. Never raise a skill level they did not claim, turn a course into work
experience, or add a years-of-experience total they did not state.

## App mode

Read `career ws summary --profile <id>` and `career ws profile --profile <id>` before making
changes so dashboard edits since the last conversation are included.

The profile's facts live in `career-dashboard/profiles/<id>/data/`: `config/profile.yml`
(identity, targets, rules) and `context/evidence.yml` (the evidence registry every resume line
must cite), built from the source documents. Do not edit those files by hand.

- **A new or updated document** (an updated CV, a certificate as text): put it in `me/` and run
  `career setup --profile <id>`. It adds changed files as new source versions and rebuilds; job
  history is kept, and a failed build leaves the previous profile active.
- **A fact in words** ("I finished the AWS Cloud Practitioner course in September"): the
  dashboard's Profile page (Profile request) or the Assistant turn it into a proposal the person
  reviews. You may also save it as a note in `me/` (for example `me/notes-2026-10.md`, their exact
  words and the date) and rebuild as above.
- Tell them that open resume drafts pick up the change after **Sync profile** in Resume Studio.

## AI-only mode

1. Add the fact to the right section of `me/profile.md` in their words, ending with its source:
   `[chat <date>]` or the file name.
2. When it answers an open question, move the question out of "Open questions" with the date.
3. When it contradicts something already there, keep both, and ask which is right before any
   resume uses it.
4. Tell them in one line which section changed. Resumes made from now on use it; offer to remake
   a resume that is still waiting to be sent.
