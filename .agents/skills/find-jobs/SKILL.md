---
name: find-jobs
description: >-
  Find a requested number of real, open jobs that fit the person, check each one (location,
  permission to work, never re-apply, legitimacy, requirements), and make a tailored resume for
  each. Use for "give me 5 jobs with resumes", "find jobs", "more jobs", "better jobs", or any
  request for a number of roles.
---

# Find jobs

Read `AGENTS.md` first. The person must be set up (`career-setup`): App mode needs a built
profile, AI-only mode needs `me/profile.md`. Their permission to work must be known for each
country searched; ask when it is not. Default count: the "Jobs per morning" in their profile, else 5.

## App mode

The app's overnight hunt does the whole job: it reads employer career feeds and job boards
without AI, runs focused AI searches per role, applies every check, keeps searching until it has
the number asked for at the person's fit bar, then prepares each job (company research, tailored
resume, study plan, PDF).

1. Run it for the number asked (Windows, then macOS or Linux):

   ```text
   daily-job-search\morning-jobs.cmd --jobs 5 --profile <id>
   bash daily-job-search/morning-jobs.command --jobs 5 --profile <id>
   ```

   It starts the app when it is not running and searches for up to 1.5 hours (`--hours 3` for
   longer). If your app cannot wait that long, add `--background`, tell the person it is running,
   and later rewrite the list with `--list-only`.
2. Read the index `daily-job-search/MORNING-JOBS.md` and the profile list it points to.
3. Reply with the list format below, using the list's own values. Items under "Waiting for the AI
   requirement check" are not checked yet; say so and do not count them.

To add one posting the person found themselves: save it as JSON (`company`, `title`, `location`,
`url`, full `description`) and run `career add --file job.json`, then `career ws fit --job-id <id>`
and `career prepare <id>`, or use `tailor-resume`.

## AI-only mode

Work from `me/profile.md`, `me/about-me.md` and `my-jobs/tracker.csv` (create it with the header
from `my-jobs/README.md` when missing).

1. **Plan the search.** For each target title and country, use the sources in
   `ireland-job-sources` or `us-job-sources`. Use every tool you have: web search and page
   fetching, job-board connectors (for example Indeed, Dice or ZipRecruiter when connected) and
   employer career pages. Prefer postings from the last 30 days.
2. **Open every posting** from its own page (the employer's site or its applicant-tracking page)
   and read the full text. A search snippet is not enough. If a board hides the posting behind a
   login, look for the same role on the employer's careers page; if you cannot open it anywhere,
   drop it.
3. **Check each one; drop it if any check fails:**
   - *Location*: in the country searched, or remote for that country (Northern Ireland is the UK,
     not Ireland). Respect their places and remote wishes.
   - *Permission to work*: find any sentence about visas, permits, sponsorship, citizenship or
     clearance and quote it exactly. Drop postings that refuse sponsorship when they need it, or
     that require a citizenship, permit type or clearance they do not have. Silence is fine.
   - *Never re-apply*: drop it when the same company and a similar title, or the same link, is
     already in `tracker.csv` with any status other than `suggested`. A `suggested` one may come
     back only under "Still to apply", never as new.
   - *Legitimate*: a named employer or agency, a real apply route, no fees, no chat-app-only
     contact, not expired or closed.
   - *Fit*: compare each must-have in the posting with `me/profile.md`. Score 0-100: about 60%
     must-haves backed by their evidence, 20% nice-to-haves, 20% level and years. Keep jobs at or
     above their fit bar (default 70). Write two short reasons it fits and the honest gaps.
4. **Keep going** until you have the number asked for, or the honest search is exhausted. Dropped
   jobs do not count. Never pad the list; report fewer when that is the truth.
5. **Make the resumes** with `tailor-resume` (AI-only part) for each job kept.
6. **Save everything** in `my-jobs/<today>/`: one folder per job `NN-company-role/` with `job.md`
   (link, date read, full posting text, the permit sentence, fit score, reasons, gaps) and the
   resume; `JOBS.md` with the list below; and one `suggested` line per job in `tracker.csv`.

## The list you reply with (both modes)

```text
5 new jobs for <first name>, <date>

1. <Title>, <Company> (<Location>). Fit <score>
   Why: <one line>. Gap: <one line or "none">
   Permit: <quoted sentence, or "the posting does not mention it">
   Apply: <link>
   Resume: <path to the PDF>
...
Still to apply: <n> (best three: ...)
Nothing was submitted and no one was contacted.
```

Start with anything they must do (sign in, answer a question). Then stop; never apply.
