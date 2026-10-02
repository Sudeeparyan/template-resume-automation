---
name: career-setup
description: >-
  First-time setup of this career workspace for one person: read their resume and notes in me/,
  ask only what is missing (country, permission to work, target roles, morning preferences) and
  build their profile. Use for "set me up", "here is my resume", "start", or the first message in
  a new copy of this folder.
---

# Career setup

Goal: after one short conversation the person has a profile you can trust and knows what to ask
next. Read `AGENTS.md` first for the two modes and the rules.

Run `career doctor` before collecting facts. In App mode, zero profiles is the normal first
run. If this person's profile already exists, use `--profile <id>` to resume or rebuild it;
do not create another profile after a failed build. Resolve multiple profiles before reading
sources. The shared `me/` inbox should contain only the selected person's files.

## 1. Find their documents

- Look in `me/` for a resume or CV (PDF, DOCX, TXT or MD) and for `me/about-me.md`.
- If the person supplied an attachment or selected a file in a connected Drive, save that
  document in `me/` first using the available file tool. A chat attachment or cloud connection
  alone does not add a source to the app. Ask for a local copy when no download tool exists.
- Nothing there: ask them to put their resume in the `me` folder of this workspace, or to paste
  its text in the chat (save pasted text as `me/resume.md`). Wait for it.
- `me/about-me.md` missing: copy `me/about-me.example.md` to `me/about-me.md`.

## 2. Read everything, then ask once

Read every document in `me/`. Fill `me/about-me.md` with what the documents already state (name,
email, phone, city, links). Then ask, in a single message, only what is still empty or unclear:

1. The country to search: Ireland, the United States or both (Ireland when they have no
   preference).
2. Their permission to work in each chosen country, and whether they will need sponsorship later.
   Offer the choices from `about-me.example.md`. "Not sure" is a valid answer: say that jobs which
   depend on it will wait. Never guess this answer from a nationality, a school or an address.
3. The 2 to 4 job titles they want. Suggest titles that their documents support; they confirm.
4. Level, places or remote work, and anything to avoid (optional).
5. The morning list: how many jobs (5), ready by when (07:30), and whether to email the list to
   their own address (no, unless they say yes).

Write each answer into `me/about-me.md` in their words.

## 3. Build the profile

**App mode.** Run

```text
career setup --name "<Full name>" --market <ie|us|both> --work-auth '<json>'
```

The JSON is keyed by market: `{"ie": {"status": "...", "citizenship": "...",
"needs_sponsorship_later": "..."}}`. Map their answers:

| They said | status | citizenship |
|---|---|---|
| citizen (Ireland: Irish or EU/EEA; US: citizen) | `authorized` | `citizen` |
| US green-card holder | `authorized` | `noncitizen` |
| a current permission or visa that lets them work | `authorized` | `noncitizen` |
| an employer must get them a permit or sponsor them | `needs_sponsorship` | `noncitizen` |

A green card must not be recorded as citizenship. Preserve the person's exact permission
wording in their source notes and confirm their future-sponsorship answer separately.

`needs_sponsorship_later` is `yes` or `no`. Leave out any field they are unsure of; it stays
unknown and blocks job searches until answered. On Windows, save the JSON in a private file
such as `me/work-authorization.json` and pass `--work-auth me/work-authorization.json` to avoid
shell quoting problems. The build reads supported documents in `me/` (except the guides and
`profile.md`), takes a few minutes and prints JSON. When it reports missing AI sign-in or
Tectonic, explain the specific fix from `README.md`, retain the returned profile ID, and resume
with `--profile <id>` after it is fixed. Do not claim setup is complete or start a parallel
AI-only profile. Success needs `build.status: completed` and `state: ready`.
After a good build, compare `target_roles.primary`
in `career-dashboard/profiles/<id>/data/config/profile.yml` with the titles they asked for; the
person changes differences on the dashboard's Profile page (you never edit that file by hand).
Run `career status --profile <id>` to verify the saved candidate and show the ID in the setup
reply. A provider executable found by doctor is not proof of sign-in; AI readiness is checked
by the build or `career ws ai-status --profile <id>`.

**AI-only mode.** Write `me/profile.md`. Every line ends with its source in square brackets:
`[resume.pdf]`, `[about-me]` or `[chat 2026-10-05]`. Copy titles, dates and numbers exactly as
written; do not round, improve or merge them. Put conflicts and missing facts under
"Open questions".

```text
# Profile: <Full name>
Updated: <date>
## Contact          name, email, phone, city, links
## Search           countries, job titles, level, places, avoid, fit bar, jobs per morning,
                    ready by, email the list (yes/no), resume length
## Permission to work   per country, exactly as they said; "unknown" when not given
## Experience       title, employer, dates, place, then the bullets as written
## Education
## Projects
## Skills           only skills the documents or the person state
## Certifications and awards
## Open questions
```

## 4. Finish

Reply in a few lines: what is set up, anything still unknown, and three things they can say:

- "Give me 5 jobs with resumes"
- "Set up my morning jobs" (a checked list every morning)
- "I applied to <company>" (so it is never suggested again)
