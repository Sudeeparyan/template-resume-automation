---
name: morning-jobs
description: >-
  The person's morning list: new checked jobs with apply links and tailored resumes, jobs still to
  apply for, and anything they must do. Use for "today's jobs", "my morning list", a scheduled
  morning run, and to set up "send me jobs every morning" in Claude, ChatGPT (Codex) or Kimi.
---

# Morning jobs

Read `AGENTS.md` first.

## When a scheduled task (or the person) asks for this morning's jobs

**App mode.** The app searches overnight when Morning jobs is switched on (Settings → This
profile) or when the AI app's evening task starts it.

1. Rewrite the list from what is saved (under a minute):
   `daily-job-search\morning-jobs.cmd --list-only` (Windows) or
   `bash daily-job-search/morning-jobs.command --list-only`.
2. Read `daily-job-search/MORNING-JOBS.md` and the profile list it points to.
3. When the list has fewer new jobs than they want and there is time, run `find-jobs` for the
   difference instead of padding the list.

**AI-only mode.** Run `find-jobs` for the "Jobs per morning" number in `me/profile.md` (else 5),
at their fit bar, skipping everything already in `my-jobs/tracker.csv`. Then read the tracker for
`suggested` jobs from the last 21 days: those are "Still to apply".

**Reply** (both modes) in this order, short and plain:

```text
Good morning <first name>. <n> new jobs for <date>.

Needs you: <only when they must act, for example sign in or answer a question>

1. <Title>, <Company> (<Location>). Fit <score>
   Why: <one line>. Gap: <one line or "none">
   Permit: <quoted sentence, or "not mentioned">
   Apply: <link>
   Resume: <path to the PDF>
...
Still to apply: <n>. Best three: <company, role>; ...
Nothing was submitted and no one was contacted.
```

**Email (only when asked).** When `me/about-me.md` says "Email me the morning list: yes" and an
email tool is connected (for example Gmail), send this same text to the person's own address from
`me/about-me.md`, subject "Your jobs for <date>". If you can only draft, leave a draft. Never
email anyone else and never attach anything but their own resume PDFs.

## When they ask to get this every morning

1. Make sure setup is done (`career-setup`) and `me/about-me.md` has the number of jobs, the time
   and the email choice.
2. Give them the task for their app, with the prompt below. Offer to create it when your app can
   create scheduled tasks itself; otherwise walk them through it in three steps.

   - **Claude desktop app (Cowork):** in this project, open **Scheduled**, **New task**, paste the
     prompt, choose **Daily** at their time, and pick this folder.
   - **ChatGPT (Codex app):** **Automations**, **New automation**, choose this project, paste the
     prompt, set a daily schedule.
   - **Kimi Code:** ask Kimi "every day at 07:30, run this:" followed by the prompt.
   - **Windows with the dashboard installed:** Settings → This profile → **Morning jobs** also
     searches overnight by itself; the AI app's task then only has to bring the list.

   The computer must be on and the AI app open at that time for tasks that use this folder.

3. The prompt to schedule (the same in every app):

   ```text
   Morning jobs. In this folder, follow AGENTS.md and the morning-jobs skill: bring me today's
   jobs with apply links and tailored resumes. Do not apply, contact anyone or change my profile.
   ```

4. Optional, App mode without the Windows task (for example on macOS): add an evening task at
   23:00 with this prompt, so the search runs overnight:

   ```text
   In this folder, run  daily-job-search/morning-jobs.command --background  (Windows:
   daily-job-search\morning-jobs.cmd --background) and reply only with the line it prints.
   ```

Never start an application, send outreach or edit the profile from this skill. Details of the
app's runner: `daily-job-search/AUTOPILOT.md`.
