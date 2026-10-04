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

1. This copy searches the Republic of Ireland only (`career-dashboard/backend/countries/markets.yml`);
   say so if they mention another country.
2. Their permission to work in Ireland (for example Stamp 1G and the date it ends), and whether
   they will need an employer to get them an employment permit later.
   Offer the choices from `about-me.example.md`. "Not sure" is a valid answer: say that jobs which
   depend on it will wait. Never guess this answer from a nationality, a school or an address.
   For Stamp 1G, record the permission type and ask them to confirm the exact expiry day
   from their permission record. A date such as `DEC2027` is only a month: the app can propose
   `2027-12-31`, but that is not saved as the expiry unless they confirm the actual day.
   Keep their original wording separately. Never infer permission from their degree or location.
3. The 2 to 4 job titles they want. Suggest titles that their documents support; they confirm.
4. Level, places or remote work, and anything to avoid (optional).
   Ask whether they want graduate and entry-level searches with up to 3 years required;
   use those limits only if they confirm. Ask for the actual award date, NFQ level, whether
   the awarding institution is Irish, and whether the degree is relevant to their target
   occupations. Leave each unknown when they are unsure; expected graduation is not an award.
5. The morning list: how many jobs (5), ready by when (07:30), and whether to email the list to
   their own address (no, unless they say yes).

Write each answer into `me/about-me.md` in their words.

## 3. Build the profile

**App mode.** Run

```text
career setup --name "<Full name>" --market ie --work-auth '<json>'
```

The JSON is keyed by market: `{"ie": {"status": "...", "citizenship": "...",
"needs_sponsorship_later": "..."}}`. Map their answers:

| They said | status | citizenship |
|---|---|---|
| Irish or EU/EEA citizen | `authorized` | `citizen` |
| Stamp 4 (or another permission to live and work without a permit) | `authorized` | `noncitizen` |
| a current permission that lets them work, for example Stamp 1G | `authorized` | `noncitizen` |
| an employer must get them an employment permit | `needs_sponsorship` | `noncitizen` |

Stamp 4 must not be recorded as citizenship. Preserve the person's exact permission
wording in their source notes and confirm their future-sponsorship answer separately.

The Build settings and Profile page have a **Permit facts** form. Raw dates remain verbatim;
only person-confirmed exact ISO dates enter threshold and timeline checks. The extractor never
confirms normalized facts. For explicit confirmed chat answers, the CLI also accepts
`--permission-type stamp_1g --valid-until YYYY-MM-DD --award-date YYYY-MM-DD --nfq-level 9
--irish-institution yes --relevant-degree yes`. Pass only answers the person actually gave.
`--graduate-search` applies their confirmed graduate-search preference. `--seniority`,
`--max-years-required`, `--salary-floor-eur` and `--salary-policy` accept explicit preferences.
The default floor follows the dated permit rules and changes when a confirmed graduate
window ends; an explicit personal floor is retained. With `--salary-policy
confirmed_or_estimated` (the default) a job is prepared when its advertised pay reaches the
floor or, when the posting states no pay, a labelled market estimate or researched comparable
pay does (the person then confirms the base salary with the recruiter); `confirmed_only`
prepares advertised pay only.

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
